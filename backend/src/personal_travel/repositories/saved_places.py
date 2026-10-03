import uuid
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from personal_travel.models.reservation import SavedPlace


class SavedPlaceRepository(Protocol):
    def get(
        self,
        *,
        owner_id: str,
        trip_id: uuid.UUID,
        saved_place_id: uuid.UUID,
        for_update: bool = False,
    ) -> SavedPlace | None: ...

    def add(self, saved_place: SavedPlace) -> SavedPlace: ...

    def delete(self, saved_place: SavedPlace) -> None: ...


class SqlAlchemySavedPlaceRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(
        self,
        *,
        owner_id: str,
        trip_id: uuid.UUID,
        saved_place_id: uuid.UUID,
        for_update: bool = False,
    ) -> SavedPlace | None:
        statement = (
            select(SavedPlace)
            .where(
                SavedPlace.owner_id == owner_id,
                SavedPlace.trip_id == trip_id,
                SavedPlace.id == saved_place_id,
            )
            .options(selectinload(SavedPlace.place))
        )
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def add(self, saved_place: SavedPlace) -> SavedPlace:
        self._session.add(saved_place)
        return saved_place

    def delete(self, saved_place: SavedPlace) -> None:
        self._session.delete(saved_place)
