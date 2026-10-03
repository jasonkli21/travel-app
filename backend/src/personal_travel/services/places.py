from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from personal_travel.api.schemas import PlaceCreate, PlaceUpdate
from personal_travel.models.place import Place
from personal_travel.repositories.places import SqlAlchemyPlaceRepository
from personal_travel.services.errors import DomainError, not_found


class PlaceService:
    def __init__(self, session: Session, owner_id: str) -> None:
        self._session = session
        self._owner_id = owner_id
        self._places = SqlAlchemyPlaceRepository(session)

    def list(self) -> list[Place]:
        return self._places.list(owner_id=self._owner_id)

    def create(self, data: PlaceCreate) -> Place:
        if (data.latitude is None) != (data.longitude is None):
            raise DomainError(
                "invalid_coordinates", "latitude and longitude must be provided together."
            )
        with self._session.begin():
            place = Place(
                owner_id=self._owner_id,
                name=data.name,
                address=data.address,
                category=data.category,
                phone=data.phone,
                website_url=data.website_url,
                latitude=Decimal(str(data.latitude)) if data.latitude is not None else None,
                longitude=Decimal(str(data.longitude)) if data.longitude is not None else None,
            )
            self._places.add(place)
            self._session.flush()
            return place

    def update(self, place_id: UUID, data: PlaceUpdate) -> Place:
        with self._session.begin():
            place = self._places.get(owner_id=self._owner_id, place_id=place_id, for_update=True)
            if place is None:
                raise not_found("place")
            if "name" in data.model_fields_set:
                if data.name is None:
                    raise DomainError("invalid_place_name", "name cannot be null.")
                place.name = data.name
            if "address" in data.model_fields_set:
                place.address = data.address
            if "category" in data.model_fields_set:
                place.category = data.category
            if "phone" in data.model_fields_set:
                place.phone = data.phone
            if "website_url" in data.model_fields_set:
                place.website_url = data.website_url
            if "latitude" in data.model_fields_set and "longitude" in data.model_fields_set:
                place.latitude = Decimal(str(data.latitude)) if data.latitude is not None else None
                place.longitude = (
                    Decimal(str(data.longitude)) if data.longitude is not None else None
                )
            self._session.flush()
            return place

    def get(self, place_id: UUID) -> Place:
        place = self._places.get(owner_id=self._owner_id, place_id=place_id)
        if place is None:
            raise not_found("place")
        return place
