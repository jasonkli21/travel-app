"""Pure validation coverage for the internal, non-HTTP proposal preview."""

import json
from datetime import date, timedelta
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

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
from personal_travel.models.itinerary import ItineraryItem
from personal_travel.models.place import Place
from personal_travel.models.reservation import Reservation, SavedPlace
from personal_travel.models.trip import Trip, TripDay
from personal_travel.services.errors import DomainError
from personal_travel.services.proposal_preview import (
    build_proposal_snapshot,
    preview_proposal,
    validate_proposal_snapshot,
)
from personal_travel.services.time_utils import local_datetime_for_item


def handle(label: str) -> str:
    return f"h_{label:<8}".replace(" ", "0")


def make_snapshot(
    *,
    start_date: date = date(2026, 5, 10),
    timezone: str = "America/Los_Angeles",
    item_a_times: tuple[str | None, str | None] = ("09:00", "09:30"),
) -> ProposalTripSnapshot:
    trip_id = uuid4()
    place_id = uuid4()
    candidate_id = uuid4()
    reservation_id = uuid4()
    item_a_id, item_b_id, item_c_id = uuid4(), uuid4(), uuid4()
    first_day_id, second_day_id = uuid4(), uuid4()
    place_handle = handle("place001")
    candidate_handle = handle("cand0001")
    reservation_handle = handle("reserv01")
    item_a_handle, item_b_handle, item_c_handle = (
        handle("item0001"),
        handle("item0002"),
        handle("item0003"),
    )
    first_day_handle, second_day_handle = handle("day00001"), handle("day00002")
    start_at, end_at = local_datetime_for_item(
        start_date, item_a_times[0], item_a_times[1], timezone
    )
    reservation_start, reservation_end = local_datetime_for_item(
        start_date, "10:00", "11:00", timezone
    )
    linked_start, linked_end = local_datetime_for_item(start_date, "10:00", "11:00", timezone)

    place = ProposalPlaceSnapshot(
        place_id=place_id,
        owner_id="local",
        handle=place_handle,
        revision=4,
        name="City Museum",
    )
    candidate = ProposalCandidateSnapshot(
        candidate_id=candidate_id,
        owner_id="local",
        trip_id=trip_id,
        handle=candidate_handle,
        place_handle=place_handle,
    )
    reservation = ProposalReservationSnapshot(
        reservation_id=reservation_id,
        owner_id="local",
        trip_id=trip_id,
        handle=reservation_handle,
        status="confirmed",
        starts_at=reservation_start,
        ends_at=reservation_end,
        place_handle=place_handle,
    )
    item_a = ProposalItemSnapshot(
        item_id=item_a_id,
        owner_id="local",
        trip_id=trip_id,
        handle=item_a_handle,
        item_type="activity",
        title="Walk",
        status="planned",
        sort_order=0,
        starts_at=start_at,
        ends_at=end_at,
        place_handle=place_handle,
        reservation_handle=None,
    )
    protected_item = ProposalItemSnapshot(
        item_id=item_b_id,
        owner_id="local",
        trip_id=trip_id,
        handle=item_b_handle,
        item_type="lodging",
        title="Confirmed stay",
        status="planned",
        sort_order=1,
        starts_at=linked_start,
        ends_at=linked_end,
        place_handle=place_handle,
        reservation_handle=reservation_handle,
    )
    removable_item = ProposalItemSnapshot(
        item_id=item_c_id,
        owner_id="local",
        trip_id=trip_id,
        handle=item_c_handle,
        item_type="activity",
        title="Optional stop",
        status="tentative",
        sort_order=0,
        starts_at=None,
        ends_at=None,
        place_handle=None,
        reservation_handle=None,
    )
    return ProposalTripSnapshot(
        schema_version="travel-preview-context-local-v1",
        owner_id="local",
        trip_id=trip_id,
        trip_handle=handle("trip0001"),
        trip_revision=12,
        title="City break",
        start_date=start_date,
        end_date=start_date + timedelta(days=1),
        timezone=timezone,
        days=(
            ProposalDaySnapshot(
                day_id=first_day_id,
                trip_id=trip_id,
                handle=first_day_handle,
                day_index=1,
                date=start_date,
                title="Arrival",
                items=(item_a, protected_item),
            ),
            ProposalDaySnapshot(
                day_id=second_day_id,
                trip_id=trip_id,
                handle=second_day_handle,
                day_index=2,
                date=start_date + timedelta(days=1),
                title=None,
                items=(removable_item,),
            ),
        ),
        places=(place,),
        candidates=(candidate,),
        reservations=(reservation,),
        removable_item_handles=(item_c_handle,),
    )


def draft(snapshot: ProposalTripSnapshot, *operations: object) -> LocalProposalDraft:
    return LocalProposalDraft(
        schema_version="travel-itinerary-patch-local-v1",
        trip_handle=snapshot.trip_handle,
        operations=operations,  # type: ignore[arg-type]
    )


def test_preview_returns_immutable_complete_projection_and_deterministic_warnings() -> None:
    snapshot = make_snapshot()
    before_snapshot = snapshot.model_dump(mode="python")
    proposal = draft(
        snapshot,
        ProposalAddItem(
            kind="add_item",
            day_handle=snapshot.days[0].handle,
            candidate_handle=snapshot.candidates[0].handle,
            item_type="food",
            position=2,
            start_time="10:30",
            end_time="11:30",
        ),
        ProposalMoveItem(
            kind="move_item",
            item_handle=snapshot.days[0].items[0].handle,
            day_handle=snapshot.days[1].handle,
            position=0,
        ),
        ProposalSetItemTimes(
            kind="set_item_times",
            item_handle=snapshot.days[0].items[0].handle,
            start_time="12:00",
            end_time="13:00",
        ),
        ProposalRemoveItem(kind="remove_item", item_handle=snapshot.days[1].items[0].handle),
    )

    preview = preview_proposal(snapshot, proposal)

    assert preview.trip_handle == snapshot.trip_handle
    assert preview.base_trip_revision == 12
    assert preview.operation_count == 4
    assert [(footprint.place_id, footprint.revision) for footprint in preview.place_revisions] == [
        (snapshot.places[0].place_id, 4)
    ]
    assert [item.title for item in preview.before[0].items] == ["Walk", "Confirmed stay"]
    assert [item.sort_order for item in preview.after[0].items] == [0, 1]
    assert [item.title for item in preview.after[0].items] == ["Confirmed stay", "City Museum"]
    assert [item.title for item in preview.after[1].items] == ["Walk"]
    assert preview.after[1].items[0].start_time == "12:00"
    assert preview.after[1].items[0].end_time == "13:00"
    assert all(
        item.sort_order == index for day in preview.after for index, item in enumerate(day.items)
    )
    assert [
        (warning.code, warning.day_handle, warning.item_handle) for warning in preview.warnings
    ] == [("reservation_overlap", snapshot.days[0].handle, "preview-add-1")]
    assert snapshot.model_dump(mode="python") == before_snapshot
    with pytest.raises(ValidationError):
        snapshot.title = "mutated"


def test_reservation_warning_order_uses_stable_ids_not_random_handles() -> None:
    snapshot = make_snapshot()
    original_reservation = snapshot.reservations[0]
    earlier_reservation = original_reservation.model_copy(
        update={"reservation_id": UUID(int=1), "handle": handle("zzzzzzz9")}
    )
    unordered_snapshot = snapshot.model_copy(
        update={"reservations": (original_reservation, earlier_reservation)}
    )
    add = ProposalAddItem(
        kind="add_item",
        day_handle=unordered_snapshot.days[0].handle,
        candidate_handle=unordered_snapshot.candidates[0].handle,
        item_type="food",
        position=2,
        start_time="10:30",
        end_time="11:30",
    )

    preview = preview_proposal(unordered_snapshot, draft(unordered_snapshot, add))

    added_item_warnings = [
        warning.reservation_handle
        for warning in preview.warnings
        if warning.item_handle == "preview-add-1"
    ]
    assert added_item_warnings == [earlier_reservation.handle, original_reservation.handle]


def test_draft_rejects_extra_coercion_and_unbounded_operation_count() -> None:
    snapshot = make_snapshot()
    operation = {
        "kind": "move_item",
        "item_handle": snapshot.days[0].items[0].handle,
        "day_handle": snapshot.days[1].handle,
        "position": 0,
    }
    base = {
        "schema_version": "travel-itinerary-patch-local-v1",
        "trip_handle": snapshot.trip_handle,
        "operations": [operation],
    }
    LocalProposalDraft.model_validate_json(json.dumps(base))

    with pytest.raises(ValidationError, match="extra_forbidden"):
        LocalProposalDraft.model_validate_json(json.dumps({**base, "client_id": "x"}))
    with pytest.raises(ValidationError):
        LocalProposalDraft.model_validate_json(
            json.dumps({**base, "operations": [{**operation, "position": "0"}]})
        )
    with pytest.raises(ValidationError, match="too_long"):
        LocalProposalDraft.model_validate_json(json.dumps({**base, "operations": [operation] * 26}))


def test_preview_rejects_scope_unknown_handles_and_unapproved_removal() -> None:
    snapshot = make_snapshot()
    good = ProposalMoveItem(
        kind="move_item",
        item_handle=snapshot.days[0].items[0].handle,
        day_handle=snapshot.days[1].handle,
        position=0,
    )
    wrong_trip = LocalProposalDraft(
        schema_version="travel-itinerary-patch-local-v1",
        trip_handle=handle("other001"),
        operations=(good,),
    )
    with pytest.raises(DomainError, match="another trip") as mismatch:
        preview_proposal(snapshot, wrong_trip)
    assert mismatch.value.code == "proposal_scope_mismatch"

    unknown = ProposalMoveItem(
        kind="move_item",
        item_handle=handle("unknown1"),
        day_handle=snapshot.days[1].handle,
        position=0,
    )
    with pytest.raises(DomainError) as unknown_error:
        preview_proposal(snapshot, draft(snapshot, unknown))
    assert unknown_error.value.code == "unknown_proposal_handle"

    disallowed_removal = ProposalRemoveItem(
        kind="remove_item", item_handle=snapshot.days[0].items[0].handle
    )
    with pytest.raises(DomainError) as removal_error:
        preview_proposal(snapshot, draft(snapshot, disallowed_removal))
    assert removal_error.value.code == "item_not_removable"

    unknown_candidate = ProposalAddItem(
        kind="add_item",
        day_handle=snapshot.days[0].handle,
        candidate_handle=handle("missing1"),
        item_type="activity",
        position=2,
    )
    with pytest.raises(DomainError) as candidate_error:
        preview_proposal(snapshot, draft(snapshot, unknown_candidate))
    assert candidate_error.value.code == "unknown_proposal_handle"


@pytest.mark.parametrize(
    "operation,error_code",
    [
        (
            lambda snapshot: ProposalMoveItem(
                kind="move_item",
                item_handle=snapshot.days[0].items[1].handle,
                day_handle=snapshot.days[1].handle,
                position=0,
            ),
            "protected_item_anchor",
        ),
        (
            lambda snapshot: ProposalRemoveItem(
                kind="remove_item", item_handle=snapshot.days[0].items[1].handle
            ),
            "protected_item_anchor",
        ),
        (
            lambda snapshot: ProposalSetItemTimes(
                kind="set_item_times",
                item_handle=snapshot.days[0].items[1].handle,
                start_time="12:00",
            ),
            "protected_item_anchor",
        ),
    ],
)
def test_preview_protects_confirmed_reservation_anchors(operation, error_code: str) -> None:
    snapshot = make_snapshot()
    with pytest.raises(DomainError) as protected:
        preview_proposal(snapshot, draft(snapshot, operation(snapshot)))
    assert protected.value.code == error_code


def test_snapshot_validator_rejects_cross_owner_trip_and_duplicate_handles() -> None:
    snapshot = make_snapshot()
    foreign_candidate = snapshot.candidates[0].model_copy(update={"owner_id": "another-owner"})
    with pytest.raises(DomainError) as owner_error:
        validate_proposal_snapshot(snapshot.model_copy(update={"candidates": (foreign_candidate,)}))
    assert owner_error.value.code == "invalid_proposal_context"

    foreign_trip_candidate = snapshot.candidates[0].model_copy(update={"trip_id": uuid4()})
    with pytest.raises(DomainError) as trip_error:
        validate_proposal_snapshot(
            snapshot.model_copy(update={"candidates": (foreign_trip_candidate,)})
        )
    assert trip_error.value.code == "invalid_proposal_context"

    duplicate_day = snapshot.days[1].model_copy(update={"handle": snapshot.days[0].handle})
    with pytest.raises(DomainError) as duplicate_error:
        validate_proposal_snapshot(
            snapshot.model_copy(update={"days": (snapshot.days[0], duplicate_day)})
        )
    assert duplicate_error.value.code == "invalid_proposal_context"


@pytest.mark.parametrize("status", ["booked", "completed"])
def test_preview_protects_booked_and_completed_items(status: str) -> None:
    snapshot = make_snapshot()
    protected_item = snapshot.days[0].items[0].model_copy(update={"status": status})
    first_day = snapshot.days[0].model_copy(
        update={"items": (protected_item, snapshot.days[0].items[1])}
    )
    protected_snapshot = snapshot.model_copy(update={"days": (first_day, snapshot.days[1])})
    move = ProposalMoveItem(
        kind="move_item",
        item_handle=protected_item.handle,
        day_handle=protected_snapshot.days[1].handle,
        position=1,
    )

    with pytest.raises(DomainError) as protected:
        preview_proposal(protected_snapshot, draft(protected_snapshot, move))
    assert protected.value.code == "protected_item_anchor"


@pytest.mark.parametrize(
    "start_date,timezone,wall_time,error_code",
    [
        (date(2026, 3, 7), "America/New_York", "02:30", "invalid_local_time"),
        (date(2026, 10, 31), "America/New_York", "01:30", "ambiguous_local_time"),
    ],
)
def test_preview_move_reuses_dst_validation_without_changing_snapshot(
    start_date: date, timezone: str, wall_time: str, error_code: str
) -> None:
    snapshot = make_snapshot(
        start_date=start_date,
        timezone=timezone,
        item_a_times=(wall_time, "03:00"),
    )
    move = ProposalMoveItem(
        kind="move_item",
        item_handle=snapshot.days[0].items[0].handle,
        day_handle=snapshot.days[1].handle,
        position=1,
    )
    original = snapshot.model_dump(mode="python")

    with pytest.raises(DomainError) as dst_error:
        preview_proposal(snapshot, draft(snapshot, move))

    assert dst_error.value.code == error_code
    assert snapshot.model_dump(mode="python") == original


def test_preview_rejects_add_that_exceeds_total_item_bound() -> None:
    snapshot = make_snapshot()
    days = list(snapshot.days)
    remaining = 5000 - sum(len(day.items) for day in days)
    for day_index in range(remaining):
        source = days[day_index % len(days)]
        next_order = len(source.items)
        filler = source.items[-1].model_copy(
            update={
                "item_id": uuid4(),
                "handle": handle(f"fill{day_index:04}"),
                "sort_order": next_order,
            }
        )
        days[day_index % len(days)] = source.model_copy(update={"items": (*source.items, filler)})
    bounded_snapshot = snapshot.model_copy(update={"days": tuple(days)})
    final_day = bounded_snapshot.days[-1]
    add = ProposalAddItem(
        kind="add_item",
        day_handle=final_day.handle,
        candidate_handle=bounded_snapshot.candidates[0].handle,
        item_type="activity",
        position=len(final_day.items),
    )

    with pytest.raises(DomainError) as bound_error:
        preview_proposal(bounded_snapshot, draft(bounded_snapshot, add))
    assert bound_error.value.code == "proposal_context_too_large"


def test_orm_projection_uses_opaque_handles_and_omits_private_text() -> None:
    trip_id, day_id, item_id, place_id, candidate_id, reservation_id = (uuid4() for _ in range(6))
    place = Place(id=place_id, owner_id="local", name="Library", revision=7)
    reservation = Reservation(
        id=reservation_id,
        owner_id="local",
        trip_id=trip_id,
        provider_name="Private Provider",
        status="tentative",
        confirmation_code="secret-code",
        source_reference="private-reference",
        notes="private reservation note",
    )
    item = ItineraryItem(
        id=item_id,
        trip_day_id=day_id,
        title="Quiet visit",
        notes="private itinerary note",
        item_type="activity",
        status="tentative",
        sort_order=0,
        place=place,
    )
    day = TripDay(
        id=day_id,
        trip_id=trip_id,
        day_index=1,
        date=date(2026, 5, 10),
        items=[item],
    )
    candidate = SavedPlace(
        id=candidate_id,
        owner_id="local",
        trip_id=trip_id,
        place_id=place_id,
        place=place,
    )
    trip = Trip(
        id=trip_id,
        owner_id="local",
        title="Projection test",
        start_date=date(2026, 5, 10),
        end_date=date(2026, 5, 10),
        timezone="America/Los_Angeles",
        revision=9,
        days=[day],
        saved_places=[candidate],
        reservations=[reservation],
    )
    opaque_handles = iter(
        handle(label)
        for label in (
            "trip0001",
            "day00001",
            "item0001",
            "reserv01",
            "place001",
            "cand0001",
        )
    )

    snapshot = build_proposal_snapshot(
        trip,
        "local",
        removable_item_ids=(item_id,),
        handle_factory=lambda: next(opaque_handles),
    )

    assert snapshot.trip_handle == handle("trip0001")
    assert snapshot.trip_revision == 9
    assert snapshot.days[0].items[0].handle == handle("item0001")
    assert snapshot.candidates[0].handle == handle("cand0001")
    assert snapshot.removable_item_handles == (handle("item0001"),)
    serialized = snapshot.model_dump_json()
    assert "private itinerary note" not in serialized
    assert "secret-code" not in serialized
    assert "private-reference" not in serialized
    assert "private reservation note" not in serialized


def test_orm_projection_rejects_item_link_outside_trip_reservations() -> None:
    trip_id, day_id, item_id, reservation_id = (uuid4() for _ in range(4))
    reservation = Reservation(
        id=reservation_id,
        owner_id="local",
        trip_id=uuid4(),
        provider_name="Other trip reservation",
        status="tentative",
    )
    item = ItineraryItem(
        id=item_id,
        trip_day_id=day_id,
        reservation_id=reservation_id,
        title="Linked elsewhere",
        item_type="activity",
        status="tentative",
        sort_order=0,
        reservation=reservation,
    )
    day = TripDay(
        id=day_id,
        trip_id=trip_id,
        day_index=1,
        date=date(2026, 5, 10),
        items=[item],
    )
    trip = Trip(
        id=trip_id,
        owner_id="local",
        title="Projection test",
        start_date=date(2026, 5, 10),
        end_date=date(2026, 5, 10),
        timezone="UTC",
        days=[day],
    )

    with pytest.raises(DomainError) as invalid_link:
        build_proposal_snapshot(trip, "local")
    assert invalid_link.value.code == "invalid_proposal_context"
