from uuid import UUID

from sqlalchemy.orm import Session

from personal_travel.api.schemas import ItemCreate, ItemUpdate, MoveItemRequest
from personal_travel.models.itinerary import ItineraryItem
from personal_travel.models.place import Place
from personal_travel.models.trip import Trip, TripDay
from personal_travel.repositories.itinerary import SqlAlchemyItineraryItemRepository
from personal_travel.repositories.places import SqlAlchemyPlaceRepository
from personal_travel.repositories.trips import SqlAlchemyTripRepository
from personal_travel.services.errors import DomainError, not_found
from personal_travel.services.time_utils import local_datetime_for_item, local_time_string


class ItineraryService:
    def __init__(self, session: Session, owner_id: str) -> None:
        self._session = session
        self._owner_id = owner_id
        self._trips = SqlAlchemyTripRepository(session)
        self._places = SqlAlchemyPlaceRepository(session)
        self._items = SqlAlchemyItineraryItemRepository(session)

    def create_item(self, trip_id: UUID, day_id: UUID, data: ItemCreate) -> ItineraryItem:
        with self._session.begin():
            trip = self._get_trip(trip_id)
            day = self._find_day(trip, day_id)
            place = self._get_place(data.place_id)
            starts_at, ends_at = local_datetime_for_item(
                day.date, data.start_time, data.end_time, trip.timezone
            )
            item = ItineraryItem(
                item_type=data.item_type,
                title=data.title,
                notes=data.notes,
                starts_at=starts_at,
                ends_at=ends_at,
                sort_order=len(day.items),
                status=data.status,
            )
            if place is not None:
                item.place = place
            day.items.append(item)
            self._items.add(item)
            self._session.flush()
            return item

    def update_item(self, trip_id: UUID, item_id: UUID, data: ItemUpdate) -> ItineraryItem:
        with self._session.begin():
            trip = self._get_trip(trip_id)
            day, item = self._find_item(trip, item_id)
            if "item_type" in data.model_fields_set:
                if data.item_type is None:
                    raise DomainError("invalid_item_type", "item_type cannot be null.")
                item.item_type = data.item_type
            if "title" in data.model_fields_set:
                if data.title is None:
                    raise DomainError("invalid_title", "title cannot be null.")
                item.title = data.title
            if "notes" in data.model_fields_set:
                item.notes = data.notes
            if "status" in data.model_fields_set:
                if data.status is None:
                    raise DomainError("invalid_status", "status cannot be null.")
                item.status = data.status
            if "place_id" in data.model_fields_set:
                item.place = self._get_place(data.place_id)

            if "start_time" in data.model_fields_set or "end_time" in data.model_fields_set:
                current_start = local_time_string(item.starts_at, trip.timezone)
                current_end = local_time_string(item.ends_at, trip.timezone)
                start_value = (
                    data.start_time if "start_time" in data.model_fields_set else current_start
                )
                end_value = data.end_time if "end_time" in data.model_fields_set else current_end
                item.starts_at, item.ends_at = local_datetime_for_item(
                    day.date, start_value, end_value, trip.timezone
                )
            self._session.flush()
            return item

    def delete_item(self, trip_id: UUID, item_id: UUID) -> None:
        with self._session.begin():
            trip = self._get_trip(trip_id)
            day, item = self._find_item(trip, item_id)
            day.items.remove(item)
            self._items.delete(item)
            self._reindex(day)
            self._session.flush()

    def move_item(self, trip_id: UUID, item_id: UUID, data: MoveItemRequest) -> ItineraryItem:
        with self._session.begin():
            trip = self._get_trip(trip_id)
            source_day, item = self._find_item(trip, item_id)
            destination_day = self._find_day(trip, data.destination_day_id)

            if source_day is destination_day:
                source_day.items.remove(item)
                if data.position > len(source_day.items):
                    raise DomainError(
                        "invalid_position",
                        "position must be within the destination day's insertion range.",
                        details={"position": data.position},
                    )
                source_day.items.insert(data.position, item)
                self._reindex(source_day)
            else:
                source_day.items.remove(item)
                if data.position > len(destination_day.items):
                    raise DomainError(
                        "invalid_position",
                        "position must be within the destination day's insertion range.",
                        details={"position": data.position},
                    )
                destination_day.items.insert(data.position, item)
                self._reindex(source_day)
                self._reindex(destination_day)

            self._session.flush()
            return item

    def _get_trip(self, trip_id: UUID) -> Trip:
        trip = self._trips.get(owner_id=self._owner_id, trip_id=trip_id)
        if trip is None:
            raise not_found("trip")
        return trip

    def _get_place(self, place_id: UUID | None) -> Place | None:
        if place_id is None:
            return None
        place = self._places.get(owner_id=self._owner_id, place_id=place_id)
        if place is None:
            raise not_found("place")
        return place

    @staticmethod
    def _find_day(trip: Trip, day_id: UUID) -> TripDay:
        for day in trip.days:
            if day.id == day_id:
                return day
        raise not_found("trip day")

    @staticmethod
    def _find_item(trip: Trip, item_id: UUID) -> tuple[TripDay, ItineraryItem]:
        for day in trip.days:
            for item in day.items:
                if item.id == item_id:
                    return day, item
        raise not_found("itinerary item")

    @staticmethod
    def _reindex(day: TripDay) -> None:
        for sort_order, item in enumerate(day.items):
            item.sort_order = sort_order
