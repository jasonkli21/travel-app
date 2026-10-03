import uuid
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from personal_travel.models.place import Place


class PlaceRepository(Protocol):
    def get(self, *, owner_id: str, place_id: uuid.UUID) -> Place | None: ...
    def get_by_provider_identity(
        self, *, owner_id: str, provider: str, provider_place_id: str
    ) -> Place | None: ...
    def list(self, *, owner_id: str) -> list[Place]: ...
    def add(self, place: Place) -> Place: ...


class SqlAlchemyPlaceRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, *, owner_id: str, place_id: uuid.UUID) -> Place | None:
        statement = select(Place).where(Place.owner_id == owner_id, Place.id == place_id)
        return self._session.scalar(statement)

    def get_by_provider_identity(
        self, *, owner_id: str, provider: str, provider_place_id: str
    ) -> Place | None:
        statement = select(Place).where(
            Place.owner_id == owner_id,
            Place.provider == provider,
            Place.provider_place_id == provider_place_id,
        )
        return self._session.scalar(statement)

    def list(self, *, owner_id: str) -> list[Place]:
        statement = select(Place).where(Place.owner_id == owner_id).order_by(Place.name.asc())
        return list(self._session.scalars(statement))

    def add(self, place: Place) -> Place:
        self._session.add(place)
        return place
