from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from personal_travel.models.itinerary import ItineraryItem
from personal_travel.models.trip import Trip, TripDay
from personal_travel.services.time_utils import as_aware_utc


@dataclass(frozen=True)
class ReservationConflict:
    item: ItineraryItem
    day: TripDay
    reason: str


def _bounds(
    starts_at: datetime | None, ends_at: datetime | None
) -> tuple[datetime, datetime] | None:
    point = starts_at or ends_at
    if point is None:
        return None
    start = as_aware_utc(starts_at or point)
    end = as_aware_utc(ends_at or point)
    return start, end


def intervals_overlap(first: tuple[datetime, datetime], second: tuple[datetime, datetime]) -> bool:
    first_start, first_end = first
    second_start, second_end = second
    if first_start == first_end and second_start == second_end:
        return first_start == second_start
    if first_start == first_end:
        return second_start <= first_start < second_end
    if second_start == second_end:
        return first_start <= second_start < first_end
    return first_start < second_end and second_start < first_end


def calculate_reservation_conflicts(
    trip: Trip,
) -> dict[UUID, list[ReservationConflict]]:
    scheduled_items = [
        (day, item, _bounds(item.starts_at, item.ends_at))
        for day in trip.days
        for item in day.items
        if item.status != "cancelled"
    ]
    conflicts: dict[UUID, list[ReservationConflict]] = {}
    for reservation in trip.reservations:
        if reservation.status == "cancelled":
            continue
        reservation_bounds = _bounds(reservation.starts_at, reservation.ends_at)
        if reservation_bounds is None:
            continue
        for day, item, item_bounds in scheduled_items:
            if item_bounds is None or item.reservation_id == reservation.id:
                continue
            if intervals_overlap(reservation_bounds, item_bounds):
                conflicts.setdefault(reservation.id, []).append(
                    ReservationConflict(
                        item=item,
                        day=day,
                        reason="Reservation overlaps this itinerary item.",
                    )
                )

    for reservation_conflicts in conflicts.values():
        reservation_conflicts.sort(
            key=lambda conflict: (conflict.day.day_index, conflict.item.sort_order)
        )
    return conflicts
