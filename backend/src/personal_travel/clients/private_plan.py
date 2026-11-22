"""Pinned HTTP receipts for the isolated PAS create and preview transactions."""

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from typing import Any, Literal
from uuid import UUID

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field

from personal_travel.api.schemas.private_plan import PRIVATE_PLAN_LANE, SHA256_PATTERN
from personal_travel.clients.task_wire import canonical_bytes

PLAN_PATH = "/private/application-plan/v1/conversations"
RECEIPT_HEADER = "x-application-plan-receipt"
MAX_PREVIEW_BYTES = 8192
PlanStatus = Literal["not_found", "in_progress", "unknown"]


def create_body(original_key: UUID, title: str) -> bytes:
    return canonical_bytes({"idempotency_key": str(original_key), "title": title})


def preview_body(original_key: UUID, content: str) -> bytes:
    return canonical_bytes(
        {
            "idempotency_key": str(original_key),
            "content": content,
            "choice": {"mode": PRIVATE_PLAN_LANE},
        }
    )


class PlanReceiptError(ValueError):
    pass


def _parse_json(raw: bytes) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result = dict(pairs)
        if len(result) != len(pairs):
            raise PlanReceiptError("private_plan_receipt_invalid")
        return result

    if not 0 < len(raw) <= MAX_PREVIEW_BYTES:
        raise PlanReceiptError("private_plan_receipt_invalid")
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
    except (ValueError, RecursionError) as error:
        raise PlanReceiptError("private_plan_receipt_invalid") from error


class StrictPlanModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class PlanDestination(StrictPlanModel):
    application: Literal["travel"]
    provider: Literal["ChatGPT"]
    model: str = Field(min_length=1, max_length=200)
    runtime: str = Field(min_length=1, max_length=200)
    recipient: str = Field(min_length=1, max_length=200)


class _History(StrictPlanModel):
    category: Literal["conversation_history"]
    turns: int = Field(ge=0, le=3072)


class _Fields(StrictPlanModel):
    category: Literal["domain_current", "ai_memory"]
    fields: list[str]


class PlanPreview(StrictPlanModel):
    confirmation: str = Field(pattern=SHA256_PATTERN)
    destination: PlanDestination
    purpose: Literal["foreground_application_plan"]
    selected_context: list[_History | _Fields] = Field(min_length=3, max_length=3)
    input_tokens_estimated: int = Field(gt=0)
    expires_at: str


@dataclass(frozen=True, slots=True, repr=False)
class PlanReceiptRegistration:
    key: Ed25519PublicKey
    destination: PlanDestination


@dataclass(frozen=True, slots=True, repr=False)
class PlanReceipt:
    body: bytes
    signature: str

    def _json(self) -> Any:
        return _parse_json(self.body)

    def conversation_id(self) -> UUID:
        data = self._json()
        if not isinstance(data, dict) or set(data) != {"conversation_id"}:
            raise PlanReceiptError("private_plan_receipt_invalid")
        cid = data["conversation_id"]
        try:
            if not isinstance(cid, str) or str(UUID(cid)) != cid:
                raise ValueError
            return UUID(cid)
        except ValueError as error:
            raise PlanReceiptError("private_plan_receipt_invalid") from error

    def preview(
        self, *, destination: PlanDestination, now: datetime
    ) -> tuple[PlanPreview, datetime]:
        try:
            parsed = PlanPreview.model_validate(self._json())
            expiry = datetime.fromisoformat(parsed.expires_at)
        except ValueError as error:
            raise PlanReceiptError("private_plan_receipt_invalid") from error
        fields = {
            item.category: item.fields
            for item in parsed.selected_context
            if isinstance(item, _Fields)
        }
        if (
            expiry.utcoffset() is None
            or not now < expiry <= now + timedelta(seconds=45)
            or parsed.destination != destination
            or fields
            != {"ai_memory": ["content"], "domain_current": ["trip_revision", "trip_title"]}
            or not isinstance(parsed.selected_context[0], _History)
            or sum(isinstance(item, _History) for item in parsed.selected_context) != 1
        ):
            raise PlanReceiptError("private_plan_receipt_invalid")
        return parsed, expiry

    def verify(
        self,
        registration: PlanReceiptRegistration,
        *,
        operation: Literal["create", "preview"],
        target_id: str,
        client_namespace: str,
        owner_id: str,
        issuer: str,
        subject: str,
        conversation_id: UUID,
        request_digest: str,
        source_revision: int | None = None,
    ) -> None:
        statement = canonical_bytes(
            {
                "schema_version": "travel-application-plan-receipt-v1",
                "operation": operation,
                "target_id": target_id,
                "client_namespace": client_namespace,
                "application_id": "travel",
                "workspace_id": None,
                "owner_id": owner_id,
                "issuer": issuer,
                "subject": subject,
                "conversation_id": str(conversation_id),
                "request_digest": request_digest,
                "response_digest": sha256(self.body).hexdigest(),
                "source_revision": str(source_revision) if source_revision is not None else None,
            }
        )
        try:
            if re.fullmatch(r"[0-9a-f]{128}", self.signature) is None:
                raise ValueError
            registration.key.verify(bytes.fromhex(self.signature), statement)
        except (ValueError, InvalidSignature) as error:
            raise PlanReceiptError("private_plan_receipt_invalid") from error


class PrivatePlanHTTP:
    """Direct bearer, fixed isolated origin, no redirects or automatic POST retries."""

    def __init__(
        self,
        origin: str,
        *,
        bearer: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        url = httpx.URL(origin)
        if (
            url.scheme != "https"
            or not url.host
            or url.username
            or url.password
            or url.path != "/"
            or url.query
            or url.fragment
            or not bearer
            or len(bearer) > 8192
            or any(char.isspace() for char in bearer)
        ):
            raise ValueError("private_plan_origin_or_bearer_invalid")
        self._origin = url
        self._bearer = bearer
        self._transport = transport

    def receipt(self, path: str, body: bytes, *, status: int) -> PlanReceipt:
        raw, headers = self._request("POST", path, body=body, status=status)
        signatures = headers.get_list(RECEIPT_HEADER)
        if headers.get_list("cache-control") != ["no-store"] or len(signatures) != 1:
            raise PlanReceiptError("private_plan_receipt_unavailable")
        return PlanReceipt(raw, signatures[0])

    def original_status(self, conversation: UUID, original_key: UUID) -> PlanStatus:
        raw, _ = self._request(
            "GET", f"/v1/conversations/{conversation}/continuations/{original_key}", status=200
        )
        value = _parse_json(raw)
        if not isinstance(value, dict) or set(value) != {"state", "message_id"}:
            raise PlanReceiptError("private_plan_status_unavailable")
        state = value["state"]
        if state == "not_found" and value["message_id"] is None:
            return "not_found"
        if state == "in_progress":
            return "in_progress"
        if state in ("completed", "unknown"):
            # Preview-only recovery has no terminal/fence witness.
            return "unknown"
        raise PlanReceiptError("private_plan_status_unavailable")

    def _request(
        self,
        method: Literal["GET", "POST"],
        path: str,
        *,
        body: bytes | None = None,
        status: int,
    ) -> tuple[bytes, httpx.Headers]:
        with (
            httpx.Client(
                trust_env=False,
                transport=self._transport,
                timeout=3.0,
                follow_redirects=False,
            ) as client,
            client.stream(
                method,
                self._origin.copy_with(path=path),
                content=body,
                headers={
                    "authorization": f"Bearer {self._bearer}",
                    "content-type": "application/json",
                    "x-application-id": "travel",
                    "accept-encoding": "identity",
                },
            ) as response,
        ):
            if (
                response.status_code != status
                or response.headers.get_list("content-type") != ["application/json"]
                or response.headers.get("content-encoding", "identity") != "identity"
            ):
                raise PlanReceiptError("private_plan_response_unavailable")
            raw = bytearray()
            for chunk in response.iter_bytes(chunk_size=4096):
                if len(raw) + len(chunk) > MAX_PREVIEW_BYTES:
                    raise PlanReceiptError("private_plan_response_invalid")
                raw.extend(chunk)
            return bytes(raw), response.headers
