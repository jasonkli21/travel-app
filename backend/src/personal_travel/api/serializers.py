from uuid import UUID

from personal_travel.api.schemas import (
    ItineraryItemResponse,
    PlaceSummaryResponse,
    ReservationConflictResponse,
    ReservationLinkedItemResponse,
    ReservationResponse,
    ReservationSummaryResponse,
    SavedPlaceResponse,
    TripDayResponse,
    TripDetailResponse,
    TripSummaryResponse,
)
from personal_travel.models.itinerary import ItineraryItem
from personal_travel.models.place import Place
from personal_travel.models.reservation import Reservation, SavedPlace
from personal_travel.models.trip import Trip, TripDay
from personal_travel.services.conflicts import ReservationConflict, calculate_reservation_conflicts
from personal_travel.services.time_utils import local_date_time_parts, local_time_string


def serialize_place(place: Place | None) -> PlaceSummaryResponse | None:
    if place is None:
        return None
    return PlaceSummaryResponse(
        id=place.id,
        name=place.name,
        address=place.address,
        category=place.category,
        phone=place.phone,
        website_url=place.website_url,
        latitude=float(place.latitude) if place.latitude is not None else None,
        longitude=float(place.longitude) if place.longitude is not None else None,
    )


def serialize_item(
    item: ItineraryItem,
    timezone_name: str,
    *,
    reservation_conflict_counts: dict[UUID, int] | None = None,
) -> ItineraryItemResponse:
    reservation = item.reservation
    return ItineraryItemResponse(
        id=item.id,
        item_type=item.item_type,  # type: ignore[arg-type]
        title=item.title,
        notes=item.notes,
        start_time=local_time_string(item.starts_at, timezone_name),
        end_time=local_time_string(item.ends_at, timezone_name),
        sort_order=item.sort_order,
        status=item.status,  # type: ignore[arg-type]
        place=serialize_place(item.place),
        reservation=(
            ReservationSummaryResponse(
                id=reservation.id,
                reservation_type=reservation.reservation_type,  # type: ignore[arg-type]
                status=reservation.status,  # type: ignore[arg-type]
                provider_name=reservation.provider_name,
                confirmation_code=reservation.confirmation_code,
                conflict_count=(reservation_conflict_counts or {}).get(reservation.id, 0),
            )
            if reservation is not None
            else None
        ),
    )


def serialize_day(
    day: TripDay,
    timezone_name: str,
    *,
    reservation_conflict_counts: dict[UUID, int] | None = None,
) -> TripDayResponse:
    return TripDayResponse(
        id=day.id,
        day_index=day.day_index,
        date=day.date,
        title=day.title,
        items=[
            serialize_item(
                item,
                timezone_name,
                reservation_conflict_counts=reservation_conflict_counts,
            )
            for item in sorted(day.items, key=lambda candidate: candidate.sort_order)
        ],
    )


def serialize_summary(trip: Trip) -> TripSummaryResponse:
    return TripSummaryResponse(
        id=trip.id,
        title=trip.title,
        start_date=trip.start_date,
        end_date=trip.end_date,
        timezone=trip.timezone,
        day_count=len(trip.days),
        item_count=sum(len(day.items) for day in trip.days),
        created_at=trip.created_at,
        updated_at=trip.updated_at,
    )


def serialize_reservation(
    reservation: Reservation,
    trip: Trip,
    conflicts: list[ReservationConflict],
) -> ReservationResponse:
    start_date, start_time = local_date_time_parts(reservation.starts_at, trip.timezone)
    end_date, end_time = local_date_time_parts(reservation.ends_at, trip.timezone)
    linked_items = [
        ReservationLinkedItemResponse(
            id=item.id,
            title=item.title,
            day_id=day.id,
            day_index=day.day_index,
            date=day.date,
        )
        for day in trip.days
        for item in day.items
        if item.reservation_id == reservation.id
    ]
    return ReservationResponse(
        id=reservation.id,
        reservation_type=reservation.reservation_type,  # type: ignore[arg-type]
        status=reservation.status,  # type: ignore[arg-type]
        provider_name=reservation.provider_name,
        confirmation_code=reservation.confirmation_code,
        start_date=start_date,
        start_time=start_time,
        end_date=end_date,
        end_time=end_time,
        place=serialize_place(reservation.place),
        source_reference=reservation.source_reference,
        notes=reservation.notes,
        linked_items=linked_items,
        conflicts=[
            ReservationConflictResponse(
                item_id=conflict.item.id,
                day_id=conflict.day.id,
                day_index=conflict.day.day_index,
                date=conflict.day.date,
                title=conflict.item.title,
                start_time=local_time_string(conflict.item.starts_at, trip.timezone),
                end_time=local_time_string(conflict.item.ends_at, trip.timezone),
                reason=conflict.reason,
            )
            for conflict in conflicts
        ],
        created_at=reservation.created_at,
        updated_at=reservation.updated_at,
    )


def serialize_saved_place(saved_place: SavedPlace) -> SavedPlaceResponse:
    place = serialize_place(saved_place.place)
    assert place is not None
    return SavedPlaceResponse(
        id=saved_place.id,
        note=saved_place.note,
        place=place,
        created_at=saved_place.created_at,
        updated_at=saved_place.updated_at,
    )


def serialize_detail(trip: Trip) -> TripDetailResponse:
    reservation_conflicts = calculate_reservation_conflicts(trip)
    conflict_counts = {
        reservation_id: len(conflicts)
        for reservation_id, conflicts in reservation_conflicts.items()
    }
    summary = serialize_summary(trip)
    return TripDetailResponse(
        **summary.model_dump(),
        days=[
            serialize_day(
                day,
                trip.timezone,
                reservation_conflict_counts=conflict_counts,
            )
            for day in sorted(trip.days, key=lambda candidate: candidate.day_index)
        ],
    )
