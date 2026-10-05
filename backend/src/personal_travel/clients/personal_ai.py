from __future__ import annotations

import asyncio
import ipaddress
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, cast
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from personal_travel.auth.contracts import PersonalAIAuthContext
from personal_travel.auth.google_oidc import cloud_run_service_id_token
from personal_travel.clients.http import InvalidUpstreamResponse, read_json, sse_lines
from personal_travel.config import get_settings
from personal_travel.domain.types import ResearchState
from personal_travel.domain.upstream_extractions import UpstreamBookingExtractionResult
from personal_travel.domain.upstream_proposals import UpstreamProposalResult
from personal_travel.domain.urls import validate_http_url

TerminalResearchState = Literal["completed", "insufficient", "failed", "expired"]

MAX_RESEARCH_EVENT_BYTES = 16 * 1024
MAX_RESEARCH_STREAM_BYTES = 128 * 1024
MAX_RESEARCH_EVENTS = 128
MAX_RESEARCH_RESPONSE_BYTES = 1_000_000
MAX_PROPOSAL_RESPONSE_BYTES = 128 * 1024
MAX_EXTRACTION_RESPONSE_BYTES = 64 * 1024


class PersonalAIError(RuntimeError):
    pass


@dataclass(frozen=True)
class PersonalAIHealth:
    status: str
    service: str | None = None


class _ResearchCitation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    number: int = Field(ge=1)
    evidence_id: UUID
    source_observation_id: UUID
    url: str = Field(min_length=1, max_length=2048)
    title: str | None = Field(default=None, max_length=500)
    observed_at: datetime
    expires_at: datetime

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        validate_http_url(value)
        return value

    @field_validator("observed_at", "expires_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("research timestamps must include a timezone")
        return value


class _ResearchSession(BaseModel):
    model_config = ConfigDict(extra="ignore")

    schema_version: Literal["research-v1"]
    id: UUID
    state: ResearchState
    expires_at: datetime
    failure_code: str | None = Field(default=None, max_length=80, pattern=r"^[a-z0-9_]+$")
    answer: str | None = Field(default=None, max_length=20_000)
    citations: list[_ResearchCitation] = Field(default_factory=list, max_length=144)

    @field_validator("expires_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("research timestamps must include a timezone")
        return value


class PersonalAIResearchResult(BaseModel):
    session_id: UUID
    state: ResearchState
    expires_at: datetime
    failure_code: str | None = None
    answer: str | None = None
    citations: list[_ResearchCitation] = Field(default_factory=list)


class PersonalAIProposalError(RuntimeError):
    """Safe proposal-client failure; its text never contains upstream data."""


class PersonalAIProposalUnknown(PersonalAIProposalError):
    """The POST outcome is ambiguous and can only be reconciled by its stable key."""


class PersonalAIExtractionError(RuntimeError):
    """Safe booking extraction client failure."""


class PersonalAIExtractionUnknown(PersonalAIExtractionError):
    """The POST outcome is ambiguous; recovery must use the existing key."""


class PersonalAIExtractionRejected(PersonalAIExtractionError):
    """The upstream request was conclusively rejected before a result was created."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__("booking extraction request was rejected")


class PersonalAIClient:
    """Typed HTTP boundary to personal-ai-system."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        auth_context: PersonalAIAuthContext | None = None,
        service_token_fetcher: Callable[[str, str], str] | None = None,
    ) -> None:
        settings = get_settings()
        self._base_url = str(base_url or settings.personal_ai_base_url).rstrip("/")
        self._timeout = (
            timeout_seconds if timeout_seconds is not None else settings.personal_ai_timeout_seconds
        )
        self._transport = transport
        self._auth_context = auth_context
        if auth_context is not None:
            destination = urlsplit(self._base_url)
            configured = urlsplit(str(settings.personal_ai_base_url))
            if (
                destination.scheme != "https"
                or not destination.hostname
                or destination.username is not None
                or destination.password is not None
                or destination.query
                or destination.fragment
                or (destination.scheme, destination.netloc, destination.path.rstrip("/"))
                != (configured.scheme, configured.netloc, configured.path.rstrip("/"))
            ):
                raise ValueError(
                    "Authenticated AI requests require the configured HTTPS service URL."
                )
        self._service_token_fetcher = service_token_fetcher or cloud_run_service_id_token

    async def _outbound_headers(self) -> dict[str, str]:
        if self._auth_context is None:
            return {}
        try:
            service_token = await asyncio.to_thread(
                self._service_token_fetcher,
                self._auth_context.service_audience,
                self._auth_context.service_account,
            )
        except Exception:
            raise PersonalAIError("personal-ai-system service identity is unavailable") from None
        if not service_token or len(service_token) > 8192:
            raise PersonalAIError("personal-ai-system service identity is unavailable")
        return {
            "Authorization": f"Bearer {service_token}",
            "X-User-ID-Token": self._auth_context.user_id_token,
        }

    async def health(self) -> PersonalAIHealth:
        try:
            async with (
                asyncio.timeout(self._timeout),
                httpx.AsyncClient(timeout=self._timeout) as client,
            ):
                async with client.stream("GET", f"{self._base_url}/health") as response:
                    response.raise_for_status()
                    try:
                        payload = await read_json(response, max_bytes=4096)
                    except InvalidUpstreamResponse as exc:
                        raise PersonalAIError(
                            "personal-ai-system returned invalid health JSON"
                        ) from exc
        except (httpx.HTTPError, TimeoutError) as exc:
            raise PersonalAIError("personal-ai-system is unavailable") from exc

        if not isinstance(payload, dict):
            raise PersonalAIError("personal-ai-system returned an invalid health response")

        status = payload.get("status")
        service = payload.get("service")
        if not isinstance(status, str) or (service is not None and not isinstance(service, str)):
            raise PersonalAIError("personal-ai-system returned an invalid health response")

        return PersonalAIHealth(status=status, service=service)

    async def research(
        self,
        *,
        question: str,
        freshness: Literal["general", "current"],
        idempotency_key: UUID,
    ) -> PersonalAIResearchResult:
        # HTTPX timeouts apply per I/O operation. The full create/run/detail
        # sequence also needs a wall-clock deadline, including trickling SSE.
        try:
            async with asyncio.timeout(self._timeout):
                headers = await self._outbound_headers()
                return await self._research(
                    question=question,
                    freshness=freshness,
                    idempotency_key=idempotency_key,
                    headers=headers,
                )
        except TimeoutError:
            raise PersonalAIError("personal-ai-system research exceeded its deadline") from None

    async def create_itinerary_proposal(
        self, *, payload: dict[str, object], idempotency_key: UUID
    ) -> UpstreamProposalResult:
        """POST once, then reconcile an ambiguous outcome by the same stable key."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._timeout
        try:
            async with asyncio.timeout_at(deadline):
                headers = await self._outbound_headers()
                async with httpx.AsyncClient(
                    timeout=self._timeout, transport=self._transport
                ) as client:
                    try:
                        result = await self._proposal_request(
                            client,
                            "POST",
                            "/v1/travel/itinerary-proposals",
                            payload,
                            deadline=deadline,
                            headers=headers,
                        )
                    except (
                        httpx.HTTPError,
                        TimeoutError,
                        InvalidUpstreamResponse,
                        ValidationError,
                    ):
                        result = None
                    if result is not None and result.state != "running":
                        return result
                    reconciled = await self._proposal_request(
                        client,
                        "GET",
                        f"/v1/travel/itinerary-proposals/by-key/{idempotency_key}",
                        None,
                        deadline=deadline,
                        missing_is_none=True,
                        headers=headers,
                    )
                    if reconciled is not None:
                        return reconciled
                    raise PersonalAIProposalUnknown("proposal outcome is unknown")
        except PersonalAIProposalUnknown:
            raise
        except PersonalAIError:
            raise PersonalAIProposalError("proposal service is unavailable") from None
        except (
            TimeoutError,
            httpx.HTTPError,
            InvalidUpstreamResponse,
            ValidationError,
            ValueError,
        ):
            raise PersonalAIProposalUnknown("proposal outcome is unknown") from None

    async def get_itinerary_proposal_by_key(
        self, idempotency_key: UUID
    ) -> UpstreamProposalResult | None:
        return await self._get_itinerary_proposal(
            f"/v1/travel/itinerary-proposals/by-key/{idempotency_key}", missing_is_none=True
        )

    async def get_itinerary_proposal(self, proposal_id: UUID) -> UpstreamProposalResult:
        result = await self._get_itinerary_proposal(
            f"/v1/travel/itinerary-proposals/{proposal_id}", missing_is_none=False
        )
        assert result is not None
        return result

    async def create_booking_extraction(
        self,
        *,
        payload: dict[str, object],
        idempotency_key: UUID,
        source_sha256: str,
    ) -> UpstreamBookingExtractionResult:
        """POST exactly once, then recover only through the same idempotency key."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._timeout
        rejection_status: int | None = None
        try:
            async with asyncio.timeout_at(deadline):
                headers = await self._outbound_headers()
                async with httpx.AsyncClient(
                    timeout=self._timeout, transport=self._transport
                ) as client:
                    try:
                        result = await self._extraction_request(
                            client,
                            "POST",
                            "/v1/travel/booking-extractions",
                            payload,
                            deadline=deadline,
                            headers=headers,
                        )
                    except httpx.HTTPStatusError as error:
                        if error.response.status_code in {400, 401, 403, 404, 409, 413, 422}:
                            rejection_status = error.response.status_code
                        result = None
                    except (
                        httpx.HTTPError,
                        TimeoutError,
                        InvalidUpstreamResponse,
                        ValidationError,
                    ):
                        result = None
                    if result is not None and result.state not in {"running"}:
                        return self._check_extraction_identity(
                            result, idempotency_key, source_sha256
                        )
                    reconciled = await self._extraction_request(
                        client,
                        "GET",
                        f"/v1/travel/booking-extractions/by-key/{idempotency_key}",
                        None,
                        deadline=deadline,
                        missing_is_none=True,
                        headers=headers,
                    )
                    if reconciled is not None:
                        return self._check_extraction_identity(
                            reconciled, idempotency_key, source_sha256
                        )
                    if rejection_status is not None:
                        raise PersonalAIExtractionRejected(rejection_status)
                    raise PersonalAIExtractionUnknown("booking extraction outcome is unknown")
        except (PersonalAIExtractionUnknown, PersonalAIExtractionRejected):
            raise
        except (PersonalAIError, TimeoutError, httpx.HTTPError, ValueError, ValidationError):
            raise PersonalAIExtractionUnknown("booking extraction outcome is unknown") from None

    async def get_booking_extraction_by_key(
        self, idempotency_key: UUID, source_sha256: str
    ) -> UpstreamBookingExtractionResult | None:
        result = await self._get_booking_extraction(
            f"/v1/travel/booking-extractions/by-key/{idempotency_key}", missing_is_none=True
        )
        if result is not None:
            return self._check_extraction_identity(result, idempotency_key, source_sha256)
        return None

    async def get_booking_extraction(
        self, extraction_id: UUID, idempotency_key: UUID, source_sha256: str
    ) -> UpstreamBookingExtractionResult:
        result = await self._get_booking_extraction(
            f"/v1/travel/booking-extractions/{extraction_id}", missing_is_none=False
        )
        assert result is not None
        return self._check_extraction_identity(result, idempotency_key, source_sha256)

    async def delete_booking_extraction(
        self, extraction_id: UUID, idempotency_key: UUID, source_sha256: str
    ) -> UpstreamBookingExtractionResult:
        return await self._booking_extraction_call(
            "DELETE",
            f"/v1/travel/booking-extractions/{extraction_id}",
            None,
            idempotency_key,
            source_sha256,
        )

    async def delete_booking_extraction_by_key(
        self, idempotency_key: UUID, source_sha256: str
    ) -> UpstreamBookingExtractionResult:
        """Tombstone the owner/key even if its POST has not reached storage yet."""
        return await self._booking_extraction_call(
            "DELETE",
            f"/v1/travel/booking-extractions/by-key/{idempotency_key}",
            {"source_sha256": source_sha256},
            idempotency_key,
            source_sha256,
        )

    async def _get_booking_extraction(
        self, path: str, *, missing_is_none: bool
    ) -> UpstreamBookingExtractionResult | None:
        try:
            async with asyncio.timeout(self._timeout):
                headers = await self._outbound_headers()
                async with httpx.AsyncClient(
                    timeout=self._timeout, transport=self._transport
                ) as client:
                    return await self._extraction_request(
                        client,
                        "GET",
                        path,
                        None,
                        deadline=asyncio.get_running_loop().time() + self._timeout,
                        missing_is_none=missing_is_none,
                        headers=headers,
                    )
        except (PersonalAIError, TimeoutError, httpx.HTTPError, ValueError, ValidationError):
            raise PersonalAIExtractionError("booking extraction service is unavailable") from None

    async def _booking_extraction_call(
        self,
        method: Literal["GET", "POST", "DELETE"],
        path: str,
        payload: dict[str, object] | None,
        idempotency_key: UUID,
        source_sha256: str,
    ) -> UpstreamBookingExtractionResult:
        try:
            async with asyncio.timeout(self._timeout):
                headers = await self._outbound_headers()
                async with httpx.AsyncClient(
                    timeout=self._timeout, transport=self._transport
                ) as client:
                    result = await self._extraction_request(
                        client,
                        method,
                        path,
                        payload,
                        deadline=asyncio.get_running_loop().time() + self._timeout,
                        headers=headers,
                    )
                    assert result is not None
                    return self._check_extraction_identity(result, idempotency_key, source_sha256)
        except (PersonalAIError, TimeoutError, httpx.HTTPError, ValueError, ValidationError):
            raise PersonalAIExtractionError("booking extraction service is unavailable") from None

    async def _extraction_request(
        self,
        client: httpx.AsyncClient,
        method: Literal["GET", "POST", "DELETE"],
        path: str,
        payload: dict[str, object] | None,
        *,
        deadline: float,
        missing_is_none: bool = False,
        headers: dict[str, str] | None = None,
    ) -> UpstreamBookingExtractionResult | None:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise TimeoutError
        async with asyncio.timeout_at(deadline):
            async with client.stream(
                method,
                f"{self._base_url}{path}",
                json=payload,
                headers=headers,
                timeout=httpx.Timeout(remaining),
            ) as response:
                if missing_is_none and response.status_code == 404:
                    return None
                response.raise_for_status()
                raw = await read_json(response, max_bytes=MAX_EXTRACTION_RESPONSE_BYTES)
                return UpstreamBookingExtractionResult.from_payload(raw)

    @staticmethod
    def _check_extraction_identity(
        result: UpstreamBookingExtractionResult, key: UUID, source_sha256: str
    ) -> UpstreamBookingExtractionResult:
        if result.idempotency_key != key or result.source_sha256 != source_sha256:
            raise InvalidUpstreamResponse("booking extraction identity mismatch")
        return result

    async def _get_itinerary_proposal(
        self, path: str, *, missing_is_none: bool
    ) -> UpstreamProposalResult | None:
        try:
            async with asyncio.timeout(self._timeout):
                headers = await self._outbound_headers()
                async with httpx.AsyncClient(
                    timeout=self._timeout, transport=self._transport
                ) as client:
                    return await self._proposal_request(
                        client,
                        "GET",
                        path,
                        None,
                        deadline=asyncio.get_running_loop().time() + self._timeout,
                        missing_is_none=missing_is_none,
                        headers=headers,
                    )
        except (
            PersonalAIError,
            TimeoutError,
            httpx.HTTPError,
            InvalidUpstreamResponse,
            ValidationError,
            ValueError,
        ):
            raise PersonalAIProposalError("proposal service is unavailable") from None

    async def _proposal_request(
        self,
        client: httpx.AsyncClient,
        method: Literal["GET", "POST"],
        path: str,
        payload: dict[str, object] | None,
        *,
        deadline: float,
        missing_is_none: bool = False,
        headers: dict[str, str] | None = None,
    ) -> UpstreamProposalResult | None:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise TimeoutError
        async with asyncio.timeout_at(deadline):
            async with client.stream(
                method,
                f"{self._base_url}{path}",
                json=payload,
                headers=headers,
                timeout=httpx.Timeout(remaining),
            ) as response:
                if missing_is_none and response.status_code == 404:
                    return None
                response.raise_for_status()
                try:
                    raw = await read_json(response, max_bytes=MAX_PROPOSAL_RESPONSE_BYTES)
                    result = UpstreamProposalResult.model_validate_json(
                        json.dumps(raw, ensure_ascii=False, separators=(",", ":"))
                    )
                except (
                    InvalidUpstreamResponse,
                    ValidationError,
                    ValueError,
                    TypeError,
                    RecursionError,
                ):
                    raise InvalidUpstreamResponse("invalid proposal response") from None
        for citation in result.citations:
            _validate_public_citation_url(citation.url)
        return result

    async def _research(
        self,
        *,
        question: str,
        freshness: Literal["general", "current"],
        idempotency_key: UUID,
        headers: dict[str, str],
    ) -> PersonalAIResearchResult:
        """Create or replay one research session, run pending work, and read its result."""

        timeout = httpx.Timeout(self._timeout)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream(
                    "POST",
                    f"{self._base_url}/v1/research",
                    headers=headers,
                    json={
                        "schema_version": "research-v1",
                        "question": question,
                        "freshness": freshness,
                        "idempotency_key": str(idempotency_key),
                    },
                ) as create:
                    create.raise_for_status()
                    created = _ResearchSession.model_validate(await _bounded_json(create))

                terminal_event: TerminalResearchState | None = None
                if created.state == "pending":
                    try:
                        async with client.stream(
                            "POST",
                            f"{self._base_url}/v1/research/{created.id}/run",
                            headers=headers,
                        ) as response:
                            response.raise_for_status()
                            terminal_event = await _consume_research_events(
                                response, expected_session_id=created.id
                            )
                    except httpx.HTTPStatusError as exc:
                        # A concurrent request may have claimed the pending session. Only
                        # recover a run conflict when the durable session confirms it.
                        if exc.response.status_code != 409:
                            raise
                    except httpx.HTTPError:
                        # A broken stream can still have a durable terminal result. The
                        # detail read below is authoritative; nonterminal state fails closed.
                        pass

                async with client.stream(
                    "GET",
                    f"{self._base_url}/v1/research/{created.id}",
                    headers=headers,
                ) as detail_response:
                    detail_response.raise_for_status()
                    detail = _ResearchSession.model_validate(await _bounded_json(detail_response))
        except PersonalAIError:
            raise
        except InvalidUpstreamResponse as exc:
            raise PersonalAIError(f"personal-ai-system returned {exc}") from None
        except (httpx.HTTPError, ValidationError, ValueError, TypeError, RecursionError) as exc:
            raise PersonalAIError(
                "personal-ai-system returned an unavailable or invalid response"
            ) from exc

        if detail.id != created.id:
            raise PersonalAIError("personal-ai-system returned a mismatched research session")
        now = datetime.now(UTC)
        expired_since_read = detail.state == "expired" and detail.expires_at <= now
        if terminal_event is not None and detail.state != terminal_event and not expired_since_read:
            raise PersonalAIError("personal-ai-system returned inconsistent research state")
        if detail.state in {"pending", "running"}:
            raise PersonalAIError("personal-ai-system research is still running")
        if created.state in {"completed", "insufficient", "failed", "expired"} and (
            detail.state != created.state and not expired_since_read
        ):
            raise PersonalAIError("personal-ai-system returned inconsistent research state")

        if detail.state == "completed":
            if not detail.answer or not detail.answer.strip() or not detail.citations:
                raise PersonalAIError("personal-ai-system returned an uncited research result")
            if detail.expires_at <= now:
                return PersonalAIResearchResult(
                    session_id=detail.id,
                    state="expired",
                    expires_at=detail.expires_at,
                )
            numbers = [citation.number for citation in detail.citations]
            if sorted(numbers) != list(range(1, len(numbers) + 1)):
                raise PersonalAIError("personal-ai-system returned invalid citations")
            if any(
                citation.observed_at > now or citation.expires_at <= citation.observed_at
                for citation in detail.citations
            ):
                raise PersonalAIError("personal-ai-system returned invalid citation timestamps")
            if any(citation.expires_at <= now for citation in detail.citations):
                return PersonalAIResearchResult(
                    session_id=detail.id,
                    state="expired",
                    expires_at=min(
                        detail.expires_at,
                        *(citation.expires_at for citation in detail.citations),
                    ),
                )
            return PersonalAIResearchResult(
                session_id=detail.id,
                state=detail.state,
                expires_at=min(detail.expires_at, *(c.expires_at for c in detail.citations)),
                answer=detail.answer,
                citations=detail.citations,
            )

        # Only completed, unexpired answers and citations may cross this boundary.
        return PersonalAIResearchResult(
            session_id=detail.id,
            state=detail.state,
            expires_at=detail.expires_at,
            failure_code=detail.failure_code,
        )


def _validate_public_citation_url(value: str) -> None:
    validate_http_url(value)
    parsed = urlsplit(value)
    host = (parsed.hostname or "").rstrip(".").lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".localhost"):
        raise InvalidUpstreamResponse("proposal citation URL is not public")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return
    if not address.is_global:
        raise InvalidUpstreamResponse("proposal citation URL is not public")


async def _bounded_json(response: httpx.Response) -> object:
    try:
        return await read_json(response, max_bytes=MAX_RESEARCH_RESPONSE_BYTES)
    except InvalidUpstreamResponse as exc:
        raise PersonalAIError(f"personal-ai-system returned {exc}") from None


async def _consume_research_events(
    response: httpx.Response, *, expected_session_id: UUID
) -> TerminalResearchState | None:
    event_name: str | None = None
    data_lines: list[str] = []
    frame_bytes = 0
    stream_bytes = 0
    event_count = 0
    terminal_state: TerminalResearchState | None = None

    async for line in sse_lines(
        response,
        max_bytes=MAX_RESEARCH_STREAM_BYTES,
        max_line_bytes=MAX_RESEARCH_EVENT_BYTES,
    ):
        line_size = len(line.encode("utf-8")) + 1
        stream_bytes += line_size
        frame_bytes += line_size
        if stream_bytes > MAX_RESEARCH_STREAM_BYTES or frame_bytes > MAX_RESEARCH_EVENT_BYTES:
            raise PersonalAIError("personal-ai-system returned an oversized research stream")
        if line == "":
            if event_name is None and not data_lines:
                frame_bytes = 0
                continue
            event_count += 1
            if event_count > MAX_RESEARCH_EVENTS:
                raise PersonalAIError("personal-ai-system returned too many research events")
            payload = _validate_research_event(
                event_name,
                "\n".join(data_lines),
                expected_session_id=expected_session_id,
            )
            if terminal_state is not None:
                raise PersonalAIError("personal-ai-system returned events after the terminal event")
            if event_name == "research.terminal":
                terminal_state = cast(TerminalResearchState, payload["state"])
            event_name = None
            data_lines = []
            frame_bytes = 0
            continue
        if line.startswith(":"):
            continue
        field, separator, value = line.partition(":")
        if not separator:
            value = ""
        elif value.startswith(" "):
            value = value[1:]
        if field == "event":
            if event_name is not None:
                raise PersonalAIError("personal-ai-system returned an invalid research event")
            event_name = value
        elif field == "data":
            data_lines.append(value)

    # The SSE spec dispatches only complete frames. A trailing partial frame is an
    # interrupted stream and is reconciled against the durable session detail.
    return terminal_state


def _validate_research_event(
    event_name: str | None,
    raw_data: str,
    *,
    expected_session_id: UUID,
) -> dict[str, object]:
    allowed = {
        "research.started",
        "research.planned",
        "research.attempt",
        "research.evidence",
        "research.selected",
        "research.terminal",
    }
    if event_name not in allowed or not raw_data:
        raise PersonalAIError("personal-ai-system returned an invalid research event")
    try:
        payload = json.loads(raw_data)
    except json.JSONDecodeError as exc:
        raise PersonalAIError("personal-ai-system returned invalid research event JSON") from exc
    if not isinstance(payload, dict):
        raise PersonalAIError("personal-ai-system returned an invalid research event")
    if payload.get("schema_version") != "research-v1":
        raise PersonalAIError("personal-ai-system returned an unsupported research event")
    try:
        session_id = UUID(str(payload.get("session_id")))
    except (TypeError, ValueError) as exc:
        raise PersonalAIError("personal-ai-system returned a mismatched research event") from exc
    if session_id != expected_session_id:
        raise PersonalAIError("personal-ai-system returned a mismatched research event")

    def count(name: str, *, maximum: int) -> None:
        value = payload.get(name)
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
            raise PersonalAIError("personal-ai-system returned an invalid research event")

    def uuid_field(name: str) -> None:
        try:
            UUID(str(payload[name]))
        except (KeyError, TypeError, ValueError) as exc:
            raise PersonalAIError("personal-ai-system returned an invalid research event") from exc

    if event_name == "research.started" and payload.get("state") != "running":
        raise PersonalAIError("personal-ai-system returned an invalid research event")
    if event_name == "research.planned":
        count("query_count", maximum=3)
    if event_name == "research.attempt":
        uuid_field("query_id")
        uuid_field("attempt_id")
        if payload.get("state") not in {"completed", "failed"}:
            raise PersonalAIError("personal-ai-system returned an invalid research event")
    if event_name == "research.evidence":
        count("source_count", maximum=12)
        count("evidence_count", maximum=12)
    if event_name == "research.selected":
        count("evidence_count", maximum=12)
    if event_name == "research.terminal":
        if payload.get("state") not in {"completed", "insufficient", "failed", "expired"}:
            raise PersonalAIError("personal-ai-system returned an invalid terminal event")
        if "failure_code" not in payload:
            raise PersonalAIError("personal-ai-system returned an invalid terminal event")
        failure_code = payload.get("failure_code")
        if failure_code is not None and (
            not isinstance(failure_code, str)
            or len(failure_code) > 80
            or re.fullmatch(r"[a-z0-9_]+", failure_code) is None
        ):
            raise PersonalAIError("personal-ai-system returned an invalid terminal event")
    return payload
