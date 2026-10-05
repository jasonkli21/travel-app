from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from math import asin, cos, radians, sin, sqrt
from typing import TYPE_CHECKING
from uuid import UUID, uuid5

from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from personal_travel.api.schemas import (
    ManualSavedPlaceCreate,
    TravelComparisonCandidateResponse,
    TravelComparisonCandidateSaveRequest,
    TravelComparisonConstraintResponse,
    TravelComparisonRequest,
    TravelComparisonResponse,
    TravelComparisonSourceResponse,
)
from personal_travel.api.schemas.travel_comparisons import TravelComparisonState
from personal_travel.auth.contracts import PersonalAIAuthContext
from personal_travel.clients.personal_ai import (
    PersonalAIClient,
    PersonalAIComparisonError,
)
from personal_travel.config import Settings
from personal_travel.domain.upstream_comparisons import (
    TRAVEL_COMPARISON_CATEGORIES,
    TravelComparisonCategory,
    UpstreamTravelComparison,
    VerifiedPlaceSource,
    _ComparisonCell,
    _DomainClaim,
    _LocationValue,
    _TextValue,
    _TypedValue,
    require_accepted_travel_contract,
)
from personal_travel.models.place import Place
from personal_travel.models.reservation import SavedPlace
from personal_travel.models.trip import Trip
from personal_travel.services.errors import DomainError, not_found
from personal_travel.services.saved_places import SavedPlaceService
from personal_travel.services.trips import TripService

if TYPE_CHECKING:
    from personal_travel.domain.upstream_comparisons import _ProviderObservation


@dataclass(frozen=True)
class _TripComparisonSnapshot:
    trip_revision: int
    reference_place_id: UUID
    reference_place_revision: int
    reference_place_name: str
    latitude: float
    longitude: float


@dataclass(frozen=True)
class _ValidatedCandidate:
    response: TravelComparisonCandidateResponse
    place_source: VerifiedPlaceSource | None
    provider_place_id: str | None


class TravelComparisonService:
    def __init__(
        self,
        session: Session,
        owner_id: str,
        settings: Settings,
        *,
        client: PersonalAIClient | None = None,
        auth_context: PersonalAIAuthContext | None = None,
    ) -> None:
        self._session = session
        self._owner_id = owner_id
        self._settings = settings
        self._client = client or PersonalAIClient(auth_context=auth_context)

    async def compare(
        self, trip_id: UUID, request: TravelComparisonRequest
    ) -> TravelComparisonResponse:
        self._require_enabled()
        snapshot = await run_in_threadpool(
            self._comparison_snapshot, trip_id, request.reference_place_id
        )
        category = TRAVEL_COMPARISON_CATEGORIES[request.category]
        query = f"{category.search_hint}: {request.query}"
        if len(query) > 300:
            raise DomainError(
                "comparison_query_too_long",
                "Shorten the search text for this comparison.",
                status_code=422,
            )
        constraints = [
            {
                "id": str(uuid5(request.idempotency_key, "category")),
                "attribute": "place_type",
                "operator": "set",
                "allowed_values": [
                    {"kind": "text", "value": value} for value in category.allowed_place_types
                ],
                "required": True,
                "missing_policy": "fail_closed",
                "source": "user",
            },
            {
                "id": str(uuid5(request.idempotency_key, "distance")),
                "attribute": "location",
                "operator": "geospatial",
                "value": {
                    "kind": "location",
                    "latitude": snapshot.latitude,
                    "longitude": snapshot.longitude,
                },
                "radius_km": request.radius_km,
                "required": True,
                "missing_policy": "fail_closed",
                "source": "user",
            },
        ]
        payload: dict[str, object] = {
            "schema_version": "domain-lookup-v1",
            "idempotency_key": str(request.idempotency_key),
            "query": query,
            "constraints": constraints,
            "preferences": [],
            "max_results": request.max_results,
        }
        try:
            upstream = await self._client.lookup_travel_comparison(payload=payload)
            require_accepted_travel_contract(upstream, self._owner_id)
            if len(upstream.comparison.rows) > request.max_results:
                raise ValueError("comparison exceeded the requested result count")
            self._validate_request_footprint(upstream, request, snapshot)
            return _comparison_response(
                upstream,
                owner_id=self._owner_id,
                category=request.category,
                trip_revision=snapshot.trip_revision,
                reference_place_id=snapshot.reference_place_id,
                reference_place_revision=snapshot.reference_place_revision,
                reference_place_name=snapshot.reference_place_name,
                center=(snapshot.latitude, snapshot.longitude),
                radius_km=request.radius_km,
                max_results=request.max_results,
            )
        except PersonalAIComparisonError as error:
            raise DomainError(
                "research_comparison_unavailable",
                "The comparison could not be confirmed. Retry the same request to check "
                "its existing key.",
                status_code=503,
            ) from error
        except ValueError as error:
            raise DomainError(
                "research_comparison_invalid",
                "The comparison service returned data this travel app cannot safely use.",
                status_code=503,
            ) from error

    async def save_candidate(
        self,
        trip_id: UUID,
        comparison_id: UUID,
        candidate_id: UUID,
        request: TravelComparisonCandidateSaveRequest,
        *,
        expected_revision: int | None,
    ) -> SavedPlace:
        self._require_enabled()
        try:
            snapshot = await run_in_threadpool(
                self._comparison_snapshot, trip_id, request.reference_place_id
            )
            if (
                snapshot.trip_revision != request.trip_revision
                or snapshot.reference_place_revision != request.reference_place_revision
                or (expected_revision is not None and expected_revision != snapshot.trip_revision)
            ):
                raise DomainError(
                    "comparison_context_stale",
                    "The trip or comparison center changed. Run a new comparison before saving.",
                    status_code=409,
                )
            upstream = await self._client.get_travel_comparison(comparison_id)
            require_accepted_travel_contract(upstream, self._owner_id)
            if upstream.comparison.id != comparison_id:
                raise ValueError("comparison detail did not match its requested ID")
            category, center, radius = _comparison_scope(upstream)
            if (snapshot.latitude, snapshot.longitude) != center:
                raise DomainError(
                    "comparison_context_stale",
                    "The trip or comparison center changed. Run a new comparison before saving.",
                    status_code=409,
                )
            validated = _validated_candidate(
                upstream,
                owner_id=self._owner_id,
                category=category,
                center=center,
                radius_km=radius,
                row_id=candidate_id,
                now=datetime.now(UTC),
            )
            if not validated.response.eligible:
                raise DomainError(
                    "comparison_candidate_ineligible",
                    "Only a current candidate that meets the category and distance "
                    "requirements can be saved.",
                    status_code=409,
                )
            if validated.place_source is None or validated.provider_place_id is None:
                raise DomainError(
                    "comparison_candidate_not_persistable",
                    "This synthetic or unsupported source can be reviewed, but cannot be "
                    "saved as a sourced place.",
                    status_code=409,
                )
            if validated.response.latitude is None or validated.response.longitude is None:
                raise DomainError(
                    "comparison_candidate_invalid",
                    "The verified source has no permanent place coordinates.",
                    status_code=409,
                )
            place_data = ManualSavedPlaceCreate(
                name=request.name,
                address=request.address,
                category=request.category or validated.response.place_type,
                latitude=validated.response.latitude,
                longitude=validated.response.longitude,
                note=request.note,
            )
            return await run_in_threadpool(
                SavedPlaceService(self._session, self._owner_id).create_from_comparison,
                trip_id,
                place_data,
                validated.place_source,
                validated.provider_place_id,
                expected_revision=snapshot.trip_revision,
            )
        except PersonalAIComparisonError as error:
            raise DomainError(
                "comparison_candidate_unavailable",
                "The source comparison is no longer available for verification.",
                status_code=503,
            ) from error
        except ValueError as error:
            raise DomainError(
                "comparison_candidate_invalid",
                "The source comparison failed its category, ownership, freshness, or "
                "rights checks.",
                status_code=409,
            ) from error

    def _require_enabled(self) -> None:
        if not self._settings.personal_ai_comparisons_enabled:
            raise DomainError(
                "research_comparison_unavailable",
                "Travel comparisons are unavailable until enabled for this service.",
                status_code=503,
            )

    def _comparison_snapshot(
        self, trip_id: UUID, reference_place_id: UUID
    ) -> _TripComparisonSnapshot:
        try:
            trip = TripService(self._session, self._owner_id).get(trip_id)
            place = _find_trip_place(trip, reference_place_id, self._owner_id)
            if place.latitude is None or place.longitude is None:
                raise DomainError(
                    "comparison_location_required",
                    "Choose a trip place with coordinates as the comparison center.",
                    status_code=422,
                )
            return _TripComparisonSnapshot(
                trip_revision=trip.revision,
                reference_place_id=place.id,
                reference_place_revision=place.revision,
                reference_place_name=place.name,
                latitude=float(place.latitude),
                longitude=float(place.longitude),
            )
        finally:
            self._session.rollback()

    def _validate_request_footprint(
        self,
        upstream: UpstreamTravelComparison,
        request: TravelComparisonRequest,
        snapshot: _TripComparisonSnapshot,
    ) -> None:
        category = TRAVEL_COMPARISON_CATEGORIES[request.category]
        constraints = upstream.comparison.constraints
        place_type = [item for item in constraints if item.attribute == "place_type"]
        location = [item for item in constraints if item.attribute == "location"]
        if len(place_type) != 1 or len(location) != 1 or len(constraints) != 2:
            raise ValueError("unexpected travel comparison constraints")
        category_constraint = place_type[0]
        location_constraint = location[0]
        allowed = _text_values(category_constraint.allowed_values)
        location_value = location_constraint.value
        if not isinstance(location_value, _LocationValue):
            raise ValueError("comparison location constraint has no typed center")
        if (
            category_constraint.operator != "set"
            or allowed != category.allowed_place_types
            or not category_constraint.required
            or category_constraint.missing_policy != "fail_closed"
            or category_constraint.source != "user"
            or location_constraint.operator != "geospatial"
            or location_value.latitude != snapshot.latitude
            or location_value.longitude != snapshot.longitude
            or location_constraint.radius_km != request.radius_km
            or not location_constraint.required
            or location_constraint.missing_policy != "fail_closed"
            or location_constraint.source != "user"
            or upstream.comparison.preferences
        ):
            raise ValueError("travel comparison does not match the submitted footprint")


def _find_trip_place(trip: Trip, place_id: UUID, owner_id: str) -> Place:
    places = {
        item.place.id: item.place
        for day in trip.days
        for item in day.items
        if item.place is not None
    }
    places.update(
        {saved.place.id: saved.place for saved in trip.saved_places if saved.place is not None}
    )
    place = places.get(place_id)
    if place is None or place.owner_id != owner_id:
        raise not_found("trip place")
    return place


def _comparison_scope(
    upstream: UpstreamTravelComparison,
) -> tuple[TravelComparisonCategory, tuple[float, float], float]:
    constraints = upstream.comparison.constraints
    place_type = [item for item in constraints if item.attribute == "place_type"]
    location = [item for item in constraints if item.attribute == "location"]
    if len(constraints) != 2 or len(place_type) != 1 or len(location) != 1:
        raise ValueError("comparison constraints do not match the accepted travel contract")
    allowed = _text_values(place_type[0].allowed_values)
    category = next(
        (
            name
            for name, spec in TRAVEL_COMPARISON_CATEGORIES.items()
            if spec.allowed_place_types == allowed
        ),
        None,
    )
    center_value = location[0].value
    if (
        category is None
        or place_type[0].operator != "set"
        or not place_type[0].required
        or place_type[0].missing_policy != "fail_closed"
        or place_type[0].source != "user"
        or location[0].operator != "geospatial"
        or not isinstance(center_value, _LocationValue)
        or location[0].radius_km is None
        or not location[0].required
        or location[0].missing_policy != "fail_closed"
        or location[0].source != "user"
    ):
        raise ValueError("comparison scope is unsupported")
    return category, (center_value.latitude, center_value.longitude), location[0].radius_km


def _text_values(values: tuple[_TypedValue, ...]) -> tuple[str, ...]:
    if any(not isinstance(value, _TextValue) for value in values):
        raise ValueError("comparison category values must be typed text")
    return tuple(value.value for value in values if isinstance(value, _TextValue))


def _comparison_response(
    upstream: UpstreamTravelComparison,
    *,
    owner_id: str,
    category: TravelComparisonCategory,
    trip_revision: int,
    reference_place_id: UUID,
    reference_place_revision: int,
    reference_place_name: str,
    center: tuple[float, float],
    radius_km: float,
    max_results: int,
) -> TravelComparisonResponse:
    now = datetime.now(UTC)
    candidates = [
        _validated_candidate(
            upstream,
            owner_id=owner_id,
            category=category,
            center=center,
            radius_km=radius_km,
            row_id=row.candidate_id,
            now=now,
        ).response
        for row in upstream.comparison.rows[:max_results]
    ]
    candidates.sort(
        key=lambda item: (
            not item.eligible,
            item.rank is None,
            item.rank if item.rank is not None else 10_000,
            item.distance_km if item.distance_km is not None else 100_000,
            item.name.casefold(),
        )
    )
    expiries = [source.expires_at for candidate in candidates for source in candidate.sources]
    expires_at = min(expiries) if expiries else None
    state: TravelComparisonState = upstream.comparison.state
    if expires_at is not None and expires_at <= now:
        state = "expired"
        candidates = []
    elif not candidates and state not in {"no_verified_match", "research_needed"}:
        state = "insufficient"
    return TravelComparisonResponse(
        comparison_id=upstream.comparison.id,
        category=category,
        state=state,
        trip_revision=trip_revision,
        reference_place_id=reference_place_id,
        reference_place_revision=reference_place_revision,
        reference_place_name=reference_place_name,
        radius_km=radius_km,
        generated_at=upstream.comparison.rendered_at,
        expires_at=expires_at,
        candidates=tuple(candidates),
    )


def _validated_candidate(
    upstream: UpstreamTravelComparison,
    *,
    owner_id: str,
    category: TravelComparisonCategory,
    center: tuple[float, float],
    radius_km: float,
    row_id: UUID,
    now: datetime,
) -> _ValidatedCandidate:
    spec = TRAVEL_COMPARISON_CATEGORIES[category]
    row = next((item for item in upstream.comparison.rows if item.candidate_id == row_id), None)
    if row is None:
        raise ValueError("comparison candidate is not in the source comparison")
    claims = {claim.claim_id: claim for claim in upstream.domain_claims}
    observations_by_evidence = {item.evidence_id: item for item in upstream.provider_observations}
    cells = {cell.field: cell for cell in row.cells}
    type_cell = cells.get("place_type")
    location_cell = cells.get("location")
    place_type_claim = _current_claim(type_cell, claims, "place_type", now)
    location_claim = _current_claim(location_cell, claims, "location", now)
    place_type_value = place_type_claim.typed_value if place_type_claim is not None else None
    location_value = location_claim.typed_value if location_claim is not None else None
    if (
        place_type_claim is None
        or not isinstance(place_type_value, _TextValue)
        or location_claim is None
        or not isinstance(location_value, _LocationValue)
    ):
        raise ValueError("comparison candidate lacks current typed place evidence")

    place_type = place_type_value.value.strip().casefold()
    latitude = location_value.latitude
    longitude = location_value.longitude
    distance = _distance_km(center, (latitude, longitude))
    category_ok = place_type in spec.allowed_place_types
    distance_ok = distance <= radius_km
    if type_cell is None or location_cell is None:
        raise ValueError("comparison candidate is missing typed field cells")
    evidence_ids = set(place_type_claim.evidence_ids) | set(location_claim.evidence_ids)
    sources: dict[UUID, TravelComparisonSourceResponse] = {}
    place_source: VerifiedPlaceSource | None = None
    provider_place_id: str | None = None
    only_osm_sources = True
    osm_place_ids: set[str] = set()
    for cell, claim in ((type_cell, place_type_claim), (location_cell, location_claim)):
        for evidence_id in claim.evidence_ids:
            observation = observations_by_evidence.get(evidence_id)
            citation = next(
                (item for item in cell.sources if item.evidence_id == evidence_id), None
            )
            if (
                observation is None
                or observation.owner_id != owner_id
                or citation is None
                or citation.source_observation_id != observation.source_observation_id
                or citation.url != observation.url
                or citation.observed_at != observation.observed_at
                or citation.expires_at != observation.expires_at
                or citation.attribution != observation.attribution
                or citation.policy_url != observation.policy_url
            ):
                raise ValueError("candidate claim and source citation do not correlate")
            if observation.observed_at > now or observation.expires_at <= now:
                raise ValueError("comparison source is not current")
            current_source = TravelComparisonSourceResponse(
                evidence_id=evidence_id,
                source_observation_id=observation.source_observation_id,
                provider=observation.provider,
                url=observation.url,
                title=observation.title,
                attribution=observation.attribution,
                policy_url=observation.policy_url,
                observed_at=observation.observed_at,
                expires_at=min(observation.expires_at, claim.expires_at),
            )
            previous = sources.get(evidence_id)
            if previous is not None and previous != current_source:
                raise ValueError("duplicate candidate evidence is inconsistent")
            sources[evidence_id] = current_source
            if observation.provider == "osm_nominatim":
                place_source, provider_place_id = _verified_osm_source(observation, place_type)
                osm_place_ids.add(provider_place_id)
            else:
                only_osm_sources = False

    if not sources:
        raise ValueError("comparison candidate has no source")
    eligible = bool(row.eligible and category_ok and distance_ok)
    exclusions = list(row.exclusion_reasons)
    if not category_ok:
        exclusions.append("The source place type does not match this category.")
    if not distance_ok:
        exclusions.append("The candidate is outside the selected distance limit.")
    if not row.eligible and not exclusions:
        exclusions.append("The upstream hard-constraint check did not pass.")
    constraints = (
        TravelComparisonConstraintResponse(
            name="category",
            label="Place category",
            outcome="pass" if category_ok else "fail",
            detail=(
                f"Source type: {place_type}."
                if category_ok
                else f"Source type {place_type!r} is outside the selected category."
            ),
        ),
        TravelComparisonConstraintResponse(
            name="distance",
            label=f"Within {radius_km:g} km",
            outcome="pass" if distance_ok else "fail",
            detail=f"{distance:.1f} km from {radius_km:g} km limit.",
        ),
    )
    address = next(
        (sources[evidence_id].title for evidence_id in evidence_ids if sources[evidence_id].title),
        None,
    )
    response = TravelComparisonCandidateResponse(
        candidate_id=row.candidate_id,
        name=row.name,
        category=category,
        address=address,
        place_type=place_type,
        latitude=latitude,
        longitude=longitude,
        distance_km=distance,
        eligible=eligible,
        rank=row.rank if eligible else None,
        score=row.score if eligible else None,
        exclusion_reasons=tuple(exclusions[:10]),
        constraints=constraints,
        sources=tuple(
            sorted(sources.values(), key=lambda item: (item.observed_at, str(item.evidence_id)))
        ),
    )
    return _ValidatedCandidate(
        response=response,
        place_source=(
            place_source if eligible and only_osm_sources and len(osm_place_ids) == 1 else None
        ),
        provider_place_id=(
            provider_place_id if eligible and only_osm_sources and len(osm_place_ids) == 1 else None
        ),
    )


def _current_claim(
    cell: _ComparisonCell | None,
    claims: dict[UUID, _DomainClaim],
    attribute: str,
    now: datetime,
) -> _DomainClaim | None:
    if cell is None or cell.status != "verified" or not cell.claim_ids:
        return None
    matching = [
        claims[claim_id]
        for claim_id in cell.claim_ids
        if claim_id in claims and claims[claim_id].attribute == attribute
    ]
    current = [
        claim
        for claim in matching
        if claim.domain_id == "travel"
        and claim.observed_at <= now
        and claim.expires_at > now
        and set(claim.evidence_ids)
    ]
    if len(current) != 1:
        return None
    return current[0]


def _verified_osm_source(
    observation: _ProviderObservation, expected_place_type: str
) -> tuple[VerifiedPlaceSource, str]:
    if (
        observation.provider != "osm_nominatim"
        or observation.entity_kind != expected_place_type
        or not observation.provider_object_id
        or not observation.policy_url
        or observation.attribution != "© OpenStreetMap contributors, ODbL 1.0"
    ):
        raise ValueError("OpenStreetMap source metadata is incomplete")
    provider_place_id = observation.provider_object_id
    expected_url = f"https://www.openstreetmap.org/{provider_place_id}"
    if observation.url != expected_url or observation.policy_url != (
        "https://operations.osmfoundation.org/policies/nominatim/"
    ):
        raise ValueError("OpenStreetMap source URL does not match its provider identity")
    source = VerifiedPlaceSource(
        provider="osm_nominatim",
        provider_place_id=provider_place_id,
        provider_source_name="OpenStreetMap",
        provider_source_attribution="© OpenStreetMap contributors, ODbL 1.0",
        provider_source_license="ODbL 1.0",
        provider_source_url=observation.url,
        policy_url="https://operations.osmfoundation.org/policies/nominatim/",
    )
    return source, provider_place_id


def _distance_km(left: tuple[float, float], right: tuple[float, float]) -> float:
    lat1, lon1 = map(radians, left)
    lat2, lon2 = map(radians, right)
    delta_lat = lat2 - lat1
    delta_lon = lon2 - lon1
    haversine = sin(delta_lat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(delta_lon / 2) ** 2
    return 6371.0088 * 2 * asin(sqrt(min(1.0, haversine)))
