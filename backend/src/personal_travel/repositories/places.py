import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from personal_travel.models.place import Place


class SqlAlchemyPlaceRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, *, owner_id: str, place_id: uuid.UUID, for_update: bool = False) -> Place | None:
        statement = select(Place).where(Place.owner_id == owner_id, Place.id == place_id)
        if for_update:
            statement = statement.with_for_update().execution_options(populate_existing=True)
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
