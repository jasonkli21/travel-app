from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, cast
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from personal_travel.config import get_settings

ResearchState = Literal["pending", "running", "completed", "insufficient", "failed", "expired"]
TerminalResearchState = Literal["completed", "insufficient", "failed", "expired"]

MAX_RESEARCH_EVENT_BYTES = 16 * 1024
MAX_RESEARCH_STREAM_BYTES = 128 * 1024
MAX_RESEARCH_EVENTS = 128
MAX_RESEARCH_RESPONSE_BYTES = 1_000_000


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
        if value != value.strip() or any(ord(char) < 32 for char in value):
            raise ValueError("citation URL must be HTTP or HTTPS")
        try:
            parsed = urlsplit(value)
            hostname = parsed.hostname
        except ValueError:
            raise ValueError("citation URL must be HTTP or HTTPS") from None
        if (
            parsed.scheme not in {"http", "https"}
            or not hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("citation URL must be HTTP or HTTPS")
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


class PersonalAIClient:
    """Typed HTTP boundary to personal-ai-system."""

    def __init__(self, base_url: str | None = None, timeout_seconds: float | None = None) -> None:
        settings = get_settings()
        self._base_url = str(base_url or settings.personal_ai_base_url).rstrip("/")
        self._timeout = timeout_seconds or settings.personal_ai_timeout_seconds

    async def health(self) -> PersonalAIHealth:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(f"{self._base_url}/health")
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise PersonalAIError("personal-ai-system is unavailable") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise PersonalAIError("personal-ai-system returned invalid health JSON") from exc

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
        """Create or replay one research session, run pending work, and read its result."""

        timeout = httpx.Timeout(self._timeout)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                create = await client.post(
                    f"{self._base_url}/v1/research",
                    json={
                        "schema_version": "research-v1",
                        "question": question,
                        "freshness": freshness,
                        "idempotency_key": str(idempotency_key),
                    },
                )
                create.raise_for_status()
                created = _ResearchSession.model_validate(await _bounded_json(create))

                terminal_event: TerminalResearchState | None = None
                if created.state == "pending":
                    try:
                        async with client.stream(
                            "POST", f"{self._base_url}/v1/research/{created.id}/run"
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

                detail_response = await client.get(
                    f"{self._base_url}/v1/research/{created.id}"
                )
                detail_response.raise_for_status()
                detail = _ResearchSession.model_validate(await _bounded_json(detail_response))
        except PersonalAIError:
            raise
        except (httpx.HTTPError, ValidationError, ValueError, TypeError) as exc:
            raise PersonalAIError(
                "personal-ai-system returned an unavailable or invalid response"
            ) from exc

        if detail.id != created.id:
            raise PersonalAIError("personal-ai-system returned a mismatched research session")
        if terminal_event is not None and detail.state != terminal_event:
            raise PersonalAIError("personal-ai-system returned inconsistent research state")
        if detail.state in {"pending", "running"}:
            raise PersonalAIError("personal-ai-system research is still running")
        if created.state in {"completed", "insufficient", "failed", "expired"} and (
            detail.state != created.state
        ):
            raise PersonalAIError("personal-ai-system returned inconsistent research state")

        now = datetime.now(UTC)
        if detail.state == "completed":
            if detail.answer is None or not detail.citations:
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
                expires_at=detail.expires_at,
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


async def _bounded_json(response: httpx.Response) -> object:
    content_length = response.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > MAX_RESEARCH_RESPONSE_BYTES:
                raise PersonalAIError("personal-ai-system returned an oversized response")
        except ValueError:
            raise PersonalAIError("personal-ai-system returned an invalid response size") from None

    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > MAX_RESEARCH_RESPONSE_BYTES:
            raise PersonalAIError("personal-ai-system returned an oversized response")
        chunks.append(chunk)
    try:
        return json.loads(b"".join(chunks))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PersonalAIError("personal-ai-system returned invalid research JSON") from exc


async def _consume_research_events(
    response: httpx.Response, *, expected_session_id: UUID
) -> TerminalResearchState | None:
    event_name: str | None = None
    data_lines: list[str] = []
    frame_bytes = 0
    stream_bytes = 0
    event_count = 0
    terminal_state: TerminalResearchState | None = None

    async for line in response.aiter_lines():
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
