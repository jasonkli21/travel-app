from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4, uuid5

import pytest

from personal_travel.api.schemas.travel_comparisons import TravelComparisonRequest
from personal_travel.domain.upstream_comparisons import (
    TRAVEL_COMPARISON_CATEGORIES,
    _LocationValue,
    _TextValue,
)
from personal_travel.services.errors import DomainError
from personal_travel.services.travel_comparisons import (
    TravelComparisonService,
    _comparison_response,
    _distance_km,
    _find_trip_place,
    _validated_candidate,
)

OWNER = "phase7-test-owner"
CENTER = (35.0, 139.0)


def _observation(
    now: datetime,
    place_type: str,
    *,
    evidence_id: UUID | None = None,
    expires_at: datetime | None = None,
    provider: str = "osm_nominatim",
    object_id: str = "node/1234",
) -> SimpleNamespace:
    evidence_id = evidence_id or uuid4()
    source_observation_id = uuid4()
    return SimpleNamespace(
        evidence_id=evidence_id,
        source_observation_id=source_observation_id,
        owner_id=OWNER,
        provider=provider,
        provider_object_id=object_id if provider == "osm_nominatim" else None,
        entity_kind=place_type,
        adapter_version="fixture-v1",
        attribution=(
            "© OpenStreetMap contributors, ODbL 1.0" if provider == "osm_nominatim" else "Fixture"
        ),
        policy_url=(
            "https://operations.osmfoundation.org/policies/nominatim/"
            if provider == "osm_nominatim"
            else None
        ),
        url=(
            f"https://www.openstreetmap.org/{object_id}"
            if provider == "osm_nominatim"
            else "https://example.test/source"
        ),
        title="Fixture address",
        observed_at=now - timedelta(minutes=2),
        expires_at=expires_at or now + timedelta(hours=1),
    )


def _claim(
    field: str,
    value: object,
    observation: SimpleNamespace,
    *,
    expires_at: datetime | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        claim_id=uuid4(),
        domain_id="travel",
        attribute=field,
        typed_value=value,
        original_value=str(value),
        evidence_ids=(observation.evidence_id,),
        observed_at=observation.observed_at,
        expires_at=expires_at or observation.expires_at,
    )


def _citation(observation: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(
        evidence_id=observation.evidence_id,
        source_observation_id=observation.source_observation_id,
        url=observation.url,
        title=observation.title,
        attribution=observation.attribution,
        policy_url=observation.policy_url,
        observed_at=observation.observed_at,
        expires_at=observation.expires_at,
    )


def _cell(
    field: str, claim: SimpleNamespace | None, obs: SimpleNamespace | None, status: str = "verified"
) -> SimpleNamespace:
    return SimpleNamespace(
        field=field,
        label=field,
        value=None,
        status=status if claim else "missing",
        note=None,
        claim_ids=(claim.claim_id,) if claim else (),
        sources=(_citation(obs),) if claim and obs else (),
    )


def _row(
    now: datetime,
    *,
    name: str = "Fixture Cafe",
    place_type: str = "amenity/cafe",
    coordinates: tuple[float, float] = CENTER,
    type_status: str = "verified",
    location_status: str = "verified",
    type_expiry: datetime | None = None,
    location_expiry: datetime | None = None,
    shared_evidence: bool = False,
    row_eligible: bool = True,
    observation_expiry: datetime | None = None,
    provider: str = "osm_nominatim",
) -> tuple[SimpleNamespace, list[SimpleNamespace], list[SimpleNamespace]]:
    candidate_id = uuid4()
    type_observation = _observation(
        now,
        place_type,
        expires_at=observation_expiry,
        provider=provider,
        object_id=f"node/{candidate_id.int % 1_000_000 + 1000}",
    )
    location_observation = (
        type_observation
        if shared_evidence
        else _observation(
            now,
            place_type,
            expires_at=observation_expiry,
            provider=provider,
            object_id=f"node/{candidate_id.int % 1_000_000 + 1000}",
        )
    )
    type_claim = _claim(
        "place_type",
        _TextValue(value=place_type),
        type_observation,
        expires_at=type_expiry,
    )
    location_claim = _claim(
        "location",
        _LocationValue(latitude=coordinates[0], longitude=coordinates[1]),
        location_observation,
        expires_at=location_expiry,
    )
    row = SimpleNamespace(
        candidate_id=candidate_id,
        name=name,
        eligible=row_eligible,
        selected=row_eligible,
        rank=1 if row_eligible else None,
        score=0.9 if row_eligible else None,
        exclusion_reasons=() if row_eligible else ("upstream_uncertain",),
        cells=(
            _cell("place_type", type_claim, type_observation, type_status),
            _cell("location", location_claim, location_observation, location_status),
        ),
        features=(),
    )
    observations = [type_observation]
    if location_observation is not type_observation:
        observations.append(location_observation)
    return row, [type_claim, location_claim], observations


def _comparison(
    *rows: tuple[SimpleNamespace, list[SimpleNamespace], list[SimpleNamespace]],
) -> SimpleNamespace:
    row_values = tuple(entry[0] for entry in rows)
    claims = tuple(claim for entry in rows for claim in entry[1])
    observations = tuple(obs for entry in rows for obs in entry[2])
    return SimpleNamespace(
        comparison=SimpleNamespace(
            id=uuid4(),
            rendered_at=datetime.now(UTC) - timedelta(minutes=5),
            state="recommended",
            rows=row_values,
        ),
        domain_claims=claims,
        provider_observations=observations,
    )


def _response(upstream: SimpleNamespace, category: str = "food"):
    return _comparison_response(
        upstream,
        owner_id=OWNER,
        category=category,
        trip_revision=4,
        reference_place_id=uuid4(),
        reference_place_revision=2,
        reference_place_name="Tokyo base",
        center=CENTER,
        radius_km=5,
        max_results=10,
    )


def test_mixed_valid_missing_conflicting_and_stale_rows_keep_valid_candidates() -> None:
    now = datetime.now(UTC)
    good = _row(now, name="Current Cafe")
    missing = _row(now, name="Missing Place", type_status="missing", location_status="missing")
    stale = _row(
        now,
        name="Stale Place",
        type_status="stale",
        type_expiry=now - timedelta(minutes=1),
        observation_expiry=now + timedelta(hours=1),
    )
    conflicting = _row(now, name="Conflicting Place", type_status="conflicting")
    result = _response(_comparison(good, missing, stale, conflicting))

    assert result.state == "recommended"
    assert {candidate.name for candidate in result.candidates} == {
        "Current Cafe",
        "Missing Place",
        "Stale Place",
        "Conflicting Place",
    }
    current = next(candidate for candidate in result.candidates if candidate.name == "Current Cafe")
    uncertain = next(
        candidate for candidate in result.candidates if candidate.name == "Missing Place"
    )
    old = next(candidate for candidate in result.candidates if candidate.name == "Stale Place")
    disputed = next(
        candidate for candidate in result.candidates if candidate.name == "Conflicting Place"
    )
    assert current.eligible
    assert not uncertain.eligible and [item.outcome for item in uncertain.constraints] == [
        "unknown",
        "unknown",
    ]
    assert not old.eligible and old.constraints[0].outcome == "unknown"
    assert not disputed.eligible and disputed.constraints[0].outcome == "unknown"


def test_all_missing_and_expired_claims_return_explicit_safe_states() -> None:
    now = datetime.now(UTC)
    missing = _row(now, type_status="missing", location_status="missing")
    insufficient = _response(_comparison(missing))
    assert insufficient.state == "insufficient"
    assert len(insufficient.candidates) == 1
    assert not insufficient.candidates[0].eligible

    expired = _row(
        now,
        type_expiry=now - timedelta(minutes=1),
        location_expiry=now - timedelta(minutes=1),
        observation_expiry=now + timedelta(hours=1),
    )
    stale_result = _response(_comparison(expired))
    assert stale_result.state == "expired"
    assert stale_result.expires_at is not None and stale_result.expires_at < now
    assert not stale_result.candidates[0].eligible


def test_shared_evidence_merges_different_claim_expiries_to_the_earliest() -> None:
    now = datetime.now(UTC)
    row = _row(
        now,
        shared_evidence=True,
        type_expiry=now + timedelta(minutes=40),
        location_expiry=now + timedelta(minutes=10),
    )
    result = _response(_comparison(row))
    candidate = result.candidates[0]
    assert candidate.eligible
    assert len(candidate.sources) == 1
    assert candidate.sources[0].expires_at == now + timedelta(minutes=10)


def test_category_and_radius_are_rechecked_even_when_upstream_marks_row_eligible() -> None:
    now = datetime.now(UTC)
    wrong_category = _row(now, name="Hotel", place_type="amenity/hotel")
    outside_radius = _row(now, name="Far Cafe", coordinates=(36.0, 139.0))
    result = _response(_comparison(wrong_category, outside_radius))
    candidates = {candidate.name: candidate for candidate in result.candidates}
    assert not candidates["Hotel"].eligible
    assert candidates["Hotel"].constraints[0].outcome == "fail"
    assert not candidates["Far Cafe"].eligible
    assert candidates["Far Cafe"].constraints[1].outcome == "fail"
    assert _distance_km(CENTER, CENTER) == 0


@pytest.mark.parametrize("category", ["food", "activity", "neighborhood", "day_trip"])
def test_each_accepted_category_uses_its_typed_place_type_fixture(category: str) -> None:
    now = datetime.now(UTC)
    supported_type = TRAVEL_COMPARISON_CATEGORIES[category].allowed_place_types[0]
    row = _row(now, place_type=supported_type)
    result = _response(_comparison(row), category)
    assert result.candidates[0].eligible
    assert result.candidates[0].place_type == supported_type


def test_synthetic_source_is_displayable_but_cannot_be_persisted() -> None:
    now = datetime.now(UTC)
    row, claims, observations = _row(now, provider="fake")
    upstream = _comparison((row, claims, observations))
    validated = _validated_candidate(
        upstream,
        owner_id=OWNER,
        category="food",
        center=CENTER,
        radius_km=5,
        row_id=row.candidate_id,
        now=now,
    )
    assert validated.response.eligible
    assert validated.place_source is None
    assert validated.provider_place_id is None


def test_foreign_source_owners_and_unrelated_citations_are_rejected() -> None:
    now = datetime.now(UTC)
    row, claims, observations = _row(now)
    observations[0].owner_id = "another-owner"
    with pytest.raises(ValueError, match="citation do not correlate"):
        _response(_comparison((row, claims, observations)))


def test_nonverified_rows_still_reject_foreign_domain_and_duplicate_evidence() -> None:
    now = datetime.now(UTC)
    row, claims, observations = _row(now, type_status="conflicting")
    claims[0].domain_id = "another-domain"
    with pytest.raises(ValueError, match="missing or mismatched claim"):
        _response(_comparison((row, claims, observations)))

    row, claims, observations = _row(now)
    with pytest.raises(ValueError, match="duplicate evidence IDs"):
        _response(_comparison((row, claims, [observations[0], observations[0], observations[1]])))

    row, claims, observations = _row(now)
    row.cells[0].sources[0].url = "https://example.test/forged"
    with pytest.raises(ValueError, match="citation do not correlate"):
        _response(_comparison((row, claims, observations)))


def test_reservation_only_places_are_trip_members_and_cancelled_ones_are_not() -> None:
    reservation_place = SimpleNamespace(id=uuid4(), owner_id=OWNER, latitude=35.0, longitude=139.0)
    trip = SimpleNamespace(
        days=[],
        saved_places=[],
        reservations=[SimpleNamespace(status="confirmed", place=reservation_place)],
    )
    assert _find_trip_place(trip, reservation_place.id, OWNER) is reservation_place
    trip.reservations[0].status = "cancelled"
    with pytest.raises(DomainError):
        _find_trip_place(trip, reservation_place.id, OWNER)


def test_request_constraint_ids_are_bound_to_the_idempotency_key() -> None:
    key = uuid4()
    service = object.__new__(TravelComparisonService)
    category = TRAVEL_COMPARISON_CATEGORIES["food"]
    snapshot = SimpleNamespace(latitude=CENTER[0], longitude=CENTER[1])

    def upstream(category_id: UUID, distance_id: UUID) -> SimpleNamespace:
        return SimpleNamespace(
            comparison=SimpleNamespace(
                constraints=(
                    SimpleNamespace(
                        id=category_id,
                        attribute="place_type",
                        operator="set",
                        allowed_values=tuple(
                            _TextValue(value=value) for value in category.allowed_place_types
                        ),
                        required=True,
                        missing_policy="fail_closed",
                        source="user",
                    ),
                    SimpleNamespace(
                        id=distance_id,
                        attribute="location",
                        operator="geospatial",
                        value=_LocationValue(latitude=CENTER[0], longitude=CENTER[1]),
                        radius_km=5,
                        required=True,
                        missing_policy="fail_closed",
                        source="user",
                    ),
                ),
                preferences=(),
            )
        )

    request = SimpleNamespace(category="food", radius_km=5, idempotency_key=key)
    category_id, distance_id = uuid5(key, "category"), uuid5(key, "distance")
    service._validate_request_footprint(upstream(category_id, distance_id), request, snapshot)
    previous_key = uuid4()
    for wrong_ids in (
        (uuid4(), distance_id),
        (distance_id, category_id),
        (category_id, category_id),
        (uuid5(previous_key, "category"), uuid5(previous_key, "distance")),
    ):
        with pytest.raises(ValueError):
            service._validate_request_footprint(upstream(*wrong_ids), request, snapshot)


def test_stale_reference_snapshot_is_included_in_the_typed_request_contract() -> None:
    request = TravelComparisonRequest(
        category="food",
        query="sushi",
        reference_place_id=uuid4(),
        reference_place_revision=5,
        reference_latitude=35.0,
        reference_longitude=139.0,
        trip_revision=8,
        idempotency_key=uuid4(),
    )
    assert request.reference_place_revision == 5
    assert request.trip_revision == 8
