"""Default-off synthetic confirmation and authorized original-key observation."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from hmac import compare_digest
from hmac import digest as hmac_digest
from threading import Lock
from time import monotonic
from typing import Literal, Protocol
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from personal_travel.api.routes.personal_ai_context import _record
from personal_travel.clients.private_dispatch import (
    CurrentSource,
    DispatchBinding,
    DispatchDestination,
    DispatchEvidenceConflict,
    DispatchPreviewExpired,
    Phase,
    PrivateDispatchHTTP,
    SigningRole,
    canonical,
    digest,
    verify_detail,
    verify_preview,
    verify_status,
)
from personal_travel.clients.private_plan import (
    PLAN_PATH,
    PlanPreview,
    PlanReceipt,
    PlanReceiptError,
    create_body,
    preview_body,
)
from personal_travel.models.auth import AuthIdentity, AuthSession
from personal_travel.models.private_plan import (
    PrivatePlanBinding,
    PrivatePlanConfirmation,
    PrivatePlanIntent,
)
from personal_travel.models.trip import Trip
from personal_travel.services.errors import DomainError, not_found
from personal_travel.services.private_plan import (
    PrivatePlanJournal,
    _plan_conflict,
    resolve_plan_trip,
)

MAX_CALLBACK_NONCES = 4096


@dataclass(frozen=True, slots=True)
class LiveDispatchGrants:
    """Current independently registered grants, fetched anew for each phase."""

    permission_epoch: int
    policy: str
    branch: str
    memory: str
    source_grant: str
    model_grant: str
    runtime_grant: str
    destination: DispatchDestination
    expires: int


class GrantReader(Protocol):
    def __call__(
        self, session: Session, trip: Trip, binding: DispatchBinding, phase: Phase
    ) -> LiveDispatchGrants | None: ...


class ContinuityReader(Protocol):
    """Check a retained checkpoint outside the recovered Travel database."""

    def __call__(
        self, session: Session, binding: DispatchBinding, submitted_body_digest: str | None
    ) -> bool: ...


@dataclass(frozen=True, slots=True, repr=False)
class DispatchRegistration:
    travel_owner: str
    pas_owner: str
    pas_client: str
    tenant: str
    installation: str
    destination: DispatchDestination
    preview_role: SigningRole
    terminal_role: SigningRole
    source_key: bytes
    session_token_digest: str
    role_active: Callable[[SigningRole], bool]
    grants: GrantReader
    continuity: ContinuityReader

    def __post_init__(self) -> None:
        if (
            len(self.source_key) < 32
            or len(self.session_token_digest) != 64
            or any(char not in "0123456789abcdef" for char in self.session_token_digest)
            or self.preview_role.audience != "travel"
            or self.terminal_role.audience != self.preview_role.issuer
            or self.preview_role.public_key.public_bytes_raw()
            == self.terminal_role.public_key.public_bytes_raw()
            or self.destination.provider != "fake"
            or self.destination.endpoint != "loopback-fixture"
        ):
            raise ValueError("private_dispatch_registration_invalid")


@dataclass(frozen=True, slots=True, repr=False)
class CreatedForDispatch:
    conversation: UUID
    receipt: PlanReceipt


@dataclass(frozen=True, slots=True, repr=False)
class PreparedTravelDispatch:
    binding: DispatchBinding
    create_receipt: PlanReceipt
    preview_receipt: PlanReceipt
    envelope: bytes
    confirmation_body: bytes

    @property
    def metadata(self) -> PlanPreview:
        """Derive each view from the immutable, previously verified receipt bytes."""
        return PlanPreview.model_validate_json(self.preview_receipt.body)

    @property
    def destination(self) -> DispatchDestination:
        """The actual synthetic destination bound alongside the legacy metadata."""
        return self.binding.destination


@dataclass(frozen=True, slots=True, repr=False)
class _AuthorizedDispatch:
    plan: PrivatePlanBinding
    intent: PrivatePlanIntent
    expiry: int


@dataclass(frozen=True, slots=True, repr=False)
class _Submission:
    binding_id: UUID
    body_digest: str
    body: bytes


@dataclass(frozen=True, slots=True, repr=False)
class DispatchObservation:
    state: Literal["unknown", "claimed", "send_started", "completed", "quarantined"]
    message_id: UUID | None = None
    result: str | None = None


def source_commitment(key: bytes, trip: Trip, grants: LiveDispatchGrants) -> str:
    """Commit to the exact ordered travel-context-read-v2 private trip record."""
    record = _record(
        trip,
        entity_type="trip",
        entity_id=trip.id,
        payload={"trip_title": trip.title, "trip_revision": trip.revision},
    )
    return hmac_digest(
        key,
        canonical(
            {
                "schema": "travel-context-read-v2",
                "fields": list(record.payload),
                "projection": record.model_dump(mode="json"),
                "policy": grants.policy,
                "permission_epoch": grants.permission_epoch,
                "grants": [grants.source_grant, grants.model_grant, grants.runtime_grant],
                "destination": grants.destination.model_dump(),
                "grant_expires": grants.expires,
            }
        ),
        "sha256",
    ).hex()


class PrivateDispatchJournal:
    """Use the existing private-plan binding and explicit local test registration."""

    def __init__(
        self, legacy: PrivatePlanJournal, registration: DispatchRegistration | None = None
    ) -> None:
        self.legacy = legacy
        self.registration = registration
        self.session = legacy.session

    def _registration(self) -> DispatchRegistration:
        if self.registration is None or self.legacy.receipts is None:
            raise not_found("private plan")
        return self.registration

    def _principal(self) -> AuthIdentity:
        registration = self._registration()
        identity = self.legacy._identity()
        native_session = self.session.scalar(
            select(AuthSession)
            .where(
                AuthSession.token_hash == registration.session_token_digest,
                AuthSession.owner_id == identity.owner_id,
                AuthSession.revoked_at.is_(None),
                AuthSession.issued_at <= self.legacy.clock(),
                AuthSession.expires_at > self.legacy.clock(),
            )
            .with_for_update(read=True)
        )
        if native_session is None or registration.travel_owner != identity.owner_id:
            raise not_found("private plan")
        return identity

    def _authorize(
        self,
        binding: DispatchBinding,
        phase: Phase,
        *,
        trip_id: UUID | None = None,
        pending: bool = False,
    ) -> _AuthorizedDispatch:
        registration = self._registration()
        identity = self._principal()
        if (
            binding.app != "travel"
            or binding.workspace is not None
            or (binding.actor_issuer, binding.subject, identity.owner_id)
            != (identity.issuer, identity.subject, registration.travel_owner)
            or (binding.owner, binding.client, binding.target, binding.tenant, binding.installation)
            != (
                registration.pas_owner,
                registration.pas_client,
                self.legacy.trust.target_id,
                registration.tenant,
                registration.installation,
            )
            or binding.destination != registration.destination
            or not registration.role_active(
                registration.terminal_role if phase == "disclose" else registration.preview_role
            )
        ):
            raise not_found("private plan")
        plan, trip = resolve_plan_trip(
            self.session,
            identity=identity,
            trust=self.legacy.trust,
            conversation_id=UUID(binding.conversation),
            expected_trip_id=trip_id,
            lock=True,
        )
        intent = self.legacy._intent(plan, lock=True)
        states = ("preview_pending", "preview_ready") if pending else ("preview_ready",)
        if (
            intent is None
            or intent.state not in states
            or (str(plan.create_key), str(intent.original_key), trip.revision)
            != (binding.create_key, binding.original_key, intent.source_revision)
        ):
            raise not_found("private plan")
        grants = registration.grants(self.session, trip, binding, phase)
        if (
            grants is None
            or not grants.source_grant
            or not grants.model_grant
            or not grants.runtime_grant
            or grants.expires <= self.legacy.clock().timestamp()
            or (grants.permission_epoch, grants.policy, grants.branch, grants.memory)
            != (binding.epoch, binding.policy, binding.branch, binding.memory)
            or grants.destination != binding.destination
            or not compare_digest(
                source_commitment(registration.source_key, trip, grants), binding.source
            )
        ):
            raise not_found("private plan")
        return _AuthorizedDispatch(plan, intent, grants.expires)

    def _verify_artifacts(
        self,
        binding: DispatchBinding,
        authority: _AuthorizedDispatch,
        create_receipt: PlanReceipt,
        preview_receipt: PlanReceipt,
    ) -> None:
        plan, intent = authority.plan, authority.intent
        conversation = UUID(binding.conversation)
        if create_receipt.conversation_id() != conversation:
            raise not_found("private plan")
        self._verify_create_artifact(binding, plan, create_receipt)
        self.legacy._verify_receipt(
            preview_receipt,
            plan,
            operation="preview",
            conversation_id=conversation,
            request_digest=intent.request_digest,
            source_revision=intent.source_revision,
        )
        if digest(preview_receipt.signature.encode("ascii")) != binding.preview_receipt or (
            intent.preview_digest is not None
            and digest(preview_receipt.body) != intent.preview_digest
        ):
            raise not_found("private plan")

    def _verify_create_artifact(
        self, binding: DispatchBinding, plan: PrivatePlanBinding, create_receipt: PlanReceipt
    ) -> None:
        conversation = UUID(binding.conversation)
        self.legacy._verify_receipt(
            create_receipt,
            plan,
            operation="create",
            conversation_id=conversation,
            request_digest=digest(create_body(plan.create_key, plan.title)),
        )
        if (
            digest(bytes.fromhex(create_receipt.signature)) != plan.create_receipt_digest
            or digest(create_receipt.signature.encode("ascii")) != binding.create_receipt
        ):
            raise not_found("private plan")

    def _check_continuity(
        self, binding: DispatchBinding, row: PrivatePlanConfirmation | None
    ) -> None:
        if not self._registration().continuity(
            self.session, binding, row.body_digest if row is not None else None
        ):
            raise DomainError(
                "private_dispatch_unavailable", "Private plan unavailable.", status_code=503
            )

    def prepare(
        self,
        *,
        trip_id: UUID,
        binding: DispatchBinding,
        create_receipt: PlanReceipt,
        preview_receipt: PlanReceipt,
        envelope: bytes,
    ) -> PreparedTravelDispatch:
        registration = self._registration()
        with self.session.begin():
            authority = self._authorize(binding, "preview", trip_id=trip_id, pending=True)
            self._check_continuity(binding, None)
            if authority.intent.state != "preview_pending":
                raise _plan_conflict()
            self._verify_artifacts(binding, authority, create_receipt, preview_receipt)
            assert self.legacy.receipts is not None
            metadata, expiry = preview_receipt.preview(
                destination=self.legacy.receipts.destination.model_copy(
                    update={"provider": "fake"}
                ),
                now=self.legacy.clock(),
                synthetic=True,
            )
            confirmation, dispatch_expiry = verify_preview(
                envelope, binding, registration.preview_role, self.legacy.clock().timestamp()
            )
            if (
                metadata.destination.model,
                metadata.destination.runtime,
                metadata.destination.recipient,
            ) != (
                binding.destination.model,
                binding.destination.runtime,
                binding.destination.account,
            ):
                raise ValueError("dispatch_destination_invalid")
            if dispatch_expiry > min(authority.expiry, int(expiry.timestamp())):
                raise ValueError("dispatch_preview_expiry_invalid")
            authority.intent.preview_digest = digest(preview_receipt.body)
            authority.intent.preview_expires_at = expiry
            authority.intent.state = "preview_ready"
            return PreparedTravelDispatch(
                binding,
                create_receipt,
                preview_receipt,
                envelope,
                canonical(confirmation.model_dump()),
            )

    def _row(self, plan: PrivatePlanBinding) -> PrivatePlanConfirmation | None:
        return self.session.get(PrivatePlanConfirmation, plan.id, with_for_update=True)

    def _matches_row(self, row: PrivatePlanConfirmation, binding: DispatchBinding) -> bool:
        return (
            row.binding_digest == binding.commitment()
            and (str(row.original_key), str(row.attempt), str(row.root))
            == (binding.original_key, binding.attempt, binding.root)
            and (row.create_receipt_digest, row.preview_receipt_digest)
            == (binding.create_receipt, binding.preview_receipt)
            and digest(row.body) == row.body_digest
        )

    def submit(
        self, *, trip_id: UUID, prepared: PreparedTravelDispatch, human_confirmation: bytes
    ) -> _Submission | None:
        """Return a send authorization only to the transaction inserting the marker."""
        binding = prepared.binding
        registration = self._registration()
        try:
            with self.session.begin():
                authority = self._authorize(binding, "confirm", trip_id=trip_id)
                self._verify_artifacts(
                    binding, authority, prepared.create_receipt, prepared.preview_receipt
                )
                row = self._row(authority.plan)
                self._check_continuity(binding, row)
                if row is not None:
                    if not self._matches_row(row, binding) or row.body != human_confirmation:
                        raise _plan_conflict()
                    return None
                now = self.legacy.clock().timestamp()
                if authority.intent.preview_expires_at is None or now >= (
                    authority.intent.preview_expires_at.timestamp()
                ):
                    raise DomainError(
                        "private_dispatch_expired", "Private plan preview expired.", status_code=410
                    )
                claims, expiry = verify_preview(
                    prepared.envelope, binding, registration.preview_role, now
                )
                body = canonical(claims.model_dump())
                if (
                    expiry > authority.expiry
                    or body != prepared.confirmation_body
                    or body != human_confirmation
                ):
                    raise _plan_conflict()
                body_digest = digest(body)
                self.session.add(
                    PrivatePlanConfirmation(
                        binding_id=authority.plan.id,
                        owner_id=authority.plan.owner_id,
                        original_key=UUID(binding.original_key),
                        attempt=UUID(binding.attempt),
                        root=UUID(binding.root),
                        binding_digest=binding.commitment(),
                        body=body,
                        body_digest=body_digest,
                        confirmation_digest=claims.confirmation,
                        create_receipt_digest=binding.create_receipt,
                        preview_receipt_digest=binding.preview_receipt,
                    )
                )
                return _Submission(authority.plan.id, body_digest, body)
        except IntegrityError:
            raise _plan_conflict() from None

    def check(
        self, *, trip_id: UUID, binding: DispatchBinding, phase: Literal["confirm", "disclose"]
    ) -> _Submission:
        with self.session.begin():
            authority = self._authorize(binding, phase, trip_id=trip_id)
            row = self._row(authority.plan)
            if row is None or row.quarantined_at is not None or not self._matches_row(row, binding):
                raise not_found("private plan")
            self._check_continuity(binding, row)
            return _Submission(authority.plan.id, row.body_digest, row.body)

    def quarantine(self, submission: _Submission) -> None:
        """Record stale or contradictory evidence without recording any result text."""
        with self.session.begin():
            row = self.session.get(
                PrivatePlanConfirmation, submission.binding_id, with_for_update=True
            )
            if (
                row is not None
                and row.body_digest == submission.body_digest
                and row.quarantined_at is None
            ):
                row.quarantined_at = self.legacy.clock()

    def source_current(self, binding: DispatchBinding, phase: Phase) -> int:
        with self.session.begin():
            authority = self._authorize(binding, phase, pending=phase == "preview")
            row = self._row(authority.plan)
            if phase != "preview" and (
                row is None or row.quarantined_at is not None or not self._matches_row(row, binding)
            ):
                raise not_found("private plan")
            self._check_continuity(binding, row)
            return authority.expiry


class RegisteredTravelSource:
    """In-process capability for PAS to verify one registered Travel source."""

    def __init__(
        self, journal: Callable[[], PrivateDispatchJournal], clock: Callable[[], datetime]
    ) -> None:
        self._journal = journal
        self._clock = clock
        self._nonces: set[str] = set()
        self._lock = Lock()

    def current(self, binding: DispatchBinding, phase: Phase, nonce: str) -> CurrentSource:
        started = monotonic()
        result = CurrentSource(
            binding_sha256=binding.commitment(),
            phase=phase,
            nonce=nonce,
            current=False,
            expires=int(self._clock().timestamp()),
        )
        with self._lock:
            if nonce in self._nonces or len(self._nonces) >= MAX_CALLBACK_NONCES:
                return result
            self._nonces.add(nonce)
        try:
            journal = self._journal()
            with journal.session:
                expires = journal.source_current(binding, phase)
        except (DomainError, SQLAlchemyError, ValueError):
            return result
        now = self._clock().timestamp()
        if monotonic() - started >= 1 or now >= expires:
            return result
        return result.model_copy(update={"current": True, "expires": min(expires, int(now) + 1)})


class PrivateDispatchCaller:
    def __init__(self, journal: PrivateDispatchJournal, http: PrivateDispatchHTTP) -> None:
        self.journal = journal
        self.http = http

    def create(self, *, trip_id: UUID, create_key: UUID, title: str) -> CreatedForDispatch:
        self.journal._registration()
        legacy = self.journal.legacy
        with legacy.session.begin():
            self.journal._principal()
        reservation = legacy.reserve_create(trip_id=trip_id, create_key=create_key, title=title)
        if reservation.conversation_id is not None:
            # Established legacy rows cannot fabricate a missing original artifact.
            raise not_found("private plan")
        receipt = self.http.receipt(PLAN_PATH, create_body(create_key, title), status=201)
        conversation = legacy.bind_created_conversation(
            trip_id=trip_id, create_key=create_key, receipt=receipt
        )
        return CreatedForDispatch(conversation, receipt)

    def preview(
        self,
        *,
        trip_id: UUID,
        created: CreatedForDispatch,
        original_key: UUID,
        content: str,
        expected: DispatchBinding,
    ) -> PreparedTravelDispatch | None:
        """Apply an allocation independently held by the trusted fixture composition."""
        self.journal._registration()
        legacy = self.journal.legacy
        with legacy.session.begin():
            self.journal._principal()
        conversation = created.conversation
        if (
            legacy.reserve_preview(
                trip_id=trip_id,
                conversation_id=conversation,
                original_key=original_key,
                content=content,
            )
            != "reserved"
        ):
            return None
        try:
            if (expected.conversation, expected.original_key) != (
                str(conversation),
                str(original_key),
            ):
                raise not_found("private plan")
            with legacy.session.begin():
                authority = self.journal._authorize(
                    expected, "preview", trip_id=trip_id, pending=True
                )
                if created.receipt.conversation_id() != conversation:
                    raise not_found("private plan")
                self.journal._verify_create_artifact(expected, authority.plan, created.receipt)
                self.journal._check_continuity(expected, None)
            receipt, envelope = self.http.dispatch_preview(
                conversation, preview_body(original_key, content)
            )
            binding = expected.model_copy(
                update={"preview_receipt": digest(receipt.signature.encode("ascii"))}
            )
            return self.journal.prepare(
                trip_id=trip_id,
                binding=binding,
                create_receipt=created.receipt,
                preview_receipt=receipt,
                envelope=envelope,
            )
        except (httpx.HTTPError, ValueError, DomainError):
            legacy.record_preview_unknown(
                trip_id=trip_id, conversation_id=conversation, original_key=original_key
            )
            raise

    def confirm(
        self, *, trip_id: UUID, prepared: PreparedTravelDispatch, human_confirmation: bytes
    ) -> DispatchObservation:
        try:
            submission = self.journal.submit(
                trip_id=trip_id, prepared=prepared, human_confirmation=human_confirmation
            )
        except DispatchPreviewExpired:
            raise DomainError(
                "private_dispatch_expired", "Private plan preview expired.", status_code=410
            ) from None
        except ValueError:
            raise not_found("private plan") from None
        binding = prepared.binding
        if submission is None:
            return self.observe(trip_id=trip_id, binding=binding)
        try:
            if self.journal.check(trip_id=trip_id, binding=binding, phase="confirm") != submission:
                return DispatchObservation("unknown")
            verify_preview(
                prepared.envelope,
                binding,
                self.journal._registration().preview_role,
                self.journal.legacy.clock().timestamp(),
            )
            if digest(submission.body) != submission.body_digest:
                return DispatchObservation("unknown")
            self.http.confirm_once(UUID(binding.conversation), submission.body)
        except (httpx.HTTPError, ValueError, DomainError):
            # Unknown delivery, including post-commit revocation, consumes the marker.
            pass
        return DispatchObservation("unknown")

    def observe(self, *, trip_id: UUID, binding: DispatchBinding) -> DispatchObservation:
        submission = self.journal.check(trip_id=trip_id, binding=binding, phase="disclose")
        conversation, key = UUID(binding.conversation), UUID(binding.original_key)

        def recheck() -> bool:
            try:
                return (
                    self.journal.check(trip_id=trip_id, binding=binding, phase="disclose")
                    == submission
                )
            except DomainError as error:
                if error.status_code != 503:
                    self._quarantine(submission)
                return False

        try:
            raw = self.http.dispatch_status(conversation, key)
        except (httpx.HTTPError, PlanReceiptError):
            return DispatchObservation("unknown")
        if not recheck():
            return DispatchObservation("unknown")
        role = self.journal._registration().terminal_role
        try:
            status = verify_status(
                raw, binding, submission.body_digest, role, self.journal.legacy.clock().timestamp()
            )
        except DispatchEvidenceConflict:
            self._quarantine(submission)
            return DispatchObservation("quarantined")
        except ValueError:
            return DispatchObservation("unknown")
        if status.state != "completed":
            if status.state == "quarantined":
                self._quarantine(submission)
            return DispatchObservation(status.state)
        if not recheck():
            return DispatchObservation("unknown")
        try:
            detail_bytes = self.http.dispatch_detail(conversation, key)
        except (httpx.HTTPError, PlanReceiptError):
            return DispatchObservation("unknown")
        try:
            detail = verify_detail(detail_bytes, status)
            verify_status(
                raw, binding, submission.body_digest, role, self.journal.legacy.clock().timestamp()
            )
        except DispatchEvidenceConflict:
            self._quarantine(submission)
            return DispatchObservation("quarantined")
        except ValueError:
            return DispatchObservation("unknown")
        if not recheck():
            return DispatchObservation("unknown")
        return DispatchObservation("completed", UUID(detail.message_id), detail.result)

    def _quarantine(self, submission: _Submission) -> None:
        try:
            self.journal.quarantine(submission)
        except SQLAlchemyError:
            # The result is withheld even if the frozen owner blocks an audit update.
            pass
