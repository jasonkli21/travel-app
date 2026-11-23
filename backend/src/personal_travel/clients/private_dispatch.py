"""Closed pas-dispatch/1 wire for the isolated Travel composition."""

import base64
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from ssl import CERT_REQUIRED, SSLContext
from typing import Annotated, Literal
from uuid import UUID

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import AfterValidator, Field

from personal_travel.clients.private_plan import (
    PLAN_PATH,
    RECEIPT_HEADER,
    PlanReceipt,
    PlanReceiptError,
    PrivatePlanHTTP,
    StrictPlanModel,
)

MEDIA_TYPE = "application/vnd.pas.dispatch-v1+json"
DISPATCH_RECEIPT = "x-pas-dispatch-receipt"
VERSION: Literal["pas-dispatch/1"] = "pas-dispatch/1"
MAX_BYTES = 8192
SAFE_INTEGER = (1 << 53) - 1

Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Opaque = Annotated[str, Field(min_length=1, max_length=255)]
Epoch = Annotated[int, Field(ge=0, le=SAFE_INTEGER)]
Signature = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{86}$")]


def _uuid(value: str) -> str:
    if str(UUID(value)) != value:
        raise ValueError("dispatch_uuid_invalid")
    return value


UUIDString = Annotated[str, AfterValidator(_uuid)]
Phase = Literal["preview", "confirm", "release", "send", "disclose"]


def canonical(value: object) -> bytes:
    """RFC 8785 for this ABI's integer-only number domain."""
    if value is None or type(value) in (bool, str):
        return json.dumps(value, ensure_ascii=False).encode("utf-8")
    if type(value) is int and -SAFE_INTEGER <= value <= SAFE_INTEGER:
        return str(value).encode("ascii")
    if isinstance(value, Mapping):
        keys = sorted(value, key=lambda key: key.encode("utf-16-be"))
        return (
            b"{" + b",".join(canonical(key) + b":" + canonical(value[key]) for key in keys) + b"}"
        )
    if isinstance(value, (list, tuple)):
        return b"[" + b",".join(canonical(item) for item in value) + b"]"
    raise ValueError("dispatch_json_invalid")


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("dispatch_json_invalid")
    return result


def parse(raw: bytes) -> object:
    if not 0 < len(raw) <= MAX_BYTES:
        raise ValueError("dispatch_json_invalid")
    try:
        value: object = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique)
        if canonical(value) == raw:
            return value
    except (ValueError, RecursionError):
        pass
    raise ValueError("dispatch_json_invalid")


def digest(raw: bytes) -> str:
    return sha256(raw).hexdigest()


class DispatchDestination(StrictPlanModel):
    account: Opaque
    connection: Opaque
    endpoint: Opaque
    model: Opaque
    provider: Literal["fake"]
    revision: Epoch
    runtime: Opaque


class DispatchBinding(StrictPlanModel):
    actor_issuer: Opaque
    app: Literal["travel", "health"]
    attempt: UUIDString
    branch: Digest
    client: Opaque
    conversation: UUIDString
    create_key: UUIDString
    create_receipt: Digest
    destination: DispatchDestination
    epoch: Epoch
    installation: Opaque
    memory: Digest
    original_key: UUIDString
    owner: Opaque
    policy: Opaque
    preview_receipt: Digest
    root: UUIDString
    source: Digest
    subject: Opaque
    target: Opaque
    tenant: Opaque
    workspace: Opaque | None

    def commitment(self) -> str:
        return digest(canonical(self.model_dump()))


class PreviewClaims(StrictPlanModel):
    audience: Opaque
    binding_sha256: Digest
    expires: Epoch
    issuer: Opaque
    kid: Opaque
    payload_sha256: Digest
    purpose: Literal["preview"]
    v: Literal["pas-dispatch/1"]


class PreviewEnvelope(StrictPlanModel):
    claims: PreviewClaims
    signature: Signature


class Confirmation(StrictPlanModel):
    confirmation: Digest
    original_key: UUIDString
    preview_sig: Signature
    v: Literal["pas-dispatch/1"]


class TerminalClaims(StrictPlanModel):
    attempt: UUIDString
    audience: Opaque
    binding_sha256: Digest
    body_sha256: Digest
    expires: Epoch
    issuer: Opaque
    kid: Opaque
    message_id: UUIDString
    purpose: Literal["terminal"]
    result_sha256: Digest
    root: UUIDString
    v: Literal["pas-dispatch/1"]


class DispatchPreviewExpired(ValueError):
    """The authenticated preview has expired."""


class DispatchEvidenceConflict(ValueError):
    """Complete evidence conflicts with the submitted original attempt."""


class DispatchStatus(StrictPlanModel):
    state: Literal["unknown", "claimed", "send_started", "completed", "quarantined"]
    original_key: UUIDString
    attempt: UUIDString
    root: UUIDString
    body_sha256: Digest
    message_id: UUIDString | None
    terminal: TerminalClaims | None
    signature: Signature | None


class DispatchDetail(StrictPlanModel):
    message_id: UUIDString
    attempt: UUIDString
    root: UUIDString
    original_key: UUIDString
    result: str = Field(max_length=MAX_BYTES)


class CurrentSource(StrictPlanModel):
    binding_sha256: Digest
    phase: Phase
    nonce: UUIDString
    current: bool
    expires: Epoch


@dataclass(frozen=True, slots=True)
class SigningRole:
    issuer: str
    kid: str
    audience: str
    public_key: Ed25519PublicKey


def _verify(role: SigningRole, raw: bytes, signature: str) -> None:
    try:
        if re.fullmatch(r"[A-Za-z0-9_-]{86}", signature) is None:
            raise ValueError
        signature_bytes = base64.urlsafe_b64decode(signature + "==")
        if base64.urlsafe_b64encode(signature_bytes).decode("ascii").rstrip("=") != signature:
            raise ValueError
        role.public_key.verify(signature_bytes, raw)
    except (ValueError, InvalidSignature):
        raise ValueError("dispatch_signature_invalid") from None


def verify_preview(
    raw: bytes, binding: DispatchBinding, role: SigningRole, now: float
) -> tuple[Confirmation, int]:
    value = parse(raw)
    if not isinstance(value, dict) or set(value) != {"claims", "signature"}:
        raise ValueError("dispatch_preview_invalid")
    claims_bytes = canonical(value["claims"])
    signature = value["signature"]
    if not isinstance(signature, str):
        raise ValueError("dispatch_preview_invalid")
    _verify(role, claims_bytes, signature)
    envelope = PreviewEnvelope.model_validate(value)
    claims = envelope.claims
    if (
        (claims.issuer, claims.kid, claims.audience) != (role.issuer, role.kid, role.audience)
        or claims.audience != binding.app
        or claims.binding_sha256 != binding.commitment()
        or claims.expires > now + 60
    ):
        raise ValueError("dispatch_preview_invalid")
    if claims.expires <= now:
        raise DispatchPreviewExpired("dispatch_preview_expired")
    return Confirmation(
        confirmation=digest(claims_bytes),
        original_key=binding.original_key,
        preview_sig=envelope.signature,
        v=VERSION,
    ), claims.expires


def verify_status(
    raw: bytes, binding: DispatchBinding, body_digest: str, role: SigningRole, now: float
) -> DispatchStatus:
    value = parse(raw)
    if not isinstance(value, dict):
        raise ValueError("dispatch_status_invalid")
    if value.get("state") == "completed":
        signature = value.get("signature")
        if not isinstance(signature, str) or value.get("terminal") is None:
            raise DispatchEvidenceConflict("dispatch_status_invalid")
        try:
            _verify(role, canonical(value["terminal"]), signature)
        except ValueError:
            raise DispatchEvidenceConflict("dispatch_terminal_signature_invalid") from None
    try:
        status = DispatchStatus.model_validate(value)
    except ValueError:
        if value.get("state") == "completed":
            raise DispatchEvidenceConflict("dispatch_status_invalid") from None
        raise
    if (status.original_key, status.attempt, status.root, status.body_sha256) != (
        binding.original_key,
        binding.attempt,
        binding.root,
        body_digest,
    ):
        raise DispatchEvidenceConflict("dispatch_status_invalid")
    terminal = status.terminal
    if status.state != "completed":
        if status.message_id is not None or terminal is not None or status.signature is not None:
            raise DispatchEvidenceConflict("dispatch_status_invalid")
    elif (
        terminal is None
        or status.message_id is None
        or terminal.message_id != status.message_id
        or (terminal.issuer, terminal.kid, terminal.audience)
        != (role.issuer, role.kid, role.audience)
        or terminal.binding_sha256 != binding.commitment()
        or (terminal.attempt, terminal.root, terminal.body_sha256)
        != (binding.attempt, binding.root, body_digest)
    ):
        raise DispatchEvidenceConflict("dispatch_terminal_invalid")
    if terminal is not None and now >= terminal.expires:
        # Archived expiry needs an authenticated commit-time witness absent from v1.
        raise DispatchEvidenceConflict("dispatch_terminal_expired")
    return status


def verify_detail(raw: bytes, status: DispatchStatus) -> DispatchDetail:
    value = parse(raw)
    try:
        detail = DispatchDetail.model_validate(value)
    except ValueError:
        raise DispatchEvidenceConflict("dispatch_detail_invalid") from None
    if (
        status.terminal is None
        or (detail.message_id, detail.attempt, detail.root, detail.original_key)
        != (status.message_id, status.attempt, status.root, status.original_key)
        or digest(detail.result.encode("utf-8")) != status.terminal.result_sha256
    ):
        raise DispatchEvidenceConflict("dispatch_detail_invalid")
    return detail


class PrivateDispatchHTTP(PrivatePlanHTTP):
    """Typed, loopback-only Travel-to-PAS transport for offline fixtures."""

    def __init__(
        self,
        origin: str,
        *,
        bearer: str,
        transport: httpx.MockTransport | None = None,
        tls: SSLContext | None = None,
    ) -> None:
        if httpx.URL(origin).host not in {"127.0.0.1", "::1"}:
            raise ValueError("private_dispatch_loopback_required")
        if transport is not None and (
            type(transport) is not httpx.MockTransport or tls is not None
        ):
            raise ValueError("private_dispatch_transport_invalid")
        if transport is None:
            if tls is None or tls.verify_mode != CERT_REQUIRED or not tls.check_hostname:
                raise ValueError("private_dispatch_pinned_tls_required")
            selected_transport: httpx.BaseTransport = httpx.HTTPTransport(
                verify=tls, trust_env=False, retries=0
            )
        else:
            selected_transport = transport
        super().__init__(origin, bearer=bearer, transport=selected_transport)

    def _request(
        self,
        method: Literal["GET", "POST"],
        path: str,
        *,
        body: bytes | None = None,
        status: int,
        accept: str | None = None,
        request_media: str = "application/json",
        response_media: str | None = "application/json",
        original_key: UUID | None = None,
        deadline_seconds: float | None = 3.0,
    ) -> tuple[bytes, httpx.Headers]:
        if body is not None and len(body) > 128 * 1024:
            raise PlanReceiptError("private_dispatch_request_too_large")
        return super()._request(
            method,
            path,
            body=body,
            status=status,
            accept=accept,
            request_media=request_media,
            response_media=response_media,
            original_key=original_key,
            deadline_seconds=deadline_seconds,
        )

    def dispatch_preview(self, conversation: UUID, body: bytes) -> tuple[PlanReceipt, bytes]:
        raw, headers = self._request(
            "POST",
            f"{PLAN_PATH}/{conversation}/context-preview",
            body=body,
            status=200,
            accept=MEDIA_TYPE,
            request_media=MEDIA_TYPE,
            response_media=MEDIA_TYPE,
        )
        signatures = headers.get_list(RECEIPT_HEADER)
        envelopes = headers.get_list(DISPATCH_RECEIPT)
        if (
            headers.get_list("cache-control") != ["no-store"]
            or len(signatures) != 1
            or len(envelopes) != 1
        ):
            raise PlanReceiptError("private_plan_receipt_unavailable")
        return PlanReceipt(raw, signatures[0]), envelopes[0].encode("ascii")

    def confirm_once(self, conversation: UUID, body: bytes) -> None:
        Confirmation.model_validate(parse(body))
        self._request(
            "POST",
            f"{PLAN_PATH}/{conversation}/continue",
            body=body,
            status=202,
            accept=MEDIA_TYPE,
            request_media=MEDIA_TYPE,
            response_media=None,
        )

    def dispatch_status(self, conversation: UUID, key: UUID) -> bytes:
        raw, headers = self._request(
            "GET",
            f"/v1/conversations/{conversation}/continuations/{key}",
            status=200,
            accept=MEDIA_TYPE,
            response_media=MEDIA_TYPE,
        )
        if headers.get_list("cache-control") != ["no-store"]:
            raise PlanReceiptError("private_dispatch_status_unavailable")
        return raw

    def dispatch_detail(self, conversation: UUID, key: UUID) -> bytes:
        raw, headers = self._request(
            "GET",
            f"/v1/conversations/{conversation}",
            status=200,
            accept=MEDIA_TYPE,
            response_media=MEDIA_TYPE,
            original_key=key,
        )
        if headers.get_list("cache-control") != ["no-store"]:
            raise PlanReceiptError("private_dispatch_detail_unavailable")
        return raw
