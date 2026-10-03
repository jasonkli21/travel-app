from datetime import date
from uuid import UUID

from sqlalchemy.orm import Session

from personal_travel.api.schemas import ReservationCreate, ReservationUpdate
from personal_travel.models.place import Place
from personal_travel.models.reservation import Reservation
from personal_travel.models.trip import Trip
from personal_travel.repositories.places import SqlAlchemyPlaceRepository
from personal_travel.repositories.reservations import SqlAlchemyReservationRepository
from personal_travel.repositories.trips import SqlAlchemyTripRepository
from personal_travel.services.errors import DomainError, not_found
from personal_travel.services.time_utils import local_datetime_for_reservation


class ReservationService:
    def __init__(self, session: Session, owner_id: str) -> None:
        self._session = session
        self._owner_id = owner_id
        self._trips = SqlAlchemyTripRepository(session)
        self._places = SqlAlchemyPlaceRepository(session)
        self._reservations = SqlAlchemyReservationRepository(session)

    def list(self, trip_id: UUID) -> list[Reservation]:
        trip = self._get_trip(trip_id)
        return sorted(trip.reservations, key=self._sort_key)

    def create(self, trip_id: UUID, data: ReservationCreate) -> Reservation:
        with self._session.begin():
            trip = self._get_trip(trip_id, for_update=True)
            place = self._get_place(data.place_id)
            starts_at, ends_at = local_datetime_for_reservation(
                data.start_date,
                data.start_time,
                data.end_date,
                data.end_time,
                trip.timezone,
            )
            reservation = Reservation(
                owner_id=self._owner_id,
                trip_id=trip.id,
                reservation_type=data.reservation_type,
                status=data.status,
                provider_name=data.provider_name,
                confirmation_code=data.confirmation_code,
                starts_at=starts_at,
                ends_at=ends_at,
                source_reference=data.source_reference,
                notes=data.notes,
            )
            if place is not None:
                reservation.place = place
            self._reservations.add(reservation)
            self._session.flush()
            return reservation

    def update(self, trip_id: UUID, reservation_id: UUID, data: ReservationUpdate) -> Reservation:
        with self._session.begin():
            trip = self._get_trip(trip_id, for_update=True)
            reservation = self._find_reservation(trip, reservation_id)

            if "reservation_type" in data.model_fields_set:
                if data.reservation_type is None:
                    raise DomainError(
                        "invalid_reservation_type", "reservation_type cannot be null."
                    )
                reservation.reservation_type = data.reservation_type
            if "status" in data.model_fields_set:
                if data.status is None:
                    raise DomainError("invalid_reservation_status", "status cannot be null.")
                reservation.status = data.status
            if "provider_name" in data.model_fields_set:
                if data.provider_name is None:
                    raise DomainError("invalid_provider_name", "provider_name cannot be null.")
                reservation.provider_name = data.provider_name
            if "confirmation_code" in data.model_fields_set:
                reservation.confirmation_code = data.confirmation_code
            if "source_reference" in data.model_fields_set:
                reservation.source_reference = data.source_reference
            if "notes" in data.model_fields_set:
                reservation.notes = data.notes
            if "place_id" in data.model_fields_set:
                reservation.place = self._get_place(data.place_id)
            if {
                "start_date",
                "start_time",
                "end_date",
                "end_time",
            }.issubset(data.model_fields_set):
                reservation.starts_at, reservation.ends_at = local_datetime_for_reservation(
                    data.start_date,
                    data.start_time,
                    data.end_date,
                    data.end_time,
                    trip.timezone,
                )
            self._session.flush()
            return reservation

    def delete(self, trip_id: UUID, reservation_id: UUID) -> None:
        with self._session.begin():
            trip = self._get_trip(trip_id, for_update=True)
            reservation = self._find_reservation(trip, reservation_id)
            self._reservations.delete(reservation)
            self._session.flush()

    def _get_trip(self, trip_id: UUID, *, for_update: bool = False) -> Trip:
        trip = self._trips.get(owner_id=self._owner_id, trip_id=trip_id, for_update=for_update)
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
    def _find_reservation(trip: Trip, reservation_id: UUID) -> Reservation:
        for reservation in trip.reservations:
            if reservation.id == reservation_id:
                return reservation
        raise not_found("reservation")

    @staticmethod
    def _sort_key(reservation: Reservation) -> tuple[bool, object, str, str, UUID]:
        return (
            reservation.starts_at is None,
            reservation.starts_at or date.max,
            reservation.status,
            reservation.provider_name.casefold(),
            reservation.id,
        )
