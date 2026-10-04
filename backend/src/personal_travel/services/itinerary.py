from uuid import UUID

from sqlalchemy.orm import Session

from personal_travel.api.schemas import ItemCreate, ItemUpdate, MoveItemRequest
from personal_travel.models.itinerary import ItineraryItem
from personal_travel.models.place import Place
from personal_travel.models.reservation import Reservation
from personal_travel.models.trip import Trip, TripDay
from personal_travel.repositories.itinerary import SqlAlchemyItineraryItemRepository
from personal_travel.repositories.places import SqlAlchemyPlaceRepository
from personal_travel.repositories.reservations import SqlAlchemyReservationRepository
from personal_travel.repositories.trips import SqlAlchemyTripRepository
from personal_travel.services.errors import DomainError, not_found
from personal_travel.services.revisions import require_expected_revision
from personal_travel.services.time_utils import local_datetime_for_item, local_time_string


class ItineraryService:
    def __init__(self, session: Session, owner_id: str) -> None:
        self._session = session
        self._owner_id = owner_id
        self._trips = SqlAlchemyTripRepository(session)
        self._places = SqlAlchemyPlaceRepository(session)
        self._reservations = SqlAlchemyReservationRepository(session)
        self._items = SqlAlchemyItineraryItemRepository(session)

    def create_item(
        self,
        trip_id: UUID,
        day_id: UUID,
        data: ItemCreate,
        *,
        expected_revision: int | None = None,
    ) -> ItineraryItem:
        with self._session.begin():
            trip = self._get_trip(trip_id)
            require_expected_revision(trip.revision, expected_revision, aggregate="trip")
            day = self._find_day(trip, day_id)
            place = self._get_place(data.place_id)
            reservation = self._get_reservation(trip.id, data.reservation_id)
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
            if reservation is not None:
                item.reservation = reservation
            day.items.append(item)
            self._items.add(item)
            trip.revision += 1
            self._session.flush()
            return item

    def update_item(
        self,
        trip_id: UUID,
        item_id: UUID,
        data: ItemUpdate,
        *,
        expected_revision: int | None = None,
    ) -> ItineraryItem:
        with self._session.begin():
            trip = self._get_trip(trip_id)
            require_expected_revision(trip.revision, expected_revision, aggregate="trip")
            day, item = self._find_item(trip, item_id)
            changed = False
            if "item_type" in data.model_fields_set:
                if data.item_type is None:
                    raise DomainError("invalid_item_type", "item_type cannot be null.")
                changed = changed or item.item_type != data.item_type
                item.item_type = data.item_type
            if "title" in data.model_fields_set:
                if data.title is None:
                    raise DomainError("invalid_title", "title cannot be null.")
                changed = changed or item.title != data.title
                item.title = data.title
            if "notes" in data.model_fields_set:
                changed = changed or item.notes != data.notes
                item.notes = data.notes
            if "status" in data.model_fields_set:
                if data.status is None:
                    raise DomainError("invalid_status", "status cannot be null.")
                changed = changed or item.status != data.status
                item.status = data.status
            if "place_id" in data.model_fields_set:
                place = self._get_place(data.place_id)
                changed = changed or item.place_id != (place.id if place is not None else None)
                item.place = place
            if "reservation_id" in data.model_fields_set:
                reservation = self._get_reservation(trip.id, data.reservation_id)
                changed = changed or item.reservation_id != (
                    reservation.id if reservation is not None else None
                )
                item.reservation = reservation

            if "start_time" in data.model_fields_set or "end_time" in data.model_fields_set:
                current_start = local_time_string(item.starts_at, trip.timezone)
                current_end = local_time_string(item.ends_at, trip.timezone)
                start_value = (
                    data.start_time if "start_time" in data.model_fields_set else current_start
                )
                end_value = data.end_time if "end_time" in data.model_fields_set else current_end
                starts_at, ends_at = local_datetime_for_item(
                    day.date, start_value, end_value, trip.timezone
                )
                changed = changed or item.starts_at != starts_at or item.ends_at != ends_at
                item.starts_at, item.ends_at = starts_at, ends_at
            if changed:
                trip.revision += 1
            self._session.flush()
            return item

    def delete_item(
        self,
        trip_id: UUID,
        item_id: UUID,
        *,
        expected_revision: int | None = None,
    ) -> None:
        with self._session.begin():
            trip = self._get_trip(trip_id)
            require_expected_revision(trip.revision, expected_revision, aggregate="trip")
            day, item = self._find_item(trip, item_id)
            day.items.remove(item)
            self._items.delete(item)
            self._session.flush()
            self._reindex(day)
            trip.revision += 1
            self._session.flush()

    def move_item(
        self,
        trip_id: UUID,
        item_id: UUID,
        data: MoveItemRequest,
        *,
        expected_revision: int | None = None,
    ) -> ItineraryItem:
        with self._session.begin():
            trip = self._get_trip(trip_id)
            require_expected_revision(trip.revision, expected_revision, aggregate="trip")
            source_day, item = self._find_item(trip, item_id)
            destination_day = self._find_day(trip, data.destination_day_id)
            original_position = source_day.items.index(item)

            if source_day is destination_day and data.position == original_position:
                return item

            # Moving a timed item preserves its wall-clock schedule on the new
            # date. Resolve before changing the graph so DST failures roll back
            # the whole move, including both days' order.
            if source_day is not destination_day:
                item.starts_at, item.ends_at = local_datetime_for_item(
                    destination_day.date,
                    local_time_string(item.starts_at, trip.timezone),
                    local_time_string(item.ends_at, trip.timezone),
                    trip.timezone,
                )

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
                self._reindex(source_day, destination_day)

            self._session.flush()
            trip.revision += 1
            self._session.flush()
            return item

    def _get_trip(self, trip_id: UUID) -> Trip:
        # Lock the aggregate root so concurrent item mutations serialize before
        # they calculate or rewrite sort_order values.
        trip = self._trips.get(owner_id=self._owner_id, trip_id=trip_id, for_update=True)
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

    def _get_reservation(self, trip_id: UUID, reservation_id: UUID | None) -> Reservation | None:
        if reservation_id is None:
            return None
        reservation = self._reservations.get(
            owner_id=self._owner_id,
            trip_id=trip_id,
            reservation_id=reservation_id,
        )
        if reservation is None:
            raise not_found("reservation")
        return reservation

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

    def _reindex(self, *days: TripDay) -> None:
        # Immediate SQL uniqueness checks make in-place swaps unsafe. First move
        # every affected item above all occupied positions, then compact. Both
        # flushes are inside the same trip-locked transaction.
        items = [item for day in days for item in day.items]
        temporary_start = (
            max(
                max((item.sort_order for item in items), default=-1),
                max((len(day.items) - 1 for day in days), default=-1),
            )
            + 1
        )
        for offset, item in enumerate(items):
            item.sort_order = temporary_start + offset
        self._session.flush()
        for day in days:
            for sort_order, item in enumerate(day.items):
                item.sort_order = sort_order
