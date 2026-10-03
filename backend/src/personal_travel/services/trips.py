from collections.abc import Iterable
from datetime import date, datetime, time
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from personal_travel.api.schemas import DayUpdate, TripCreate, TripUpdate
from personal_travel.models.trip import Trip, TripDay
from personal_travel.repositories.trips import SqlAlchemyTripRepository
from personal_travel.services.errors import DomainError, not_found
from personal_travel.services.time_utils import (
    as_aware_utc,
    get_zoneinfo,
    resolve_local_datetime,
)

MAX_TRIP_DAYS = 366


def validate_trip_range(start_date: date, end_date: date) -> None:
    if start_date > end_date:
        raise DomainError("invalid_date_range", "start_date must be on or before end_date.")
    if (end_date - start_date).days + 1 > MAX_TRIP_DAYS:
        raise DomainError(
            "date_range_too_large",
            f"a Phase 1 trip may contain at most {MAX_TRIP_DAYS} calendar days.",
            details={"max_days": MAX_TRIP_DAYS},
        )


def dates_between(start_date: date, end_date: date) -> Iterable[date]:
    for offset in range((end_date - start_date).days + 1):
        yield start_date.fromordinal(start_date.toordinal() + offset)


class TripService:
    def __init__(self, session: Session, owner_id: str) -> None:
        self._session = session
        self._owner_id = owner_id
        self._trips = SqlAlchemyTripRepository(session)

    def create(self, data: TripCreate) -> Trip:
        validate_trip_range(data.start_date, data.end_date)
        get_zoneinfo(data.timezone)
        with self._session.begin():
            trip = Trip(
                owner_id=self._owner_id,
                title=data.title,
                start_date=data.start_date,
                end_date=data.end_date,
                timezone=data.timezone,
            )
            self._trips.add(trip)
            self._session.flush()
            for day_index, day_date in enumerate(
                dates_between(data.start_date, data.end_date), start=1
            ):
                trip.days.append(TripDay(day_index=day_index, date=day_date))
            self._session.flush()
            return trip

    def list(self) -> list[Trip]:
        return self._trips.list(owner_id=self._owner_id)

    def get(self, trip_id: UUID) -> Trip:
        trip = self._trips.get(owner_id=self._owner_id, trip_id=trip_id)
        if trip is None:
            raise not_found("trip")
        return trip

    def update(self, trip_id: UUID, data: TripUpdate) -> Trip:
        with self._session.begin():
            trip = self._get_in_transaction(trip_id)
            target_start = (
                data.start_date if "start_date" in data.model_fields_set else trip.start_date
            )
            target_end = data.end_date if "end_date" in data.model_fields_set else trip.end_date
            target_timezone = (
                data.timezone if "timezone" in data.model_fields_set else trip.timezone
            )
            if target_start is None or target_end is None or target_timezone is None:
                raise DomainError(
                    "invalid_trip_update", "trip date and timezone values are required."
                )
            validate_trip_range(target_start, target_end)
            get_zoneinfo(target_timezone)

            if target_start != trip.start_date or target_end != trip.end_date:
                self._reconcile_days(trip, target_start, target_end)
            if target_timezone != trip.timezone:
                self._rebase_item_times(trip, trip.timezone, target_timezone)

            if "title" in data.model_fields_set:
                if data.title is None:
                    raise DomainError("invalid_title", "title cannot be null.")
                trip.title = data.title
            trip.start_date = target_start
            trip.end_date = target_end
            trip.timezone = target_timezone
            self._session.flush()
            return trip

    def update_day(self, trip_id: UUID, day_id: UUID, data: DayUpdate) -> TripDay:
        with self._session.begin():
            trip = self._get_in_transaction(trip_id)
            day = self._find_day(trip, day_id)
            if "title" in data.model_fields_set:
                day.title = data.title
            self._session.flush()
            return day

    def delete(self, trip_id: UUID) -> None:
        with self._session.begin():
            trip = self._get_in_transaction(trip_id)
            self._trips.delete(trip)

    def _get_in_transaction(self, trip_id: UUID) -> Trip:
        trip = self._trips.get(owner_id=self._owner_id, trip_id=trip_id)
        if trip is None:
            raise not_found("trip")
        return trip

    @staticmethod
    def _find_day(trip: Trip, day_id: UUID) -> TripDay:
        for day in trip.days:
            if day.id == day_id:
                return day
        raise not_found("trip day")

    def _reconcile_days(self, trip: Trip, start_date: date, end_date: date) -> None:
        requested_dates = list(dates_between(start_date, end_date))
        existing_by_date = {day.date: day for day in trip.days}
        removed_days = [day for day in trip.days if day.date not in requested_dates]
        occupied = [day for day in removed_days if day.items]
        if occupied:
            raise DomainError(
                "trip_days_contain_items",
                "cannot remove trip days that still contain itinerary items.",
                status_code=409,
                details={"day_ids": [str(day.id) for day in occupied]},
            )

        for day in removed_days:
            trip.days.remove(day)
            self._session.delete(day)
        if removed_days:
            self._session.flush()

        ordered_days: list[TripDay] = []
        for day_date in requested_dates:
            existing_day = existing_by_date.get(day_date)
            if existing_day is None:
                ordered_day = TripDay(
                    trip=trip,
                    date=day_date,
                    day_index=MAX_TRIP_DAYS + len(ordered_days) + 1,
                )
                self._session.add(ordered_day)
            else:
                ordered_day = existing_day
            ordered_days.append(ordered_day)

        self._session.flush()
        for day_index, day in enumerate(ordered_days, start=1):
            day.day_index = day_index
        self._session.flush()

    @staticmethod
    def _rebase_item_times(trip: Trip, old_timezone: str, new_timezone: str) -> None:
        old_zone = get_zoneinfo(old_timezone)
        new_zone = get_zoneinfo(new_timezone)
        for day in trip.days:
            starts: list[datetime | None] = []
            ends: list[datetime | None] = []
            for item in day.items:
                start_time = TripService._stored_local_time(
                    item.starts_at, day.date, old_zone, "start_time"
                )
                end_time = TripService._stored_local_time(
                    item.ends_at, day.date, old_zone, "end_time"
                )
                if start_time is not None and end_time is not None and start_time > end_time:
                    raise DomainError(
                        "invalid_time_range",
                        "an existing itinerary item has an invalid time range.",
                    )
                starts.append(
                    resolve_local_datetime(day.date, start_time, new_zone, field_name="start_time")
                    if start_time is not None
                    else None
                )
                ends.append(
                    resolve_local_datetime(day.date, end_time, new_zone, field_name="end_time")
                    if end_time is not None
                    else None
                )
            for item, starts_at, ends_at in zip(day.items, starts, ends, strict=True):
                item.starts_at = starts_at
                item.ends_at = ends_at

    @staticmethod
    def _stored_local_time(
        value: datetime | None,
        day_date: date,
        zone: ZoneInfo,
        field_name: str,
    ) -> time | None:
        if value is None:
            return None
        local = as_aware_utc(value).astimezone(zone)
        if local.date() != day_date:
            raise DomainError(
                "item_time_out_of_day",
                f"an existing {field_name} does not belong to its trip day.",
                details={"field": field_name, "date": day_date.isoformat()},
            )
        return local.timetz().replace(tzinfo=None)
