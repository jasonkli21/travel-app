from personal_travel.api.schemas import (
    ItineraryItemResponse,
    PlaceSummaryResponse,
    TripDayResponse,
    TripDetailResponse,
    TripSummaryResponse,
)
from personal_travel.models.itinerary import ItineraryItem
from personal_travel.models.place import Place
from personal_travel.models.trip import Trip, TripDay
from personal_travel.services.time_utils import local_time_string


def serialize_place(place: Place | None) -> PlaceSummaryResponse | None:
    if place is None:
        return None
    return PlaceSummaryResponse(
        id=place.id,
        name=place.name,
        address=place.address,
        latitude=float(place.latitude) if place.latitude is not None else None,
        longitude=float(place.longitude) if place.longitude is not None else None,
    )


def serialize_item(item: ItineraryItem, timezone_name: str) -> ItineraryItemResponse:
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
    )


def serialize_day(day: TripDay, timezone_name: str) -> TripDayResponse:
    return TripDayResponse(
        id=day.id,
        day_index=day.day_index,
        date=day.date,
        title=day.title,
        items=[
            serialize_item(item, timezone_name)
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


def serialize_detail(trip: Trip) -> TripDetailResponse:
    summary = serialize_summary(trip)
    return TripDetailResponse(
        **summary.model_dump(),
        days=[
            serialize_day(day, trip.timezone)
            for day in sorted(trip.days, key=lambda candidate: candidate.day_index)
        ],
    )
