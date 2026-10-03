from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from personal_travel.api.schemas import (
    LogisticsEstimateRequest,
    LogisticsEstimateResponse,
    LogisticsLegResponse,
    PlaceImportRequest,
    PlaceSearchResponse,
)
from personal_travel.clients.geoapify import GeoapifyClient, GeoapifyClientError
from personal_travel.models.itinerary import ItineraryItem
from personal_travel.models.place import Place
from personal_travel.models.reservation import SavedPlace
from personal_travel.models.trip import Trip, TripDay
from personal_travel.repositories.places import SqlAlchemyPlaceRepository
from personal_travel.repositories.saved_places import SqlAlchemySavedPlaceRepository
from personal_travel.repositories.trips import SqlAlchemyTripRepository
from personal_travel.services.errors import DomainError, not_found


@dataclass(frozen=True)
class _EligibleLeg:
    origin_item_id: UUID
    origin_title: str
    origin_ends_at: datetime
    origin_coordinates: tuple[float, float]
    destination_item_id: UUID
    destination_title: str
    destination_starts_at: datetime
    destination_coordinates: tuple[float, float]


def has_logistics_warning(
    available_gap_seconds: int,
    duration_seconds: int,
    buffer_minutes: int,
) -> bool:
    return available_gap_seconds < duration_seconds + buffer_minutes * 60


class LocationService:
    def __init__(
        self,
        session: Session,
        owner_id: str,
        geoapify: GeoapifyClient | None = None,
    ) -> None:
        self._session = session
        self._owner_id = owner_id
        self._geoapify = geoapify or GeoapifyClient()
        self._trips = SqlAlchemyTripRepository(session)
        self._places = SqlAlchemyPlaceRepository(session)
        self._saved_places = SqlAlchemySavedPlaceRepository(session)

    async def search_places(
        self,
        trip_id: UUID,
        query: str,
        *,
        limit: int,
    ) -> list[PlaceSearchResponse]:
        normalized_query = query.strip()
        if len(normalized_query) < 2:
            raise DomainError("invalid_search_query", "Enter at least two characters to search.")
        bias = await run_in_threadpool(self._search_bias, trip_id)
        try:
            matches = await self._geoapify.search_places(
                query=normalized_query,
                limit=limit,
                bias=bias,
            )
        except GeoapifyClientError as exc:
            raise self._provider_error(exc) from exc
        return [PlaceSearchResponse.model_validate(match.__dict__) for match in matches]

    def _search_bias(self, trip_id: UUID) -> tuple[float, float] | None:
        try:
            return self._trip_center(self._get_trip(trip_id))
        finally:
            self._session.rollback()

    def import_place(self, trip_id: UUID, data: PlaceImportRequest) -> SavedPlace:
        try:
            with self._session.begin():
                trip = self._get_trip(trip_id, for_update=True)
                place = self._places.get_by_provider_identity(
                    owner_id=self._owner_id,
                    provider="geoapify",
                    provider_place_id=data.provider_place_id,
                )
                if place is None:
                    place = Place(
                        owner_id=self._owner_id,
                        provider="geoapify",
                        provider_place_id=data.provider_place_id,
                        name=data.name,
                        address=data.address,
                        category=data.category,
                        latitude=Decimal(str(data.latitude)),
                        longitude=Decimal(str(data.longitude)),
                        provider_source_name=data.provider_source_name,
                        provider_source_attribution=data.provider_source_attribution,
                        provider_source_license=data.provider_source_license,
                        provider_source_url=data.provider_source_url,
                    )
                    self._places.add(place)
                    self._session.flush()
                return self._save_candidate(trip, place, note=data.note)
        except IntegrityError:
            # Imports to different trips lock different trip rows. If both owners'
            # transactions missed the same provider identity, the unique place key
            # elects one insert winner. Reload that winner, then finish the other
            # trip's candidate link in a fresh transaction.
            self._session.rollback()
            with self._session.begin():
                trip = self._get_trip(trip_id, for_update=True)
                place = self._places.get_by_provider_identity(
                    owner_id=self._owner_id,
                    provider="geoapify",
                    provider_place_id=data.provider_place_id,
                )
                if place is None:
                    raise
                return self._save_candidate(trip, place, note=data.note)

    def _save_candidate(self, trip: Trip, place: Place, *, note: str | None) -> SavedPlace:
        existing_saved = next(
            (saved for saved in trip.saved_places if saved.place_id == place.id),
            None,
        )
        if existing_saved is not None:
            return existing_saved

        saved_place = SavedPlace(
            owner_id=self._owner_id,
            trip_id=trip.id,
            place_id=place.id,
            note=note,
        )
        saved_place.place = place
        trip.saved_places.append(saved_place)
        self._saved_places.add(saved_place)
        self._session.flush()
        return saved_place

    async def estimate_logistics(
        self,
        trip_id: UUID,
        data: LogisticsEstimateRequest,
    ) -> LogisticsEstimateResponse:
        day_id, eligible_legs = await run_in_threadpool(self._logistics_snapshot, trip_id, data)
        leg_groups = self._group_legs(eligible_legs)
        try:
            async with asyncio.timeout(30):
                return await self._estimate_groups(day_id, leg_groups, data)
        except TimeoutError:
            raise DomainError(
                "location_provider_timeout",
                "Route estimates exceeded the request deadline.",
                status_code=503,
            ) from None

    def _logistics_snapshot(
        self, trip_id: UUID, data: LogisticsEstimateRequest
    ) -> tuple[UUID, list[_EligibleLeg]]:
        try:
            trip = self._get_trip(trip_id)
            day = next((candidate for candidate in trip.days if candidate.id == data.day_id), None)
            if day is None:
                raise not_found("trip day")
            legs = self._eligible_legs(day)
            if len(legs) > 50:
                raise DomainError(
                    "too_many_route_legs", "Estimate at most 50 eligible transfers in one request."
                )
            return day.id, legs
        finally:
            # Project and release the session in the worker thread. Sync SQL
            # never blocks the event loop or remains open across provider calls.
            self._session.rollback()

    async def _estimate_groups(
        self, day_id: UUID, leg_groups: list[list[_EligibleLeg]], data: LogisticsEstimateRequest
    ) -> LogisticsEstimateResponse:
        result_legs: list[LogisticsLegResponse] = []
        for group in leg_groups:
            waypoints = [group[0].origin_coordinates]
            waypoints.extend(leg.destination_coordinates for leg in group)
            try:
                provider_legs = await self._geoapify.route(waypoints=waypoints, mode=data.mode)
            except GeoapifyClientError as exc:
                raise self._provider_error(exc) from exc
            if len(provider_legs) != len(group):
                raise DomainError(
                    "location_provider_invalid_response",
                    "The location provider returned a different number of route legs.",
                    status_code=502,
                )
            for planned_leg, route_leg in zip(group, provider_legs, strict=True):
                gap_seconds = int(
                    (planned_leg.destination_starts_at - planned_leg.origin_ends_at).total_seconds()
                )
                warning = has_logistics_warning(
                    gap_seconds,
                    route_leg.duration_seconds,
                    data.buffer_minutes,
                )
                result_legs.append(
                    LogisticsLegResponse(
                        origin_item_id=planned_leg.origin_item_id,
                        origin_title=planned_leg.origin_title,
                        destination_item_id=planned_leg.destination_item_id,
                        destination_title=planned_leg.destination_title,
                        duration_seconds=route_leg.duration_seconds,
                        distance_meters=route_leg.distance_meters,
                        available_gap_seconds=gap_seconds,
                        buffer_minutes=data.buffer_minutes,
                        warning=warning,
                        geometry=route_leg.geometry,
                    )
                )

        return LogisticsEstimateResponse(
            day_id=day_id,
            mode=data.mode,
            buffer_minutes=data.buffer_minutes,
            generated_at=datetime.now(UTC),
            legs=result_legs,
        )

    def _get_trip(self, trip_id: UUID, *, for_update: bool = False) -> Trip:
        trip = self._trips.get(owner_id=self._owner_id, trip_id=trip_id, for_update=for_update)
        if trip is None:
            raise not_found("trip")
        return trip

    @staticmethod
    def _trip_center(trip: Trip) -> tuple[float, float] | None:
        coordinates: dict[UUID, tuple[float, float]] = {}
        for day in trip.days:
            for item in day.items:
                LocationService._add_coordinates(coordinates, item.place)
                if item.reservation is not None and item.reservation.status != "cancelled":
                    LocationService._add_coordinates(coordinates, item.reservation.place)
        for reservation in trip.reservations:
            if reservation.status != "cancelled":
                LocationService._add_coordinates(coordinates, reservation.place)
        for saved_place in trip.saved_places:
            LocationService._add_coordinates(coordinates, saved_place.place)
        if not coordinates:
            return None
        count = len(coordinates)
        return (
            sum(latitude for latitude, _ in coordinates.values()) / count,
            math.degrees(
                math.atan2(
                    sum(math.sin(math.radians(lon)) for _, lon in coordinates.values()),
                    sum(math.cos(math.radians(lon)) for _, lon in coordinates.values()),
                )
            ),
        )

    @staticmethod
    def _add_coordinates(result: dict[UUID, tuple[float, float]], place: Place | None) -> None:
        if place is None or place.latitude is None or place.longitude is None:
            return
        result[place.id] = (float(place.latitude), float(place.longitude))

    @classmethod
    def _eligible_legs(cls, day: TripDay) -> list[_EligibleLeg]:
        items = sorted(day.items, key=lambda item: item.sort_order)
        legs: list[_EligibleLeg] = []
        for origin, destination in zip(items, items[1:], strict=False):
            if (
                origin.status == "cancelled"
                or destination.status == "cancelled"
                or origin.ends_at is None
                or destination.starts_at is None
            ):
                continue
            origin_coordinates = cls._item_coordinates(origin)
            destination_coordinates = cls._item_coordinates(destination)
            if origin_coordinates is None or destination_coordinates is None:
                continue
            # These checks above establish both timestamps for each eligible leg.
            assert origin.ends_at is not None and destination.starts_at is not None
            legs.append(
                _EligibleLeg(
                    origin_item_id=origin.id,
                    origin_title=origin.title,
                    origin_ends_at=origin.ends_at,
                    origin_coordinates=origin_coordinates,
                    destination_item_id=destination.id,
                    destination_title=destination.title,
                    destination_starts_at=destination.starts_at,
                    destination_coordinates=destination_coordinates,
                )
            )
        return legs

    @staticmethod
    def _item_coordinates(item: ItineraryItem) -> tuple[float, float] | None:
        place = item.place
        if (place is None or place.latitude is None or place.longitude is None) and (
            item.reservation is not None
            and item.reservation.status != "cancelled"
            and item.reservation.place is not None
        ):
            place = item.reservation.place
        if place is None or place.latitude is None or place.longitude is None:
            return None
        return float(place.latitude), float(place.longitude)

    @staticmethod
    def _group_legs(legs: list[_EligibleLeg]) -> list[list[_EligibleLeg]]:
        groups: list[list[_EligibleLeg]] = []
        for leg in legs:
            # Bound waypoint count/URL length. Consecutive batches share the
            # joining point, so splitting cannot skip a transfer.
            if (
                groups
                and len(groups[-1]) < 9
                and groups[-1][-1].destination_item_id == leg.origin_item_id
            ):
                groups[-1].append(leg)
            else:
                groups.append([leg])
        return groups

    @staticmethod
    def _provider_error(exc: GeoapifyClientError) -> DomainError:
        return DomainError(exc.code, exc.message, status_code=exc.status_code)
