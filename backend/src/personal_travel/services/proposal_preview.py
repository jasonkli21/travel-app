"""Bounded, immutable local preview groundwork for future typed proposals.

No route or PersonalAIClient method consumes these DTOs. A later integration
must first accept and pin its upstream HTTP contract.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from secrets import token_urlsafe
from typing import cast
from uuid import UUID

from personal_travel.domain.proposals import (
    LocalProposalDraft,
    ProposalAddItem,
    ProposalCandidateSnapshot,
    ProposalDaySnapshot,
    ProposalItemSnapshot,
    ProposalMoveItem,
    ProposalPlaceSnapshot,
    ProposalRemoveItem,
    ProposalReservationSnapshot,
    ProposalSetItemTimes,
    ProposalTripSnapshot,
)
from personal_travel.domain.types import ItemStatus, ItemType, ReservationStatus
from personal_travel.models.place import Place
from personal_travel.models.trip import Trip
from personal_travel.services.conflicts import intervals_overlap, schedule_bounds
from personal_travel.services.errors import DomainError, not_found
from personal_travel.services.time_utils import (
    as_aware_utc,
    get_zoneinfo,
    local_date_time_parts,
    local_datetime_for_item,
    local_time_string,
)
from personal_travel.services.trips import dates_between, validate_trip_range

MAX_PREVIEW_DAYS = 366
MAX_PREVIEW_ITEMS = 5000
MAX_PREVIEW_PLACES = 1000
MAX_PREVIEW_CANDIDATES = 500
MAX_PREVIEW_RESERVATIONS = 500
MAX_PROPOSAL_OPERATIONS = 25


@dataclass(frozen=True, slots=True)
class PlaceRevision:
    place_id: UUID
    handle: str
    revision: int


@dataclass(frozen=True, slots=True)
class PreviewItem:
    handle: str
    title: str
    item_type: str
    status: str
    sort_order: int
    start_time: str | None
    end_time: str | None
    place_handle: str | None
    reservation_handle: str | None


@dataclass(frozen=True, slots=True)
class PreviewDay:
    handle: str
    day_index: int
    date: date
    title: str | None
    items: tuple[PreviewItem, ...]


@dataclass(frozen=True, slots=True)
class PreviewWarning:
    code: str
    day_handle: str
    item_handle: str
    reservation_handle: str
    message: str


@dataclass(frozen=True, slots=True)
class ProposalPreview:
    trip_handle: str
    base_trip_revision: int
    place_revisions: tuple[PlaceRevision, ...]
    operation_count: int
    before: tuple[PreviewDay, ...]
    after: tuple[PreviewDay, ...]
    warnings: tuple[PreviewWarning, ...]


@dataclass(slots=True)
class _WorkingItem:
    handle: str
    title: str
    item_type: str
    status: str
    starts_at: datetime | None
    ends_at: datetime | None
    place_handle: str | None
    reservation_handle: str | None


def _new_handle() -> str:
    return f"h_{token_urlsafe(24)}"


def _invalid_context(message: str = "The proposal context is inconsistent.") -> DomainError:
    return DomainError("invalid_proposal_context", message, status_code=422)


def _invalid_operation(code: str, message: str) -> DomainError:
    return DomainError(code, message, status_code=422)


def build_proposal_snapshot(
    trip: Trip,
    owner_id: str,
    *,
    removable_item_ids: Iterable[UUID] = (),
    handle_factory: Callable[[], str] = _new_handle,
) -> ProposalTripSnapshot:
    """Project an already owner-scoped ORM trip into bounded opaque handles."""
    if trip.owner_id != owner_id:
        raise not_found("trip")

    days = sorted(trip.days, key=lambda day: day.day_index)
    reservations = sorted(trip.reservations, key=lambda reservation: reservation.id)
    candidates = sorted(
        trip.saved_places,
        key=lambda candidate: (
            candidate.place.name.casefold() if candidate.place is not None else "",
            candidate.id,
        ),
    )
    items = [item for day in days for item in sorted(day.items, key=lambda item: item.sort_order)]
    if (
        len(days) > MAX_PREVIEW_DAYS
        or len(items) > MAX_PREVIEW_ITEMS
        or len(candidates) > MAX_PREVIEW_CANDIDATES
        or len(reservations) > MAX_PREVIEW_RESERVATIONS
    ):
        raise _invalid_operation(
            "proposal_context_too_large", "This trip is too large for a bounded proposal preview."
        )

    places_by_id: dict[UUID, Place] = {}
    for item in items:
        if (item.place_id is not None and item.place is None) or (
            item.reservation_id is not None and item.reservation is None
        ):
            raise _invalid_context()
        if item.place is not None:
            places_by_id[item.place.id] = item.place
    for reservation in reservations:
        if reservation.place_id is not None and reservation.place is None:
            raise _invalid_context()
        if reservation.place is not None:
            places_by_id[reservation.place.id] = reservation.place
    for candidate in candidates:
        if candidate.place_id is None or candidate.place is None:
            raise _invalid_context()
        places_by_id[candidate.place.id] = candidate.place

    reservation_ids = {reservation.id for reservation in reservations}
    if any(
        item.reservation_id is not None and item.reservation_id not in reservation_ids
        for item in items
    ):
        raise _invalid_context()

    if len(places_by_id) > MAX_PREVIEW_PLACES:
        raise _invalid_operation(
            "proposal_context_too_large", "This trip uses too many places for a bounded preview."
        )

    trip_handle = handle_factory()
    day_handles = {day.id: handle_factory() for day in days}
    item_handles = {item.id: handle_factory() for item in items}
    reservation_handles = {reservation.id: handle_factory() for reservation in reservations}
    place_handles = {place_id: handle_factory() for place_id in sorted(places_by_id)}
    candidate_handles = {candidate.id: handle_factory() for candidate in candidates}

    items_by_id = {item.id: item for item in items}
    requested_removals = set(removable_item_ids)
    if not requested_removals.issubset(items_by_id):
        raise _invalid_operation(
            "invalid_removable_item", "A selected removable item is not in this trip."
        )
    removable_handles: list[str] = []
    for item_id in sorted(requested_removals):
        item = items_by_id[item_id]
        if item.reservation_id is not None or item.status in {"booked", "completed"}:
            raise _invalid_operation(
                "protected_item_anchor", "A selected item is not eligible for removal."
            )
        removable_handles.append(item_handles[item_id])

    place_snapshots = tuple(
        ProposalPlaceSnapshot(
            place_id=place.id,
            owner_id=place.owner_id,
            handle=place_handles[place.id],
            revision=place.revision,
            name=place.name,
        )
        for place in sorted(places_by_id.values(), key=lambda place: place.id)
    )
    candidate_snapshots = tuple(
        ProposalCandidateSnapshot(
            candidate_id=candidate.id,
            owner_id=candidate.owner_id,
            trip_id=candidate.trip_id,
            handle=candidate_handles[candidate.id],
            place_handle=place_handles[candidate.place_id],
        )
        for candidate in candidates
    )
    reservation_snapshots = tuple(
        ProposalReservationSnapshot(
            reservation_id=reservation.id,
            owner_id=reservation.owner_id,
            trip_id=reservation.trip_id,
            handle=reservation_handles[reservation.id],
            status=cast(ReservationStatus, reservation.status),
            starts_at=reservation.starts_at,
            ends_at=reservation.ends_at,
            place_handle=(
                place_handles[reservation.place_id] if reservation.place_id is not None else None
            ),
        )
        for reservation in reservations
    )

    day_snapshots: list[ProposalDaySnapshot] = []
    for day in days:
        day_items = tuple(
            ProposalItemSnapshot(
                item_id=item.id,
                owner_id=owner_id,
                trip_id=trip.id,
                handle=item_handles[item.id],
                item_type=cast(ItemType, item.item_type),
                title=item.title,
                status=cast(ItemStatus, item.status),
                sort_order=item.sort_order,
                starts_at=item.starts_at,
                ends_at=item.ends_at,
                place_handle=(place_handles[item.place_id] if item.place_id is not None else None),
                reservation_handle=(
                    reservation_handles[item.reservation_id]
                    if item.reservation_id is not None
                    else None
                ),
            )
            for item in sorted(day.items, key=lambda item: item.sort_order)
        )
        day_snapshots.append(
            ProposalDaySnapshot(
                day_id=day.id,
                trip_id=trip.id,
                handle=day_handles[day.id],
                day_index=day.day_index,
                date=day.date,
                title=day.title,
                items=day_items,
            )
        )

    return ProposalTripSnapshot(
        schema_version="travel-preview-context-local-v1",
        owner_id=owner_id,
        trip_id=trip.id,
        trip_handle=trip_handle,
        trip_revision=trip.revision,
        title=trip.title,
        start_date=trip.start_date,
        end_date=trip.end_date,
        timezone=trip.timezone,
        days=tuple(day_snapshots),
        places=place_snapshots,
        candidates=candidate_snapshots,
        reservations=reservation_snapshots,
        removable_item_handles=tuple(removable_handles),
    )


def validate_proposal_snapshot(snapshot: ProposalTripSnapshot) -> None:
    """Fail closed on a malformed or cross-aggregate trusted projection."""
    try:
        validate_trip_range(snapshot.start_date, snapshot.end_date)
        get_zoneinfo(snapshot.timezone)
    except DomainError as exc:
        raise _invalid_context() from exc

    expected_dates = list(dates_between(snapshot.start_date, snapshot.end_date))
    if len(snapshot.days) != len(expected_dates):
        raise _invalid_context()
    all_handles = [snapshot.trip_handle]
    handles_by_kind: dict[str, object] = {}
    ids_by_kind: dict[str, set[UUID]] = {
        "day": set(),
        "item": set(),
        "place": set(),
        "candidate": set(),
        "reservation": set(),
    }
    place_by_handle: dict[str, ProposalPlaceSnapshot] = {}
    candidate_by_handle: dict[str, ProposalCandidateSnapshot] = {}
    reservation_by_handle: dict[str, ProposalReservationSnapshot] = {}
    item_by_handle: dict[str, tuple[ProposalDaySnapshot, ProposalItemSnapshot]] = {}
    referenced_place_handles: set[str] = set()

    for place in snapshot.places:
        if place.owner_id != snapshot.owner_id or place.place_id in ids_by_kind["place"]:
            raise _invalid_context()
        ids_by_kind["place"].add(place.place_id)
        place_by_handle[place.handle] = place
        handles_by_kind[place.handle] = place
        all_handles.append(place.handle)

    for candidate in snapshot.candidates:
        if (
            candidate.owner_id != snapshot.owner_id
            or candidate.trip_id != snapshot.trip_id
            or candidate.candidate_id in ids_by_kind["candidate"]
            or candidate.place_handle not in place_by_handle
        ):
            raise _invalid_context()
        ids_by_kind["candidate"].add(candidate.candidate_id)
        candidate_by_handle[candidate.handle] = candidate
        handles_by_kind[candidate.handle] = candidate
        all_handles.append(candidate.handle)
        referenced_place_handles.add(candidate.place_handle)

    reservation_status: dict[str, str] = {}
    for reservation in snapshot.reservations:
        if (
            reservation.owner_id != snapshot.owner_id
            or reservation.trip_id != snapshot.trip_id
            or reservation.reservation_id in ids_by_kind["reservation"]
        ):
            raise _invalid_context()
        if reservation.place_handle is not None:
            if reservation.place_handle not in place_by_handle:
                raise _invalid_context()
            referenced_place_handles.add(reservation.place_handle)
        if reservation.starts_at is None and reservation.ends_at is not None:
            raise _invalid_context()
        if reservation.starts_at is not None and reservation.ends_at is not None:
            if as_aware_utc(reservation.starts_at) > as_aware_utc(reservation.ends_at):
                raise _invalid_context()
        ids_by_kind["reservation"].add(reservation.reservation_id)
        reservation_by_handle[reservation.handle] = reservation
        reservation_status[reservation.handle] = reservation.status
        handles_by_kind[reservation.handle] = reservation
        all_handles.append(reservation.handle)

    for expected_index, (day, expected_date) in enumerate(
        zip(snapshot.days, expected_dates, strict=True), start=1
    ):
        if (
            day.trip_id != snapshot.trip_id
            or day.day_index != expected_index
            or day.date != expected_date
            or day.day_id in ids_by_kind["day"]
        ):
            raise _invalid_context()
        ids_by_kind["day"].add(day.day_id)
        handles_by_kind[day.handle] = day
        all_handles.append(day.handle)
        for expected_order, item in enumerate(day.items):
            if (
                item.owner_id != snapshot.owner_id
                or item.trip_id != snapshot.trip_id
                or item.sort_order != expected_order
                or item.item_id in ids_by_kind["item"]
            ):
                raise _invalid_context()
            if item.place_handle is not None:
                if item.place_handle not in place_by_handle:
                    raise _invalid_context()
                referenced_place_handles.add(item.place_handle)
            if item.reservation_handle is not None and item.reservation_handle not in (
                reservation_by_handle
            ):
                raise _invalid_context()
            for value in (item.starts_at, item.ends_at):
                if value is None:
                    continue
                local_date, _ = local_date_time_parts(value, snapshot.timezone)
                if local_date != day.date:
                    raise _invalid_context()
            if item.starts_at is not None and item.ends_at is not None:
                if as_aware_utc(item.starts_at) > as_aware_utc(item.ends_at):
                    raise _invalid_context()
            ids_by_kind["item"].add(item.item_id)
            item_by_handle[item.handle] = (day, item)
            handles_by_kind[item.handle] = item
            all_handles.append(item.handle)

    if referenced_place_handles != set(place_by_handle):
        raise _invalid_context()
    if len(all_handles) != len(set(all_handles)):
        raise _invalid_context()
    if len(reservation_by_handle) != len(snapshot.reservations):
        raise _invalid_context()
    if len(candidate_by_handle) != len(snapshot.candidates):
        raise _invalid_context()
    removable = snapshot.removable_item_handles
    if len(removable) != len(set(removable)):
        raise _invalid_context()
    for item_handle in removable:
        pair = item_by_handle.get(item_handle)
        if pair is None:
            raise _invalid_context()
        _, item = pair
        if item.reservation_handle is not None or item.status in {"booked", "completed"}:
            raise _invalid_context()

    # Retain explicit maps above so malformed duplicate handles cannot hide a
    # record by replacing an earlier dictionary entry.
    if len(handles_by_kind) != len(all_handles) - 1:
        raise _invalid_context()


def preview_proposal(
    snapshot: ProposalTripSnapshot,
    draft: LocalProposalDraft,
) -> ProposalPreview:
    """Validate operation handles and simulate the complete final itinerary."""
    validate_proposal_snapshot(snapshot)
    if draft.schema_version != "travel-itinerary-patch-local-v1":
        raise _invalid_operation("invalid_proposal_version", "The proposal version is unsupported.")
    if draft.trip_handle != snapshot.trip_handle:
        raise _invalid_operation("proposal_scope_mismatch", "The proposal targets another trip.")
    if len(draft.operations) > MAX_PROPOSAL_OPERATIONS:
        raise _invalid_operation(
            "proposal_too_large", "A proposal may contain at most 25 operations."
        )

    places = {place.handle: place for place in snapshot.places}
    candidates = {candidate.handle: candidate for candidate in snapshot.candidates}
    days_by_handle = {day.handle: day for day in snapshot.days}
    reservations = {reservation.handle: reservation for reservation in snapshot.reservations}
    reservation_status = {handle: value.status for handle, value in reservations.items()}
    working: dict[str, list[_WorkingItem]] = {
        day.handle: [
            _WorkingItem(
                handle=item.handle,
                title=item.title,
                item_type=item.item_type,
                status=item.status,
                starts_at=item.starts_at,
                ends_at=item.ends_at,
                place_handle=item.place_handle,
                reservation_handle=item.reservation_handle,
            )
            for item in day.items
        ]
        for day in snapshot.days
    }
    before = _freeze_days(snapshot, working)

    for operation_index, operation in enumerate(draft.operations):
        if isinstance(operation, ProposalAddItem):
            day = days_by_handle.get(operation.day_handle)
            candidate = candidates.get(operation.candidate_handle)
            if day is None or candidate is None:
                raise _invalid_operation(
                    "unknown_proposal_handle", "An add operation uses an unknown day or candidate."
                )
            place = places[candidate.place_handle]
            day_items = working[day.handle]
            if operation.position > len(day_items):
                raise _invalid_operation(
                    "invalid_position", "The add position is outside the destination day."
                )
            if sum(len(items) for items in working.values()) >= MAX_PREVIEW_ITEMS:
                raise _invalid_operation(
                    "proposal_context_too_large",
                    "A proposal cannot exceed the bounded itinerary item limit.",
                )
            starts_at, ends_at = local_datetime_for_item(
                day.date, operation.start_time, operation.end_time, snapshot.timezone
            )
            day_items.insert(
                operation.position,
                _WorkingItem(
                    handle=f"preview-add-{operation_index + 1}",
                    title=place.name,
                    item_type=operation.item_type,
                    status="tentative",
                    starts_at=starts_at,
                    ends_at=ends_at,
                    place_handle=place.handle,
                    reservation_handle=None,
                ),
            )
            continue

        item_day_handle, item = _find_working_item(working, operation.item_handle)
        if item is None or item_day_handle is None:
            raise _invalid_operation(
                "unknown_proposal_handle", "An operation uses an unknown or removed item."
            )
        _ensure_item_is_editable(item, reservation_status)

        if isinstance(operation, ProposalMoveItem):
            destination = days_by_handle.get(operation.day_handle)
            if destination is None:
                raise _invalid_operation(
                    "unknown_proposal_handle", "A move operation uses an unknown day."
                )
            source_items = working[item_day_handle]
            destination_items = working[destination.handle]
            new_schedule = (item.starts_at, item.ends_at)
            if item_day_handle != destination.handle:
                new_schedule = local_datetime_for_item(
                    destination.date,
                    local_time_string(item.starts_at, snapshot.timezone),
                    local_time_string(item.ends_at, snapshot.timezone),
                    snapshot.timezone,
                )
            source_items.remove(item)
            target_items = (
                destination_items if item_day_handle != destination.handle else source_items
            )
            if operation.position > len(target_items):
                raise _invalid_operation(
                    "invalid_position", "The move position is outside the destination day."
                )
            item.starts_at, item.ends_at = new_schedule
            target_items.insert(operation.position, item)
            continue

        if isinstance(operation, ProposalSetItemTimes):
            day = days_by_handle[item_day_handle]
            current_start = local_time_string(item.starts_at, snapshot.timezone)
            current_end = local_time_string(item.ends_at, snapshot.timezone)
            fields = operation.model_fields_set
            start_value = operation.start_time if "start_time" in fields else current_start
            end_value = operation.end_time if "end_time" in fields else current_end
            item.starts_at, item.ends_at = local_datetime_for_item(
                day.date, start_value, end_value, snapshot.timezone
            )
            continue

        if isinstance(operation, ProposalRemoveItem):
            if item.handle not in snapshot.removable_item_handles:
                raise _invalid_operation(
                    "item_not_removable", "The traveler did not select this item for removal."
                )
            working[item_day_handle].remove(item)
            continue

        raise _invalid_operation("unsupported_operation", "The proposal operation is unsupported.")

    after = _freeze_days(snapshot, working)
    place_revisions = tuple(
        PlaceRevision(place_id=place.place_id, handle=place.handle, revision=place.revision)
        for place in sorted(snapshot.places, key=lambda candidate: candidate.place_id)
    )
    return ProposalPreview(
        trip_handle=snapshot.trip_handle,
        base_trip_revision=snapshot.trip_revision,
        place_revisions=place_revisions,
        operation_count=len(draft.operations),
        before=before,
        after=after,
        warnings=_reservation_warnings(snapshot, working),
    )


def _ensure_item_is_editable(
    item: _WorkingItem,
    reservation_status: Mapping[str, ReservationStatus],
) -> None:
    if item.status in {"booked", "completed"} or (
        item.reservation_handle is not None
        and reservation_status.get(item.reservation_handle) == "confirmed"
    ):
        raise _invalid_operation(
            "protected_item_anchor", "A booked or confirmed itinerary anchor cannot be changed."
        )


def _find_working_item(
    working: dict[str, list[_WorkingItem]], handle: str
) -> tuple[str | None, _WorkingItem | None]:
    for day_handle, items in working.items():
        for item in items:
            if item.handle == handle:
                return day_handle, item
    return None, None


def _freeze_days(
    snapshot: ProposalTripSnapshot,
    working: dict[str, list[_WorkingItem]],
) -> tuple[PreviewDay, ...]:
    result: list[PreviewDay] = []
    for day in snapshot.days:
        items = tuple(
            PreviewItem(
                handle=item.handle,
                title=item.title,
                item_type=item.item_type,
                status=item.status,
                sort_order=sort_order,
                start_time=local_time_string(item.starts_at, snapshot.timezone),
                end_time=local_time_string(item.ends_at, snapshot.timezone),
                place_handle=item.place_handle,
                reservation_handle=item.reservation_handle,
            )
            for sort_order, item in enumerate(working[day.handle])
        )
        result.append(
            PreviewDay(
                handle=day.handle,
                day_index=day.day_index,
                date=day.date,
                title=day.title,
                items=items,
            )
        )
    return tuple(result)


def _reservation_warnings(
    snapshot: ProposalTripSnapshot,
    working: dict[str, list[_WorkingItem]],
) -> tuple[PreviewWarning, ...]:
    warnings: list[tuple[int, int, str, PreviewWarning]] = []
    day_by_handle = {day.handle: day for day in snapshot.days}
    for reservation in snapshot.reservations:
        if reservation.status == "cancelled":
            continue
        reservation_bounds = schedule_bounds(reservation.starts_at, reservation.ends_at)
        if reservation_bounds is None:
            continue
        for day_handle, items in working.items():
            day = day_by_handle[day_handle]
            for sort_order, item in enumerate(items):
                if item.status == "cancelled" or item.reservation_handle == reservation.handle:
                    continue
                item_bounds = schedule_bounds(item.starts_at, item.ends_at)
                if item_bounds is None or not intervals_overlap(reservation_bounds, item_bounds):
                    continue
                warnings.append(
                    (
                        day.day_index,
                        sort_order,
                        reservation.handle,
                        PreviewWarning(
                            code="reservation_overlap",
                            day_handle=day.handle,
                            item_handle=item.handle,
                            reservation_handle=reservation.handle,
                            message="Reservation overlaps this itinerary item.",
                        ),
                    )
                )
    return tuple(entry[3] for entry in sorted(warnings, key=lambda entry: entry[:3]))
