"""Travel-owned private plan authority and a preview-only, original-key journal."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Literal
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from personal_travel.api.schemas.private_plan import PRIVATE_PLAN_LANE
from personal_travel.auth.contracts import VerifiedPrincipal
from personal_travel.auth.private_plan import PrivatePlanTrust
from personal_travel.clients.private_plan import (
    PLAN_PATH,
    PlanPreview,
    PlanReceipt,
    PlanReceiptError,
    PlanReceiptRegistration,
    PlanStatus,
    PrivatePlanHTTP,
    create_body,
    preview_body,
)
from personal_travel.config import Settings
from personal_travel.models.auth import AuthIdentity
from personal_travel.models.owner_erasure import OwnerErasureLedger
from personal_travel.models.private_plan import PrivatePlanBinding, PrivatePlanIntent
from personal_travel.models.sidecar_anchor import PersonalAISidecarAnchor
from personal_travel.models.trip import Trip
from personal_travel.services.errors import DomainError, not_found
from personal_travel.services.trips import TripService


def active_plan_identity(
    session: Session, *, issuer: str, subject: str, expected_owner: str | None = None
) -> AuthIdentity:
    identity = session.scalar(
        select(AuthIdentity)
        .where(
            AuthIdentity.issuer == issuer,
            AuthIdentity.subject == subject,
            AuthIdentity.status == "active",
        )
        .with_for_update(read=True)
    )
    if (
        identity is None
        or (expected_owner is not None and identity.owner_id != expected_owner)
        or session.get(OwnerErasureLedger, identity.owner_id) is not None
    ):
        raise not_found("private plan")
    return identity


def resolve_plan_trip(
    session: Session,
    *,
    identity: AuthIdentity,
    trust: PrivatePlanTrust,
    conversation_id: UUID,
    expected_trip_id: UUID | None = None,
    lock: bool = False,
) -> tuple[PrivatePlanBinding, Trip]:
    query = (
        select(PrivatePlanBinding)
        .join(
            PersonalAISidecarAnchor,
            PersonalAISidecarAnchor.conversation_id == PrivatePlanBinding.conversation_id,
        )
        .where(
            PrivatePlanBinding.conversation_id == conversation_id,
            PrivatePlanBinding.create_receipt_digest.is_not(None),
            PrivatePlanBinding.owner_id == identity.owner_id,
            PrivatePlanBinding.issuer == identity.issuer,
            PrivatePlanBinding.subject == identity.subject,
            PrivatePlanBinding.target_id == trust.target_id,
            PrivatePlanBinding.client_namespace == trust.client_namespace,
            PersonalAISidecarAnchor.lane == PRIVATE_PLAN_LANE,
            PersonalAISidecarAnchor.owner_id == PrivatePlanBinding.owner_id,
            PersonalAISidecarAnchor.trip_id == PrivatePlanBinding.trip_id,
        )
    )
    if lock:
        query = query.with_for_update()
    binding = session.scalar(query)
    if binding is None or (expected_trip_id is not None and binding.trip_id != expected_trip_id):
        raise not_found("private plan")
    if lock:
        trip = session.scalar(
            select(Trip)
            .where(Trip.id == binding.trip_id, Trip.owner_id == identity.owner_id)
            .with_for_update()
        )
        if trip is None:
            raise not_found("private plan")
        return binding, trip
    return binding, TripService(session, identity.owner_id).get(binding.trip_id)


def _plan_conflict() -> DomainError:
    return DomainError(
        "private_plan_conflict",
        "An existing private plan uses a different selection or request.",
        status_code=409,
    )


@dataclass(frozen=True, slots=True)
class ReservedPlanCreate:
    create_key: UUID
    title: str
    conversation_id: UUID | None
    newly_reserved: bool


@dataclass(frozen=True, slots=True)
class PlanRecovery:
    """Opaque status-only selector from a current, exact original-key lookup."""

    conversation_id: UUID
    original_key: UUID


class PrivatePlanJournal:
    """Internal operations for a verified caller. Each commit precedes transport use."""

    def __init__(
        self,
        session: Session,
        *,
        settings: Settings,
        trust: PrivatePlanTrust,
        principal: VerifiedPrincipal,
        receipts: PlanReceiptRegistration | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.session = session
        self.settings = settings
        self.trust = trust
        self.principal = principal
        self.clock = clock
        self.receipts = receipts

    def _identity(self) -> AuthIdentity:
        principal = self.principal
        if (
            not self.settings.personal_ai_plan_enabled
            or self.settings.deployment_mode != "local"
            or principal.issuer != self.trust.human_issuer
            or principal.expires_at <= self.clock().timestamp()
        ):
            raise not_found("private plan")
        return active_plan_identity(
            self.session,
            issuer=principal.issuer,
            subject=principal.subject,
            expected_owner=principal.owner_id,
        )

    def _create_for_trip(
        self, *, identity: AuthIdentity, trip_id: UUID, lock: bool = False
    ) -> PrivatePlanBinding | None:
        query = select(PrivatePlanBinding).where(
            PrivatePlanBinding.owner_id == identity.owner_id,
            PrivatePlanBinding.trip_id == trip_id,
            PrivatePlanBinding.target_id == self.trust.target_id,
            PrivatePlanBinding.client_namespace == self.trust.client_namespace,
        )
        if lock:
            query = query.with_for_update()
        binding = self.session.scalar(query)
        if binding is not None and (binding.issuer, binding.subject) != (
            identity.issuer,
            identity.subject,
        ):
            raise not_found("private plan")
        return binding

    def reserve_create(self, *, trip_id: UUID, create_key: UUID, title: str) -> ReservedPlanCreate:
        """Reserve the exact idempotent create before a caller can make its first POST."""
        title.encode("utf-8")
        if not 1 <= len(title) <= 200 or not title.strip():
            raise ValueError("private_plan_title_invalid")
        try:
            with self.session.begin():
                identity = self._identity()
                TripService(self.session, identity.owner_id).get(trip_id)
                binding = self._create_for_trip(identity=identity, trip_id=trip_id)
                if binding is None:
                    self.session.add(
                        PrivatePlanBinding(
                            create_key=create_key,
                            title=title,
                            owner_id=identity.owner_id,
                            trip_id=trip_id,
                            issuer=identity.issuer,
                            subject=identity.subject,
                            target_id=self.trust.target_id,
                            client_namespace=self.trust.client_namespace,
                        )
                    )
                    return ReservedPlanCreate(create_key, title, None, True)
                if (binding.create_key, binding.title) == (create_key, title):
                    return self._existing_create(identity, binding)
        except IntegrityError:
            # A concurrent tab may have inserted the same original request.
            with self.session.begin():
                identity = self._identity()
                TripService(self.session, identity.owner_id).get(trip_id)
                binding = self._create_for_trip(identity=identity, trip_id=trip_id)
                if binding is not None and (binding.create_key, binding.title) == (
                    create_key,
                    title,
                ):
                    return self._existing_create(identity, binding)
        raise _plan_conflict()

    def _existing_create(
        self, identity: AuthIdentity, binding: PrivatePlanBinding
    ) -> ReservedPlanCreate:
        if binding.conversation_id is not None:
            resolve_plan_trip(
                self.session,
                identity=identity,
                trust=self.trust,
                conversation_id=binding.conversation_id,
                expected_trip_id=binding.trip_id,
            )
        return ReservedPlanCreate(binding.create_key, binding.title, binding.conversation_id, False)

    def _verify_receipt(
        self,
        receipt: PlanReceipt,
        binding: PrivatePlanBinding,
        *,
        operation: Literal["create", "preview"],
        conversation_id: UUID,
        request_digest: str,
        source_revision: int | None = None,
    ) -> PlanReceiptRegistration:
        if self.receipts is None or type(receipt) is not PlanReceipt:
            raise not_found("private plan")
        receipt.verify(
            self.receipts,
            operation=operation,
            target_id=binding.target_id,
            client_namespace=binding.client_namespace,
            owner_id=binding.owner_id,
            issuer=binding.issuer,
            subject=binding.subject,
            conversation_id=conversation_id,
            request_digest=request_digest,
            source_revision=source_revision,
        )
        return self.receipts

    def bind_created_conversation(
        self, *, trip_id: UUID, create_key: UUID, receipt: PlanReceipt
    ) -> UUID:
        """Verify the signed response against the exact original create reservation."""
        try:
            with self.session.begin():
                identity = self._identity()
                TripService(self.session, identity.owner_id).get(trip_id)
                binding = self._create_for_trip(identity=identity, trip_id=trip_id, lock=True)
                if binding is None or binding.create_key != create_key:
                    raise not_found("private plan")
                if type(receipt) is not PlanReceipt:
                    raise not_found("private plan")
                conversation_id = receipt.conversation_id()
                self._verify_receipt(
                    receipt,
                    binding,
                    operation="create",
                    conversation_id=conversation_id,
                    request_digest=sha256(
                        create_body(binding.create_key, binding.title)
                    ).hexdigest(),
                )
                if binding.conversation_id is not None:
                    if binding.conversation_id != conversation_id:
                        raise _plan_conflict()
                    self._existing_create(identity, binding)
                    return conversation_id
                self.session.add(
                    PersonalAISidecarAnchor(
                        conversation_id=conversation_id,
                        owner_id=identity.owner_id,
                        trip_id=trip_id,
                        lane=PRIVATE_PLAN_LANE,
                    )
                )
                self.session.flush()
                binding.conversation_id = conversation_id
                binding.create_receipt_digest = sha256(bytes.fromhex(receipt.signature)).hexdigest()
                return conversation_id
        except PlanReceiptError:
            raise not_found("private plan") from None
        except IntegrityError:
            # The shared anchor key fences a conversation already in either lane.
            raise _plan_conflict() from None

    def _bound_trip(
        self, *, trip_id: UUID, conversation_id: UUID
    ) -> tuple[PrivatePlanBinding, Trip]:
        return resolve_plan_trip(
            self.session,
            identity=self._identity(),
            trust=self.trust,
            conversation_id=conversation_id,
            expected_trip_id=trip_id,
        )

    def _intent(
        self, binding: PrivatePlanBinding, *, lock: bool = False
    ) -> PrivatePlanIntent | None:
        query = select(PrivatePlanIntent).where(
            PrivatePlanIntent.binding_id == binding.id,
            PrivatePlanIntent.owner_id == binding.owner_id,
        )
        if lock:
            query = query.with_for_update()
        return self.session.scalar(query)

    def reserve_preview(
        self, *, trip_id: UUID, conversation_id: UUID, original_key: UUID, content: str
    ) -> Literal["reserved", "join", "reconcile_only"]:
        """Save a digest of the exact PAS preview intent before any preview POST."""
        if not 1 <= len(content) <= 20_000:
            raise ValueError("private_plan_content_invalid")
        digest = sha256(preview_body(original_key, content)).hexdigest()
        try:
            with self.session.begin():
                binding, trip = self._bound_trip(trip_id=trip_id, conversation_id=conversation_id)
                intent = self._intent(binding)
                if intent is None:
                    self.session.add(
                        PrivatePlanIntent(
                            original_key=original_key,
                            binding_id=binding.id,
                            owner_id=binding.owner_id,
                            request_digest=digest,
                            source_revision=trip.revision,
                            state="preview_pending",
                        )
                    )
                    return "reserved"
                return self._joined_preview(intent, original_key, digest, trip.revision)
        except IntegrityError:
            with self.session.begin():
                binding, trip = self._bound_trip(trip_id=trip_id, conversation_id=conversation_id)
                intent = self._intent(binding)
                if intent is None:
                    raise _plan_conflict() from None
                return self._joined_preview(intent, original_key, digest, trip.revision)

    @staticmethod
    def _joined_preview(
        intent: PrivatePlanIntent, original_key: UUID, digest: str, revision: int
    ) -> Literal["join", "reconcile_only"]:
        if (intent.original_key, intent.request_digest, intent.source_revision) != (
            original_key,
            digest,
            revision,
        ):
            raise _plan_conflict()
        if intent.state == "preview_unknown":
            return "reconcile_only"
        return "join"

    def record_preview(
        self,
        *,
        trip_id: UUID,
        conversation_id: UUID,
        original_key: UUID,
        receipt: PlanReceipt,
    ) -> PlanPreview:
        """Only the scoped signed transaction can move a pending intent to ready."""
        try:
            with self.session.begin():
                binding, trip = self._bound_trip(trip_id=trip_id, conversation_id=conversation_id)
                intent = self._intent(binding, lock=True)
                if intent is None or intent.original_key != original_key:
                    raise not_found("private plan")
                if intent.source_revision != trip.revision or intent.state != "preview_pending":
                    raise _plan_conflict()
                registration = self._verify_receipt(
                    receipt,
                    binding,
                    operation="preview",
                    conversation_id=conversation_id,
                    request_digest=intent.request_digest,
                    source_revision=intent.source_revision,
                )
                preview, expiry = receipt.preview(
                    destination=registration.destination, now=self.clock()
                )
                intent.preview_digest = sha256(receipt.body).hexdigest()
                intent.preview_expires_at = expiry
                intent.state = "preview_ready"
                return preview
        except PlanReceiptError:
            raise not_found("private plan") from None

    def record_preview_unknown(
        self, *, trip_id: UUID, conversation_id: UUID, original_key: UUID
    ) -> None:
        with self.session.begin():
            binding, _ = self._bound_trip(trip_id=trip_id, conversation_id=conversation_id)
            intent = self._intent(binding, lock=True)
            if intent is None or intent.original_key != original_key:
                raise not_found("private plan")
            if intent.state == "preview_pending":
                intent.state = "preview_unknown"

    def original_status(
        self, *, trip_id: UUID, conversation_id: UUID, original_key: UUID
    ) -> PlanRecovery | None:
        """Return only the trusted GET selector. Missing or stale evidence quarantines it."""
        with self.session.begin():
            binding, trip = self._bound_trip(trip_id=trip_id, conversation_id=conversation_id)
            intent = self._intent(binding)
            if intent is None or (intent.original_key, intent.source_revision) != (
                original_key,
                trip.revision,
            ):
                return None
            return PlanRecovery(conversation_id, intent.original_key)


class PrivatePlanCaller:
    """Orchestrate isolated create, preview and status-only reconciliation."""

    def __init__(self, journal: PrivatePlanJournal, http: PrivatePlanHTTP) -> None:
        if journal.receipts is None:
            raise not_found("private plan")
        self.journal = journal
        self.http = http

    def create(self, *, trip_id: UUID, create_key: UUID, title: str) -> UUID:
        reservation = self.journal.reserve_create(
            trip_id=trip_id, create_key=create_key, title=title
        )
        if reservation.conversation_id is not None:
            return reservation.conversation_id
        receipt = self.http.receipt(PLAN_PATH, create_body(create_key, title), status=201)
        return self.journal.bind_created_conversation(
            trip_id=trip_id, create_key=create_key, receipt=receipt
        )

    def preview(
        self, *, trip_id: UUID, conversation_id: UUID, original_key: UUID, content: str
    ) -> PlanPreview | None:
        selector = dict(trip_id=trip_id, conversation_id=conversation_id, original_key=original_key)
        if self.journal.reserve_preview(**selector, content=content) != "reserved":
            return None
        try:
            receipt = self.http.receipt(
                f"{PLAN_PATH}/{conversation_id}/context-preview",
                preview_body(original_key, content),
                status=200,
            )
            return self.journal.record_preview(**selector, receipt=receipt)
        except (httpx.HTTPError, PlanReceiptError, DomainError):
            self.journal.record_preview_unknown(**selector)
            raise

    def inspect_original_status(
        self, *, trip_id: UUID, conversation_id: UUID, original_key: UUID
    ) -> PlanStatus | None:
        scope = dict(trip_id=trip_id, conversation_id=conversation_id, original_key=original_key)
        selector = self.journal.original_status(**scope)
        if selector is None:
            return None
        state = self.http.original_status(selector.conversation_id, selector.original_key)
        if self.journal.original_status(**scope) != selector:
            return None
        return state
