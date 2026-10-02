import uuid
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from personal_travel.models.trip import Trip


class TripRepository(Protocol):
    def get(self, *, owner_id: str, trip_id: uuid.UUID) -> Trip | None: ...
    def list(self, *, owner_id: str) -> list[Trip]: ...
    def add(self, trip: Trip) -> Trip: ...


class SqlAlchemyTripRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, *, owner_id: str, trip_id: uuid.UUID) -> Trip | None:
        statement = select(Trip).where(Trip.owner_id == owner_id, Trip.id == trip_id)
        return self._session.scalar(statement)

    def list(self, *, owner_id: str) -> list[Trip]:
        statement = (
            select(Trip)
            .where(Trip.owner_id == owner_id)
            .order_by(Trip.start_date.desc(), Trip.created_at.desc())
        )
        return list(self._session.scalars(statement))

    def add(self, trip: Trip) -> Trip:
        self._session.add(trip)
        return trip
