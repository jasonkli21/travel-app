import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from personal_travel.models.itinerary import ItineraryItem
from personal_travel.models.reservation import Reservation, SavedPlace
from personal_travel.models.trip import Trip, TripDay


class SqlAlchemyTripRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, *, owner_id: str, trip_id: uuid.UUID, for_update: bool = False) -> Trip | None:
        statement = (
            select(Trip)
            .execution_options(populate_existing=True)
            .where(Trip.owner_id == owner_id, Trip.id == trip_id)
            .options(
                selectinload(Trip.days)
                .selectinload(TripDay.items)
                .selectinload(ItineraryItem.place),
                selectinload(Trip.days)
                .selectinload(TripDay.items)
                .selectinload(ItineraryItem.reservation),
                selectinload(Trip.days)
                .selectinload(TripDay.items)
                .selectinload(ItineraryItem.reservation)
                .selectinload(Reservation.place),
                selectinload(Trip.reservations).selectinload(Reservation.place),
                selectinload(Trip.saved_places).selectinload(SavedPlace.place),
            )
        )
        statement = statement.with_for_update(read=not for_update)
        return self._session.scalar(statement)

    def list(self, *, owner_id: str) -> list[tuple[Trip, int, int]]:
        days = select(func.count(TripDay.id)).where(TripDay.trip_id == Trip.id).scalar_subquery()
        items = (
            select(func.count(ItineraryItem.id))
            .join(TripDay, ItineraryItem.trip_day_id == TripDay.id)
            .where(TripDay.trip_id == Trip.id)
            .scalar_subquery()
        )
        statement = (
            select(Trip, days, items)
            .where(Trip.owner_id == owner_id)
            .order_by(Trip.start_date.desc(), Trip.created_at.desc(), Trip.id)
        )
        return [
            (trip, day_count, item_count)
            for trip, day_count, item_count in self._session.execute(statement)
        ]

    def add(self, trip: Trip) -> Trip:
        self._session.add(trip)
        return trip

    def delete(self, trip: Trip) -> None:
        self._session.delete(trip)
