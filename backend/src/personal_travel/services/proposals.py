"""Durable, owner-scoped itinerary proposal lifecycle and atomic application."""

from __future__ import annotations

import hashlib
import ipaddress
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from personal_travel.api.schemas.proposals import (
    ProposalApplyResponse,
    ProposalDetailResponse,
    ProposalGenerateRequest,
)
from personal_travel.clients.personal_ai import (
    PersonalAIClient,
    PersonalAIProposalError,
    PersonalAIProposalUnknown,
)
from personal_travel.config import Settings
from personal_travel.domain.proposals import (
    LocalProposalDraft,
    ProposalAddItem,
    ProposalMoveItem,
    ProposalRemoveItem,
    ProposalSetItemTimes,
    ProposalTripSnapshot,
)
from personal_travel.domain.upstream_proposals import (
    POLICY_VERSION,
    SCHEMA_VERSION,
    UPSTREAM_REVISION,
    ProposalCandidateContext,
    ProposalDayContext,
    ProposalItemContext,
    TravelItineraryContext,
    UpstreamProposalResult,
)
from personal_travel.domain.urls import validate_http_url
from personal_travel.models.itinerary import ItineraryItem
from personal_travel.models.place import Place
from personal_travel.models.proposal import ItineraryProposal
from personal_travel.models.trip import Trip
from personal_travel.repositories.trips import SqlAlchemyTripRepository
from personal_travel.services.errors import DomainError, not_found
from personal_travel.services.itinerary import reindex_days
from personal_travel.services.proposal_preview import (
    ProposalPreview,
    build_proposal_snapshot,
    preview_proposal,
    validate_proposal_snapshot,
)
from personal_travel.services.revisions import require_expected_revision
from personal_travel.services.time_utils import local_datetime_for_item, local_time_string

MAX_TRAVEL_PROPOSAL_REQUEST_BYTES = 48 * 1024
_SAFE_FAILURE_CODES = {
    "generation_outcome_unknown",
    "provider_unavailable",
    "provider_timeout",
    "invalid_model_output",
    "context_too_large",
    "insufficient_evidence",
    "evidence_expired",
    "uncited_evidence",
    "unsupported_request",
    "no_safe_operations",
}


@dataclass(frozen=True, slots=True)
class _Seed:
    proposal_id: UUID
    trip_id: UUID
    state: str
    created: bool
    downstream_key: UUID
    trip_handle: str
    support_mode: str
    base_snapshot: dict[str, Any]


class ProposalService:
    def __init__(
        self,
        session: Session,
        owner_id: str,
        settings: Settings,
        client: PersonalAIClient | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session = session
        self._owner_id = owner_id
        self._settings = settings
        self._client = client or PersonalAIClient(
            timeout_seconds=settings.personal_ai_proposal_timeout_seconds
        )
        self._trips = SqlAlchemyTripRepository(session)
        self._clock = clock or (lambda: datetime.now(UTC))

    async def generate(
        self,
        trip_id: UUID,
        data: ProposalGenerateRequest,
        *,
        expected_revision: int | None,
    ) -> ProposalDetailResponse:
        if not self._settings.personal_ai_proposals_enabled:
            raise DomainError(
                "proposal_unavailable",
                "Itinerary proposals are unavailable until enabled for this travel service.",
                status_code=503,
            )
        if expected_revision is None:
            raise DomainError(
                "expected_revision_required",
                "Proposal requests require the current trip revision.",
                status_code=428,
            )

        seed = await run_in_threadpool(
            self._reserve,
            trip_id,
            data,
            expected_revision,
        )
        if not seed.created:
            if seed.state == "generating":
                detail = await self.get(trip_id, seed.proposal_id)
                if detail.state == "generating":
                    raise DomainError(
                        "proposal_busy",
                        "A proposal request with this key is still running.",
                        status_code=409,
                    )
                return detail
            if seed.state == "outcome_unknown":
                return await self._reconcile(trip_id, seed)
            return await self.get(trip_id, seed.proposal_id)

        snapshot = _snapshot_from_json(seed.base_snapshot)
        context = _external_context(snapshot)
        payload: dict[str, object] = {
            "schema_version": SCHEMA_VERSION,
            "idempotency_key": str(seed.downstream_key),
            "instruction": data.instruction,
            "context": context.model_dump(mode="json"),
            "research_session_ids": [str(value) for value in data.research_session_ids],
        }
        if (
            len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            > MAX_TRAVEL_PROPOSAL_REQUEST_BYTES
        ):
            await run_in_threadpool(self._fail, trip_id, seed.proposal_id, "context_too_large")
            return await self.get(trip_id, seed.proposal_id)

        try:
            remote = await self._client.create_itinerary_proposal(
                payload=payload, idempotency_key=seed.downstream_key
            )
        except (PersonalAIProposalUnknown, PersonalAIProposalError):
            await run_in_threadpool(self._mark_unknown, trip_id, seed.proposal_id)
            detail, _ = await run_in_threadpool(self._load_detail, trip_id, seed.proposal_id)
            return detail
        await self._accept_remote(seed, remote)
        # A running result has already consumed the client's reconciliation
        # budget. Return stored state; explicit status reads can reconcile later.
        detail, _ = await run_in_threadpool(self._load_detail, trip_id, seed.proposal_id)
        return detail

    async def get(self, trip_id: UUID, proposal_id: UUID) -> ProposalDetailResponse:
        detail, seed = await run_in_threadpool(self._load_detail, trip_id, proposal_id)
        if (
            detail.state == "outcome_unknown"
            and seed is not None
            and self._settings.personal_ai_proposals_enabled
        ):
            return await self._reconcile(trip_id, seed)
        return detail

    async def get_by_key(self, trip_id: UUID, idempotency_key: UUID) -> ProposalDetailResponse:
        proposal_id = await run_in_threadpool(self._find_by_key, trip_id, idempotency_key)
        return await self.get(trip_id, proposal_id)

    async def reject(self, trip_id: UUID, proposal_id: UUID) -> ProposalDetailResponse:
        await run_in_threadpool(self._reject_tx, trip_id, proposal_id)
        return await self.get(trip_id, proposal_id)

    async def apply(
        self,
        trip_id: UUID,
        proposal_id: UUID,
        *,
        expected_revision: int | None,
    ) -> ProposalApplyResponse:
        return await run_in_threadpool(self._apply_tx, trip_id, proposal_id, expected_revision)

    def _reserve(
        self,
        trip_id: UUID,
        data: ProposalGenerateRequest,
        expected_revision: int,
    ) -> _Seed:
        now = self._now()
        fingerprint = _request_fingerprint(data, expected_revision)
        downstream_key = uuid5(
            NAMESPACE_URL,
            f"personal-travel-proposal-v1:{self._owner_id}:{trip_id}:{data.idempotency_key}",
        )
        with self._session.begin():
            trip = self._get_trip(trip_id, for_update=True)
            existing = self._session.scalar(
                select(ItineraryProposal)
                .where(
                    ItineraryProposal.owner_id == self._owner_id,
                    ItineraryProposal.trip_id == trip_id,
                    ItineraryProposal.idempotency_key == data.idempotency_key,
                )
                .with_for_update()
            )
            if existing is not None:
                if existing.request_fingerprint != fingerprint:
                    raise DomainError(
                        "idempotency_key_reused",
                        "This proposal key was already used for different request content.",
                        status_code=409,
                    )
                return _seed(existing, created=False)

            require_expected_revision(trip.revision, expected_revision, aggregate="trip")
            _lock_trip_places(self._session, trip, exclusive=False)
            trip = self._get_trip(trip_id, for_update=False)
            snapshot = build_proposal_snapshot(
                trip,
                self._owner_id,
                removable_item_ids=data.removable_item_ids,
            )
            validate_proposal_snapshot(snapshot)
            context = _external_context(snapshot)
            request_body: dict[str, object] = {
                "schema_version": SCHEMA_VERSION,
                "idempotency_key": str(downstream_key),
                "instruction": data.instruction,
                "context": context.model_dump(mode="json"),
                "research_session_ids": [str(value) for value in data.research_session_ids],
            }
            if (
                len(
                    json.dumps(request_body, ensure_ascii=False, separators=(",", ":")).encode(
                        "utf-8"
                    )
                )
                > MAX_TRAVEL_PROPOSAL_REQUEST_BYTES
            ):
                raise DomainError(
                    "proposal_context_too_large",
                    "This itinerary exceeds the bounded proposal context size.",
                    status_code=422,
                )
            footprint = [
                {"place_id": str(place.place_id), "revision": place.revision}
                for place in sorted(snapshot.places, key=lambda value: value.place_id)
            ]
            row = ItineraryProposal(
                owner_id=self._owner_id,
                trip_id=trip_id,
                idempotency_key=data.idempotency_key,
                downstream_key=downstream_key,
                request_fingerprint=fingerprint,
                state="generating",
                schema_version=SCHEMA_VERSION,
                policy_version=POLICY_VERSION,
                upstream_revision=UPSTREAM_REVISION,
                support_mode=("research_evidence" if data.research_session_ids else "context_only"),
                trip_handle=snapshot.trip_handle,
                generation_deadline=now
                + timedelta(seconds=self._settings.personal_ai_proposal_timeout_seconds),
                base_trip_revision=snapshot.trip_revision,
                base_place_revisions=footprint,
                base_snapshot=jsonable_encoder(snapshot.model_dump(mode="json")),
                citations=[],
            )
            self._session.add(row)
            self._session.flush()
            return _seed(row, created=True)

    async def _reconcile(self, trip_id: UUID, seed: _Seed) -> ProposalDetailResponse:
        try:
            remote = await self._client.get_itinerary_proposal_by_key(seed.downstream_key)
        except PersonalAIProposalError:
            return (await run_in_threadpool(self._load_detail, trip_id, seed.proposal_id))[0]
        if remote is None or remote.state == "running":
            await run_in_threadpool(self._mark_unknown, trip_id, seed.proposal_id)
        else:
            await self._accept_remote(seed, remote)
        return (await run_in_threadpool(self._load_detail, trip_id, seed.proposal_id))[0]

    async def _accept_remote(self, seed: _Seed, remote: UpstreamProposalResult) -> None:
        prepared = _prepare_result(seed, remote, self._now())
        await run_in_threadpool(self._store_prepared, seed, prepared)

    def _store_prepared(self, seed: _Seed, prepared: dict[str, Any]) -> None:
        # Save only after the network request has ended. The proposal row is
        # locked before its dependency places to keep proposal-state writers in
        # one order; no external await occurs in this transaction.
        with self._session.begin():
            self._get_trip(seed.trip_id, for_update=False)
            row = self._session.scalar(
                select(ItineraryProposal)
                .where(
                    ItineraryProposal.id == seed.proposal_id,
                    ItineraryProposal.owner_id == self._owner_id,
                    ItineraryProposal.trip_id == seed.trip_id,
                )
                .with_for_update()
            )
            if row is None:
                raise not_found("proposal")
            if row.state not in {"generating", "outcome_unknown"}:
                return
            _lock_places_by_ids(
                self._session,
                self._owner_id,
                [UUID(str(item["place_id"])) for item in seed.base_snapshot.get("places", [])],
                exclusive=False,
            )
            row.state = prepared["state"]
            row.upstream_proposal_id = prepared["upstream_proposal_id"]
            row.operations = prepared["operations"]
            row.operation_support = prepared["operation_support"]
            row.preview = prepared["preview"]
            row.citations = prepared["citations"]
            row.expires_at = prepared["expires_at"]
            row.failure_code = prepared["failure_code"]
            self._session.flush()

    def _mark_unknown(self, trip_id: UUID, proposal_id: UUID) -> None:
        with self._session.begin():
            self._get_trip(trip_id, for_update=False)
            row = self._session.scalar(
                select(ItineraryProposal)
                .where(
                    ItineraryProposal.id == proposal_id,
                    ItineraryProposal.owner_id == self._owner_id,
                    ItineraryProposal.trip_id == trip_id,
                )
                .with_for_update()
            )
            if row is not None and row.state == "generating":
                row.state = "outcome_unknown"
                self._session.flush()

    def _fail(self, trip_id: UUID, proposal_id: UUID, code: str) -> None:
        with self._session.begin():
            self._get_trip(trip_id, for_update=False)
            row = self._session.scalar(
                select(ItineraryProposal)
                .where(
                    ItineraryProposal.id == proposal_id,
                    ItineraryProposal.owner_id == self._owner_id,
                    ItineraryProposal.trip_id == trip_id,
                )
                .with_for_update()
            )
            if row is not None and row.state == "generating":
                row.state = "failed"
                row.failure_code = code
                self._session.flush()

    def _load_detail(
        self, trip_id: UUID, proposal_id: UUID
    ) -> tuple[ProposalDetailResponse, _Seed | None]:
        with self._session.begin():
            trip = self._get_trip(trip_id, for_update=False)
            row = self._session.scalar(
                select(ItineraryProposal)
                .where(
                    ItineraryProposal.id == proposal_id,
                    ItineraryProposal.owner_id == self._owner_id,
                    ItineraryProposal.trip_id == trip_id,
                )
                .with_for_update()
            )
            if row is None:
                raise not_found("proposal")
            current_places = _current_place_revisions(
                self._session,
                self._owner_id,
                row.base_place_revisions,
                exclusive=False,
            )
            now = self._now()
            if row.state == "generating" and row.generation_deadline <= now:
                row.state = "outcome_unknown"
                self._session.flush()
            stale = (
                trip.revision != row.base_trip_revision
                or current_places != row.base_place_revisions
            )
            state = row.state
            if state == "ready":
                if stale:
                    presentation = "stale"
                elif row.expires_at is not None and row.expires_at <= now:
                    presentation = "expired"
                else:
                    presentation = "ready"
            else:
                presentation = state
            detail = ProposalDetailResponse.model_validate(
                {
                    "proposal_id": row.id,
                    "state": presentation,
                    "lifecycle_state": state,
                    "support_mode": row.support_mode,
                    "upstream_revision": row.upstream_revision,
                    "trip_handle": row.trip_handle,
                    "created_at": row.created_at,
                    "expires_at": row.expires_at,
                    "base_trip_revision": row.base_trip_revision,
                    "current_trip_revision": (row.applied_outcome or {}).get("applied_revision")
                    if state == "applied"
                    else trip.revision,
                    "base_place_revisions": row.base_place_revisions,
                    "current_place_revisions": current_places,
                    "operations": row.operations or [],
                    "operation_support": row.operation_support or [],
                    "citations": row.citations or [],
                    "preview": row.preview,
                    "applied_outcome": row.applied_outcome,
                    "failure_code": row.failure_code,
                }
            )
            seed = _seed(row, created=False) if state == "outcome_unknown" else None
            return detail, seed

    def _find_by_key(self, trip_id: UUID, idempotency_key: UUID) -> UUID:
        with self._session.begin():
            self._get_trip(trip_id, for_update=False)
            proposal_id = self._session.scalar(
                select(ItineraryProposal.id).where(
                    ItineraryProposal.owner_id == self._owner_id,
                    ItineraryProposal.trip_id == trip_id,
                    ItineraryProposal.idempotency_key == idempotency_key,
                )
            )
            if proposal_id is None:
                raise not_found("proposal")
            return proposal_id

    def _reject_tx(self, trip_id: UUID, proposal_id: UUID) -> None:
        with self._session.begin():
            self._get_trip(trip_id, for_update=True)
            row = self._session.scalar(
                select(ItineraryProposal)
                .where(
                    ItineraryProposal.id == proposal_id,
                    ItineraryProposal.owner_id == self._owner_id,
                    ItineraryProposal.trip_id == trip_id,
                )
                .with_for_update()
            )
            if row is None:
                raise not_found("proposal")
            if row.state == "rejected":
                return
            if row.state == "applied":
                raise DomainError(
                    "proposal_already_applied",
                    "An applied proposal cannot be rejected.",
                    status_code=409,
                )
            if row.state != "ready":
                raise DomainError(
                    "proposal_not_rejectable",
                    "Only a ready proposal can be rejected.",
                    status_code=409,
                )
            row.state = "rejected"
            row.rejected_at = self._now()
            self._session.flush()

    def _apply_tx(
        self, trip_id: UUID, proposal_id: UUID, expected_revision: int | None
    ) -> ProposalApplyResponse:
        with self._session.begin():
            # The trip root is always the first application lock; place rows
            # follow in UUID order after the base footprint is known.
            trip = self._get_trip(trip_id, for_update=True)
            row = self._session.scalar(
                select(ItineraryProposal)
                .where(
                    ItineraryProposal.id == proposal_id,
                    ItineraryProposal.owner_id == self._owner_id,
                    ItineraryProposal.trip_id == trip_id,
                )
                .with_for_update()
            )
            if row is None:
                raise not_found("proposal")
            if row.state == "applied" and row.applied_outcome is not None:
                return ProposalApplyResponse.model_validate(row.applied_outcome)
            if row.state != "ready":
                code = "proposal_rejected" if row.state == "rejected" else "proposal_not_ready"
                raise DomainError(
                    code, "This proposal cannot be applied in its current state.", status_code=409
                )
            if expected_revision is None:
                raise DomainError(
                    "expected_revision_required",
                    "Applying a proposal requires the current trip revision.",
                    status_code=428,
                )
            require_expected_revision(trip.revision, expected_revision, aggregate="trip")
            actual_place_revisions = _current_place_revisions(
                self._session,
                self._owner_id,
                row.base_place_revisions,
                exclusive=True,
            )
            if (
                trip.revision != row.base_trip_revision
                or actual_place_revisions != row.base_place_revisions
            ):
                raise DomainError(
                    "stale_proposal",
                    "Trip or place data changed after this proposal was created.",
                    status_code=409,
                    details={
                        "base_trip_revision": row.base_trip_revision,
                        "current_trip_revision": trip.revision,
                    },
                )
            snapshot = _snapshot_from_json(row.base_snapshot)
            if snapshot.trip_id != trip.id or snapshot.owner_id != self._owner_id:
                raise DomainError(
                    "invalid_proposal_context", "Proposal context is invalid.", status_code=409
                )
            draft = _draft_from_json(row.trip_handle, row.operations or [])
            try:
                computed = preview_proposal(snapshot, draft)
            except DomainError as error:
                raise DomainError(
                    "invalid_proposal",
                    "The stored proposal no longer passes validation.",
                    status_code=409,
                ) from error
            preview_json = _preview_json(computed, draft, snapshot)
            if preview_json != row.preview:
                raise DomainError(
                    "invalid_proposal",
                    "The stored preview failed integrity validation.",
                    status_code=409,
                )
            now = self._now()
            if row.expires_at is None or row.expires_at <= now:
                raise DomainError("proposal_expired", "This proposal has expired.", status_code=409)
            changed = computed.before != computed.after
            _apply_operations(self._session, trip, snapshot, draft, self._owner_id)
            if changed:
                trip.revision += 1
            self._session.flush()
            outcome = {
                "proposal_id": str(row.id),
                "state": "applied",
                "applied_revision": trip.revision,
                "applied_at": now.isoformat(),
                "preview": row.preview,
            }
            row.state = "applied"
            row.applied_at = now
            row.applied_outcome = outcome
            self._session.flush()
            return ProposalApplyResponse.model_validate(outcome)

    def _get_trip(self, trip_id: UUID, *, for_update: bool) -> Trip:
        trip = self._trips.get(owner_id=self._owner_id, trip_id=trip_id, for_update=for_update)
        if trip is None:
            raise not_found("trip")
        return trip

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("proposal clock must return an aware datetime")
        return value.astimezone(UTC)


def _seed(row: ItineraryProposal, *, created: bool) -> _Seed:
    return _Seed(
        proposal_id=row.id,
        trip_id=row.trip_id,
        state=row.state,
        created=created,
        downstream_key=row.downstream_key,
        trip_handle=row.trip_handle,
        support_mode=row.support_mode,
        base_snapshot=row.base_snapshot,
    )


def _request_fingerprint(data: ProposalGenerateRequest, expected_revision: int) -> str:
    normalized = {
        "instruction": data.instruction,
        "removable_item_ids": [str(value) for value in data.removable_item_ids],
        "research_session_ids": [str(value) for value in data.research_session_ids],
        "expected_revision": expected_revision,
    }
    return hashlib.sha256(
        json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _snapshot_from_json(value: dict[str, Any]) -> ProposalTripSnapshot:
    return ProposalTripSnapshot.model_validate_json(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    )


def _draft_from_json(trip_handle: str, operations: list[dict[str, Any]]) -> LocalProposalDraft:
    payload = {
        "schema_version": "travel-itinerary-patch-local-v1",
        "trip_handle": trip_handle,
        "operations": operations,
    }
    return LocalProposalDraft.model_validate_json(json.dumps(payload, separators=(",", ":")))


def _external_context(snapshot: ProposalTripSnapshot) -> TravelItineraryContext:
    reservations = {reservation.handle: reservation for reservation in snapshot.reservations}
    places = {place.handle: place for place in snapshot.places}
    removable = set(snapshot.removable_item_handles)
    days: list[ProposalDayContext] = []
    for day in snapshot.days:
        projected_items = []
        for item in day.items:
            confirmed_anchor = (
                item.reservation_handle is not None
                and reservations[item.reservation_handle].status == "confirmed"
            )
            protected = item.status in {"booked", "completed"} or confirmed_anchor
            projected_items.append(
                ProposalItemContext(
                    handle=item.handle,
                    label=item.title,
                    item_type=item.item_type,
                    status=item.status,
                    start_time=local_time_string(item.starts_at, snapshot.timezone),
                    end_time=local_time_string(item.ends_at, snapshot.timezone),
                    protected=protected,
                    removable=item.handle in removable,
                )
            )
        days.append(
            ProposalDayContext(
                handle=day.handle,
                day_index=day.day_index,
                date=day.date,
                items=tuple(projected_items),
            )
        )
    candidate_context = tuple(
        ProposalCandidateContext(
            handle=candidate.handle,
            label=places[candidate.place_handle].name,
        )
        for candidate in snapshot.candidates
    )
    return TravelItineraryContext(
        schema_version="travel-itinerary-context-v1",
        trip_handle=snapshot.trip_handle,
        title=snapshot.title,
        start_date=snapshot.start_date,
        end_date=snapshot.end_date,
        timezone=snapshot.timezone,
        days=tuple(days),
        candidates=candidate_context,
        removable_item_handles=snapshot.removable_item_handles,
    )


def _prepare_result(seed: _Seed, remote: UpstreamProposalResult, now: datetime) -> dict[str, Any]:
    if remote.trip_handle != seed.trip_handle:
        return _failed_result(remote, "invalid_model_output")
    if remote.support_mode != seed.support_mode:
        return _failed_result(remote, "invalid_model_output")
    if remote.state == "running":
        return {
            "state": "outcome_unknown",
            "upstream_proposal_id": remote.proposal_id,
            "operations": None,
            "operation_support": [],
            "preview": None,
            "citations": [],
            "expires_at": remote.expires_at,
            "failure_code": None,
        }
    safe_failure = remote.failure_code if remote.failure_code in _SAFE_FAILURE_CODES else None
    if remote.state != "proposed":
        return _failed_result(remote, safe_failure or "invalid_model_output")
    if remote.expires_at <= now:
        return _failed_result(remote, "evidence_expired", expires_at=remote.expires_at)
    if remote.expires_at > remote.created_at + timedelta(hours=24):
        return _failed_result(remote, "invalid_model_output")
    try:
        for citation in remote.citations:
            _validate_public_url(citation.url)
        snapshot = _snapshot_from_json(seed.base_snapshot)
        operations = [
            operation.model_dump(mode="json", exclude_unset=True) for operation in remote.operations
        ]
        draft = _draft_from_json(seed.trip_handle, operations)
        preview = preview_proposal(snapshot, draft)
        if preview.before == preview.after:
            return _failed_result(remote, "no_safe_operations")
        preview_value = _preview_json(preview, draft, snapshot)
        citations = [citation.model_dump(mode="json") for citation in remote.citations]
    except (DomainError, ValueError, TypeError, KeyError):
        return _failed_result(remote, "invalid_model_output")
    return {
        "state": "ready",
        "upstream_proposal_id": remote.proposal_id,
        "operations": operations,
        "operation_support": [item.model_dump(mode="json") for item in remote.operation_support],
        "preview": preview_value,
        "citations": citations,
        "expires_at": remote.expires_at,
        "failure_code": None,
    }


def _failed_result(
    remote: UpstreamProposalResult,
    code: str,
    *,
    expires_at: datetime | None = None,
) -> dict[str, Any]:
    return {
        "state": "failed",
        "upstream_proposal_id": remote.proposal_id,
        "operations": None,
        "operation_support": [],
        "preview": None,
        "citations": [],
        "expires_at": expires_at or remote.expires_at,
        "failure_code": code,
    }


def _validate_public_url(value: str) -> None:
    validate_http_url(value)
    host = (urlsplit(value).hostname or "").rstrip(".").lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".localhost"):
        raise ValueError("citation host is not public")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return
    if not address.is_global:
        raise ValueError("citation address is not public")


def _preview_json(
    preview: ProposalPreview,
    draft: LocalProposalDraft,
    snapshot: ProposalTripSnapshot,
) -> dict[str, Any]:
    value = jsonable_encoder(asdict(preview))
    before = _preview_item_map(value["before"])
    after = _preview_item_map(value["after"])
    diff: list[dict[str, Any]] = []
    for index, operation in enumerate(draft.operations):
        handle = (
            f"preview-add-{index + 1}"
            if isinstance(operation, ProposalAddItem)
            else getattr(operation, "item_handle", None)
        )
        diff.append(
            {
                "operation_index": index,
                "kind": operation.kind,
                "before": before.get(handle) if handle is not None else None,
                "after": after.get(handle) if handle is not None else None,
            }
        )
    value["diff"] = diff
    value["warnings"] = value.get("warnings", [])
    return cast(dict[str, Any], value)


def _preview_item_map(days: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    items: dict[str, dict[str, Any]] = {}
    for day in days:
        for item in day["items"]:
            items[item["handle"]] = {
                "day_index": day["day_index"],
                "date": day["date"],
                "sort_order": item["sort_order"],
                "title": item["title"],
                "item_type": item["item_type"],
                "start_time": item["start_time"],
                "end_time": item["end_time"],
            }
    return items


def _lock_trip_places(session: Session, trip: Trip, *, exclusive: bool) -> list[Place]:
    ids: set[UUID] = set()
    for day in trip.days:
        for item in day.items:
            if item.place_id:
                ids.add(item.place_id)
    ids.update(reservation.place_id for reservation in trip.reservations if reservation.place_id)
    ids.update(candidate.place_id for candidate in trip.saved_places)
    return _lock_places_by_ids(session, trip.owner_id, ids, exclusive=exclusive)


def _lock_places_by_ids(
    session: Session, owner_id: str, place_ids: Any, *, exclusive: bool
) -> list[Place]:
    ids = sorted(set(place_ids))
    if not ids:
        return []
    statement = (
        select(Place)
        .where(Place.owner_id == owner_id, Place.id.in_(ids))
        .order_by(Place.id)
        .execution_options(populate_existing=True)
        .with_for_update(read=not exclusive)
    )
    return list(session.scalars(statement).all())


def _current_place_revisions(
    session: Session,
    owner_id: str,
    footprint: list[dict[str, Any]],
    *,
    exclusive: bool,
) -> list[dict[str, Any]]:
    ids = [UUID(str(item["place_id"])) for item in footprint]
    places = _lock_places_by_ids(session, owner_id, ids, exclusive=exclusive)
    revisions = {str(place.id): place.revision for place in places}
    return [
        {"place_id": str(item["place_id"]), "revision": revisions.get(str(item["place_id"]))}
        for item in sorted(footprint, key=lambda value: str(value["place_id"]))
    ]


def _apply_operations(
    session: Session,
    trip: Trip,
    snapshot: ProposalTripSnapshot,
    draft: LocalProposalDraft,
    owner_id: str,
) -> None:
    days_by_id = {day.id: day for day in trip.days}
    snapshot_days = {day.handle: day for day in snapshot.days}
    snapshot_items = {item.handle: item for day in snapshot.days for item in day.items}
    snapshot_candidates = {candidate.handle: candidate for candidate in snapshot.candidates}
    saved_places = {candidate.id: candidate for candidate in trip.saved_places}
    places = {place.id: place for place in _lock_trip_places(session, trip, exclusive=False)}
    item_ids = {item.id: item for day in trip.days for item in day.items}
    item_handles = {
        item_snapshot.handle: item_ids[item_snapshot.item_id]
        for item_snapshot in snapshot_items.values()
    }
    removable = set(snapshot.removable_item_handles)

    for index, operation in enumerate(draft.operations):
        if isinstance(operation, ProposalAddItem):
            day_snapshot = snapshot_days.get(operation.day_handle)
            candidate_snapshot = snapshot_candidates.get(operation.candidate_handle)
            if day_snapshot is None or candidate_snapshot is None:
                raise DomainError(
                    "invalid_proposal", "A proposal reference is no longer valid.", status_code=409
                )
            day = days_by_id[day_snapshot.day_id]
            saved = saved_places.get(candidate_snapshot.candidate_id)
            if saved is None or saved.owner_id != owner_id or saved.trip_id != trip.id:
                raise DomainError(
                    "invalid_proposal", "A saved candidate is no longer available.", status_code=409
                )
            place = places.get(saved.place_id)
            if place is None or place.owner_id != owner_id:
                raise DomainError(
                    "invalid_proposal", "A candidate place is no longer available.", status_code=409
                )
            if operation.position > len(day.items):
                raise DomainError(
                    "invalid_proposal", "An insertion position is invalid.", status_code=409
                )
            starts_at, ends_at = local_datetime_for_item(
                day.date, operation.start_time, operation.end_time, trip.timezone
            )
            item = ItineraryItem(
                id=uuid4(),
                item_type=operation.item_type,
                title=place.name,
                notes=None,
                starts_at=starts_at,
                ends_at=ends_at,
                sort_order=len(day.items),
                status="tentative",
                place=place,
            )
            day.items.append(item)
            day.items.insert(operation.position, day.items.pop())
            session.add(item)
            reindex_days(session, day)
            item_handles[f"preview-add-{index + 1}"] = item
            continue

        target_item = item_handles.get(operation.item_handle)
        if target_item is None:
            raise DomainError(
                "invalid_proposal", "An itinerary item is no longer available.", status_code=409
            )
        source = target_item.trip_day
        if target_item.status in {"booked", "completed"} or (
            target_item.reservation is not None and target_item.reservation.status == "confirmed"
        ):
            raise DomainError(
                "protected_item_anchor",
                "A protected itinerary item cannot be changed.",
                status_code=409,
            )

        if isinstance(operation, ProposalMoveItem):
            target_snapshot = snapshot_days.get(operation.day_handle)
            if target_snapshot is None:
                raise DomainError(
                    "invalid_proposal", "A destination day is unavailable.", status_code=409
                )
            target = days_by_id[target_snapshot.day_id]
            if source is not target:
                starts_at, ends_at = local_datetime_for_item(
                    target.date,
                    local_time_string(target_item.starts_at, trip.timezone),
                    local_time_string(target_item.ends_at, trip.timezone),
                    trip.timezone,
                )
            else:
                starts_at, ends_at = target_item.starts_at, target_item.ends_at
            if source is target:
                source.items.remove(target_item)
                remaining = source.items
                if operation.position > len(remaining):
                    raise DomainError(
                        "invalid_proposal", "A move position is invalid.", status_code=409
                    )
                target_item.starts_at, target_item.ends_at = starts_at, ends_at
                source.items.insert(operation.position, target_item)
                reindex_days(session, source)
            else:
                source.items.remove(target_item)
                if operation.position > len(target.items):
                    raise DomainError(
                        "invalid_proposal", "A move position is invalid.", status_code=409
                    )
                target_item.starts_at, target_item.ends_at = starts_at, ends_at
                target.items.insert(operation.position, target_item)
                reindex_days(session, source, target)
            continue

        if isinstance(operation, ProposalSetItemTimes):
            item_snapshot = snapshot_items.get(operation.item_handle)
            if item_snapshot is None:
                raise DomainError(
                    "invalid_proposal", "An itinerary item is unavailable.", status_code=409
                )
            day = target_item.trip_day
            current_start = local_time_string(target_item.starts_at, trip.timezone)
            current_end = local_time_string(target_item.ends_at, trip.timezone)
            fields = operation.model_fields_set
            start_value = operation.start_time if "start_time" in fields else current_start
            end_value = operation.end_time if "end_time" in fields else current_end
            target_item.starts_at, target_item.ends_at = local_datetime_for_item(
                day.date, start_value, end_value, trip.timezone
            )
            session.flush()
            continue

        if isinstance(operation, ProposalRemoveItem):
            if operation.item_handle not in removable or target_item.reservation_id is not None:
                raise DomainError(
                    "item_not_removable",
                    "This itinerary item is protected from removal.",
                    status_code=409,
                )
            day = target_item.trip_day
            day.items.remove(target_item)
            session.delete(target_item)
            session.flush()
            reindex_days(session, day)
            continue

        raise DomainError(
            "unsupported_operation", "The proposal operation is unsupported.", status_code=409
        )
