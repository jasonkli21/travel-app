import uuid
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from personal_travel.models.itinerary import ItineraryItem
from personal_travel.models.reservation import Reservation, SavedPlace
from personal_travel.models.trip import Trip, TripDay


class TripRepository(Protocol):
    def get(
        self, *, owner_id: str, trip_id: uuid.UUID, for_update: bool = False
    ) -> Trip | None: ...
    def list(self, *, owner_id: str) -> list[Trip]: ...
    def add(self, trip: Trip) -> Trip: ...
    def delete(self, trip: Trip) -> None: ...


class SqlAlchemyTripRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, *, owner_id: str, trip_id: uuid.UUID, for_update: bool = False) -> Trip | None:
        statement = (
            select(Trip)
            .where(Trip.owner_id == owner_id, Trip.id == trip_id)
            .options(
                selectinload(Trip.days)
                .selectinload(TripDay.items)
                .selectinload(ItineraryItem.place),
                selectinload(Trip.days)
                .selectinload(TripDay.items)
                .selectinload(ItineraryItem.reservation),
                selectinload(Trip.reservations).selectinload(Reservation.place),
                selectinload(Trip.saved_places).selectinload(SavedPlace.place),
            )
        )
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def list(self, *, owner_id: str) -> list[Trip]:
        statement = (
            select(Trip)
            .where(Trip.owner_id == owner_id)
            .order_by(Trip.start_date.desc(), Trip.created_at.desc())
            .options(
                selectinload(Trip.days)
                .selectinload(TripDay.items)
                .selectinload(ItineraryItem.place),
                selectinload(Trip.days)
                .selectinload(TripDay.items)
                .selectinload(ItineraryItem.reservation),
                selectinload(Trip.reservations).selectinload(Reservation.place),
                selectinload(Trip.saved_places).selectinload(SavedPlace.place),
            )
        )
        return list(self._session.scalars(statement))

    def add(self, trip: Trip) -> Trip:
        self._session.add(trip)
        return trip

    def delete(self, trip: Trip) -> None:
        self._session.delete(trip)
