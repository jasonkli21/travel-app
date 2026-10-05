from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from personal_travel.api.schemas import ManualSavedPlaceCreate, SavedPlaceCreate, SavedPlaceUpdate
from personal_travel.domain.upstream_comparisons import VerifiedPlaceSource
from personal_travel.models.place import Place
from personal_travel.models.reservation import SavedPlace
from personal_travel.models.trip import Trip
from personal_travel.repositories.places import SqlAlchemyPlaceRepository
from personal_travel.repositories.saved_places import SqlAlchemySavedPlaceRepository
from personal_travel.repositories.trips import SqlAlchemyTripRepository
from personal_travel.services.errors import DomainError, not_found
from personal_travel.services.revisions import require_expected_revision


class SavedPlaceService:
    def __init__(self, session: Session, owner_id: str) -> None:
        self._session = session
        self._owner_id = owner_id
        self._trips = SqlAlchemyTripRepository(session)
        self._places = SqlAlchemyPlaceRepository(session)
        self._saved_places = SqlAlchemySavedPlaceRepository(session)

    def list(self, trip_id: UUID) -> list[SavedPlace]:
        trip = self._get_trip(trip_id)
        return sorted(trip.saved_places, key=lambda saved: (saved.place.name.casefold(), saved.id))

    def create(
        self,
        trip_id: UUID,
        data: SavedPlaceCreate,
        *,
        expected_revision: int | None = None,
    ) -> SavedPlace:
        with self._session.begin():
            trip = self._get_trip(trip_id, for_update=True)
            require_expected_revision(trip.revision, expected_revision, aggregate="trip")
            place = self._places.get(owner_id=self._owner_id, place_id=data.place_id)
            if place is None:
                raise not_found("place")
            if any(saved.place_id == place.id for saved in trip.saved_places):
                raise DomainError(
                    "saved_place_exists",
                    "this place is already saved for the trip.",
                    status_code=409,
                )
            saved_place = SavedPlace(
                owner_id=self._owner_id,
                trip_id=trip.id,
                place_id=place.id,
                note=data.note,
            )
            saved_place.place = place
            if saved_place not in trip.saved_places:
                trip.saved_places.append(saved_place)
            self._saved_places.add(saved_place)
            trip.revision += 1
            self._session.flush()
            return saved_place

    def create_manual(
        self,
        trip_id: UUID,
        data: ManualSavedPlaceCreate,
        *,
        expected_revision: int | None = None,
    ) -> SavedPlace:
        with self._session.begin():
            trip = self._get_trip(trip_id, for_update=True)
            require_expected_revision(trip.revision, expected_revision, aggregate="trip")
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
            saved_place = SavedPlace(
                owner_id=self._owner_id,
                trip_id=trip.id,
                place_id=place.id,
                note=data.note,
            )
            saved_place.place = place
            trip.saved_places.append(saved_place)
            self._saved_places.add(saved_place)
            trip.revision += 1
            self._session.flush()
            return saved_place

    def create_from_comparison(
        self,
        trip_id: UUID,
        data: ManualSavedPlaceCreate,
        source: VerifiedPlaceSource,
        provider_place_id: str,
        *,
        expected_revision: int | None = None,
        reference_place_id: UUID,
        reference_place_revision: int,
        reference_latitude: float,
        reference_longitude: float,
        evidence_expires_at: datetime,
    ) -> SavedPlace:
        """Persist one explicitly reviewed, source-verified comparison candidate."""
        if source.provider_place_id != provider_place_id:
            raise DomainError(
                "comparison_candidate_invalid",
                "The verified source identity does not match the selected candidate.",
                status_code=409,
            )
        try:
            with self._session.begin():
                trip = self._get_trip(trip_id, for_update=True)
                existing_saved = next(
                    (
                        saved
                        for saved in trip.saved_places
                        if saved.place.provider == source.provider
                        and saved.place.provider_place_id == provider_place_id
                    ),
                    None,
                )
                if existing_saved is not None:
                    return existing_saved
                require_expected_revision(trip.revision, expected_revision, aggregate="trip")

                reference_place = self._session.scalar(
                    select(Place)
                    .where(
                        Place.owner_id == self._owner_id,
                        Place.id == reference_place_id,
                    )
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                trip_place_ids = (
                    {
                        item.place.id
                        for day in trip.days
                        for item in day.items
                        if item.place is not None
                    }
                    | {saved.place.id for saved in trip.saved_places if saved.place is not None}
                    | {
                        reservation.place.id
                        for reservation in trip.reservations
                        if reservation.status != "cancelled" and reservation.place is not None
                    }
                )
                if (
                    reference_place is None
                    or reference_place_id not in trip_place_ids
                    or reference_place.revision != reference_place_revision
                    or reference_place.latitude is None
                    or reference_place.longitude is None
                    or float(reference_place.latitude) != reference_latitude
                    or float(reference_place.longitude) != reference_longitude
                ):
                    raise DomainError(
                        "comparison_context_stale",
                        "The comparison center changed. Run a new comparison before saving.",
                        status_code=409,
                    )
                place = self._session.scalar(
                    select(Place).where(
                        Place.owner_id == self._owner_id,
                        Place.provider == source.provider,
                        Place.provider_place_id == provider_place_id,
                    )
                )
                if evidence_expires_at <= datetime.now(UTC):
                    raise DomainError(
                        "comparison_evidence_expired",
                        "The comparison evidence expired before it could be saved. "
                        "Run a new comparison.",
                        status_code=409,
                    )
                if place is None:
                    place = Place(
                        owner_id=self._owner_id,
                        name=data.name,
                        address=data.address,
                        category=data.category,
                        latitude=Decimal(str(data.latitude)),
                        longitude=Decimal(str(data.longitude)),
                        provider=source.provider,
                        provider_place_id=provider_place_id,
                        provider_source_name=source.provider_source_name,
                        provider_source_attribution=source.provider_source_attribution,
                        provider_source_license=source.provider_source_license,
                        provider_source_url=source.provider_source_url,
                    )
                    self._places.add(place)
                    self._session.flush()

                saved_place = SavedPlace(
                    owner_id=self._owner_id,
                    trip_id=trip.id,
                    place_id=place.id,
                    note=data.note,
                )
                saved_place.place = place
                trip.saved_places.append(saved_place)
                self._saved_places.add(saved_place)
                trip.revision += 1
                self._session.flush()
                return saved_place
        except IntegrityError:
            raise DomainError(
                "comparison_candidate_conflict",
                "The place changed while it was being saved. Reload the trip before trying again.",
                status_code=409,
            ) from None

    def update(
        self,
        trip_id: UUID,
        saved_place_id: UUID,
        data: SavedPlaceUpdate,
        *,
        expected_revision: int | None = None,
    ) -> SavedPlace:
        with self._session.begin():
            trip = self._get_trip(trip_id, for_update=True)
            require_expected_revision(trip.revision, expected_revision, aggregate="trip")
            saved_place = self._find_saved_place(trip, saved_place_id)
            if "note" in data.model_fields_set and saved_place.note != data.note:
                saved_place.note = data.note
                trip.revision += 1
            self._session.flush()
            return saved_place

    def delete(
        self,
        trip_id: UUID,
        saved_place_id: UUID,
        *,
        expected_revision: int | None = None,
    ) -> None:
        with self._session.begin():
            trip = self._get_trip(trip_id, for_update=True)
            require_expected_revision(trip.revision, expected_revision, aggregate="trip")
            saved_place = self._find_saved_place(trip, saved_place_id)
            self._saved_places.delete(saved_place)
            self._session.flush()
            trip.revision += 1
            self._session.flush()

    def _get_trip(self, trip_id: UUID, *, for_update: bool = False) -> Trip:
        trip = self._trips.get(owner_id=self._owner_id, trip_id=trip_id, for_update=for_update)
        if trip is None:
            raise not_found("trip")
        return trip

    @staticmethod
    def _find_saved_place(trip: Trip, saved_place_id: UUID) -> SavedPlace:
        for saved_place in trip.saved_places:
            if saved_place.id == saved_place_id:
                return saved_place
        raise not_found("saved place")
