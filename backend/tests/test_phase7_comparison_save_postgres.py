from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from personal_travel.api.schemas import (
    ManualSavedPlaceCreate,
    PlaceUpdate,
    TravelComparisonCandidateSaveRequest,
)
from personal_travel.config import Settings
from personal_travel.domain.upstream_comparisons import (
    TRAVEL_COMPARISON_CATEGORIES,
    VerifiedPlaceSource,
    _LocationValue,
    _TextValue,
)
from personal_travel.models.place import Place
from personal_travel.models.reservation import SavedPlace
from personal_travel.models.trip import Trip
from personal_travel.services.errors import DomainError
from personal_travel.services.places import PlaceService
from personal_travel.services.saved_places import SavedPlaceService
from personal_travel.services.travel_comparisons import TravelComparisonService

pytestmark = pytest.mark.usefixtures("clean_database")


class BlockingComparisonClient:
    def __init__(self, comparison: SimpleNamespace) -> None:
        self.comparison = comparison
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def get_travel_comparison(self, _comparison_id: UUID) -> SimpleNamespace:
        self.started.set()
        await self.release.wait()
        return self.comparison


def _comparison_fixture(
    owner_id: str, center: tuple[float, float], *, expires_at: datetime
) -> tuple[SimpleNamespace, UUID, UUID]:
    now = datetime.now(UTC)
    comparison_id, candidate_id = uuid4(), uuid4()
    evidence_id, observation_id = uuid4(), uuid4()
    source_url = "https://www.openstreetmap.org/node/6000123"
    policy_url = "https://operations.osmfoundation.org/policies/nominatim/"
    attribution = "© OpenStreetMap contributors, ODbL 1.0"
    observation = SimpleNamespace(
        evidence_id=evidence_id,
        source_observation_id=observation_id,
        owner_id=owner_id,
        provider="osm_nominatim",
        provider_object_id="node/6000123",
        entity_kind="amenity/cafe",
        adapter_version="fixture-v1",
        attribution=attribution,
        policy_url=policy_url,
        url=source_url,
        title="Tokyo cafe",
        observed_at=now - timedelta(minutes=1),
        expires_at=expires_at,
    )
    citation = SimpleNamespace(
        evidence_id=evidence_id,
        source_observation_id=observation_id,
        url=source_url,
        title="Tokyo cafe",
        attribution=attribution,
        policy_url=policy_url,
        observed_at=observation.observed_at,
        expires_at=expires_at,
    )
    type_claim = SimpleNamespace(
        claim_id=uuid4(),
        domain_id="travel",
        attribute="place_type",
        typed_value=_TextValue(value="amenity/cafe"),
        evidence_ids=(evidence_id,),
        observed_at=observation.observed_at,
        expires_at=expires_at,
    )
    location_claim = SimpleNamespace(
        claim_id=uuid4(),
        domain_id="travel",
        attribute="location",
        typed_value=_LocationValue(latitude=center[0] + 0.001, longitude=center[1] + 0.001),
        evidence_ids=(evidence_id,),
        observed_at=observation.observed_at,
        expires_at=expires_at,
    )
    type_cell = SimpleNamespace(
        field="place_type", status="verified", claim_ids=(type_claim.claim_id,), sources=(citation,)
    )
    location_cell = SimpleNamespace(
        field="location",
        status="verified",
        claim_ids=(location_claim.claim_id,),
        sources=(citation,),
    )
    row = SimpleNamespace(
        candidate_id=candidate_id,
        name="Tokyo cafe",
        eligible=True,
        rank=1,
        score=0.9,
        exclusion_reasons=(),
        cells=(type_cell, location_cell),
    )
    category = TRAVEL_COMPARISON_CATEGORIES["food"]
    comparison = SimpleNamespace(
        id=comparison_id,
        owner_id=owner_id,
        domain_id="travel",
        field_schema_version="travel-comparison-v1",
        feature_policy_version="travel-features-v2",
        decision_policy_versions=SimpleNamespace(domain_features="travel-features-v2"),
        rows=(row,),
        constraints=(
            SimpleNamespace(
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
                attribute="location",
                operator="geospatial",
                value=_LocationValue(latitude=center[0], longitude=center[1]),
                radius_km=5,
                required=True,
                missing_policy="fail_closed",
                source="user",
            ),
        ),
        preferences=(),
    )
    registration = SimpleNamespace(
        enabled=True,
        domain_id="travel",
        source_policy_version="travel-sources-v1",
        field_schema_version="travel-comparison-v1",
        feature_policy_version="travel-features-v2",
        supported_constraints=("place_type", "location"),
        supported_entity_types=("place",),
        fields=(
            SimpleNamespace(key="place_type", value_kinds=("text",)),
            SimpleNamespace(key="location", value_kinds=("location",)),
        ),
        source_adapters=("osm_nominatim", "fake"),
    )
    upstream = SimpleNamespace(
        schema_version="domain-comparison-v1",
        registration=registration,
        comparison=comparison,
        provider_observations=(observation,),
        domain_claims=(type_claim, location_claim),
    )
    return upstream, comparison_id, candidate_id


def _create_trip_and_reference(api_client: TestClient) -> tuple[UUID, UUID, int, int]:
    trip_response = api_client.post(
        "/v1/trips",
        json={"title": "Comparison save", "start_date": "2026-12-01", "end_date": "2026-12-02"},
    )
    assert trip_response.status_code == 201, trip_response.text
    trip_id = UUID(trip_response.json()["id"])
    place_response = api_client.post(
        "/v1/places",
        json={"name": "Tokyo base", "latitude": 35.0, "longitude": 139.0},
    )
    assert place_response.status_code == 201, place_response.text
    place_id = UUID(place_response.json()["id"])
    attached = api_client.post(
        f"/v1/trips/{trip_id}/saved-places", json={"place_id": str(place_id)}
    )
    assert attached.status_code == 201, attached.text
    current_trip = api_client.get(f"/v1/trips/{trip_id}")
    assert current_trip.status_code == 200, current_trip.text
    return trip_id, place_id, current_trip.json()["revision"], place_response.json()["revision"]


def test_unchanged_context_saves_with_attribution_and_replay_returns_existing_after_expiry(
    api_client: TestClient, database_engine: Engine
) -> None:
    trip_id, reference_id, trip_revision, place_revision = _create_trip_and_reference(api_client)
    owner_id = api_client.app.state.auth_settings_provider().owner_id
    upstream, comparison_id, candidate_id = _comparison_fixture(
        owner_id, (35.0, 139.0), expires_at=datetime.now(UTC) + timedelta(minutes=5)
    )
    client = BlockingComparisonClient(upstream)
    client.release.set()
    with Session(database_engine, autoflush=False, expire_on_commit=False) as session:
        service = TravelComparisonService(
            session,
            owner_id,
            Settings(owner_id=owner_id, personal_ai_comparisons_enabled=True),
            client=client,  # type: ignore[arg-type]
        )
        saved = asyncio.run(
            service.save_candidate(
                trip_id,
                comparison_id,
                candidate_id,
                _save_request(reference_id, trip_revision, place_revision),
                expected_revision=trip_revision,
            )
        )
        saved_id = saved.id
        saved_place_id = saved.place_id
        assert saved.place.provider_source_name == "OpenStreetMap"
        assert saved.place.provider_source_attribution == "© OpenStreetMap contributors, ODbL 1.0"
        assert saved.place.provider_source_license == "ODbL 1.0"
        assert saved.place.provider_source_url == "https://www.openstreetmap.org/node/6000123"

    source = VerifiedPlaceSource(
        provider="osm_nominatim",
        provider_place_id="node/6000123",
        provider_source_attribution="© OpenStreetMap contributors, ODbL 1.0",
        provider_source_url="https://www.openstreetmap.org/node/6000123",
        policy_url="https://operations.osmfoundation.org/policies/nominatim/",
    )
    replay_data = ManualSavedPlaceCreate(
        name="Tokyo cafe",
        address="Tokyo",
        category="cafe",
        latitude=35.001,
        longitude=139.001,
        note=None,
    )
    with Session(database_engine, autoflush=False, expire_on_commit=False) as session:
        replayed = SavedPlaceService(session, owner_id).create_from_comparison(
            trip_id,
            replay_data,
            source,
            "node/6000123",
            expected_revision=trip_revision,
            reference_place_id=reference_id,
            reference_place_revision=place_revision + 1,
            reference_latitude=35.0,
            reference_longitude=139.0,
            evidence_expires_at=datetime.now(UTC) - timedelta(minutes=1),
        )
        assert replayed.id == saved_id
        assert replayed.place_id == saved_place_id

    with Session(database_engine) as session:
        trip = session.get(Trip, trip_id)
        assert trip is not None and trip.revision == trip_revision + 1


def _save_request(
    place_id: UUID, revision: int, place_revision: int
) -> TravelComparisonCandidateSaveRequest:
    return TravelComparisonCandidateSaveRequest(
        name="Tokyo cafe",
        address="Tokyo",
        category="cafe",
        trip_revision=revision,
        reference_place_id=place_id,
        reference_place_revision=place_revision,
    )


def test_center_edit_during_comparison_detail_rejects_save_without_mutation(
    api_client: TestClient, database_engine: Engine
) -> None:
    trip_id, reference_id, trip_revision, place_revision = _create_trip_and_reference(api_client)
    owner_id = api_client.app.state.auth_settings_provider().owner_id
    upstream, comparison_id, candidate_id = _comparison_fixture(
        owner_id, (35.0, 139.0), expires_at=datetime.now(UTC) + timedelta(minutes=5)
    )
    client = BlockingComparisonClient(upstream)
    with Session(database_engine, autoflush=False, expire_on_commit=False) as session:
        service = TravelComparisonService(
            session,
            owner_id,
            Settings(owner_id=owner_id, personal_ai_comparisons_enabled=True),
            client=client,  # type: ignore[arg-type]
        )

        async def save_after_edit() -> None:
            save_task = asyncio.create_task(
                service.save_candidate(
                    trip_id,
                    comparison_id,
                    candidate_id,
                    _save_request(reference_id, trip_revision, place_revision),
                    expected_revision=trip_revision,
                )
            )
            await client.started.wait()
            with Session(database_engine, autoflush=False, expire_on_commit=False) as editor:
                PlaceService(editor, owner_id).update(
                    reference_id,
                    PlaceUpdate(latitude=35.1, longitude=139.1),
                    expected_revision=place_revision,
                )
            client.release.set()
            with pytest.raises(DomainError) as error:
                await save_task
            assert error.value.code == "comparison_context_stale"
            assert error.value.status_code == 409

        asyncio.run(save_after_edit())

    with Session(database_engine) as session:
        candidate = session.scalar(
            select(Place).where(
                Place.owner_id == owner_id, Place.provider_place_id == "node/6000123"
            )
        )
        assert candidate is None
        assert (
            session.scalar(
                select(func.count()).select_from(SavedPlace).where(SavedPlace.trip_id == trip_id)
            )
            == 1
        )
        trip = session.get(Trip, trip_id)
        assert trip is not None and trip.revision == trip_revision


def test_evidence_expiring_while_waiting_for_trip_lock_rejects_save(
    api_client: TestClient, database_engine: Engine
) -> None:
    trip_id, reference_id, trip_revision, place_revision = _create_trip_and_reference(api_client)
    owner_id = api_client.app.state.auth_settings_provider().owner_id
    expires_at = datetime.now(UTC) + timedelta(seconds=1.2)
    upstream, comparison_id, candidate_id = _comparison_fixture(
        owner_id, (35.0, 139.0), expires_at=expires_at
    )
    client = BlockingComparisonClient(upstream)
    with Session(database_engine, autoflush=False, expire_on_commit=False) as session:
        service = TravelComparisonService(
            session,
            owner_id,
            Settings(owner_id=owner_id, personal_ai_comparisons_enabled=True),
            client=client,  # type: ignore[arg-type]
        )

        async def hold_trip_lock_until_expired() -> None:
            save_task = asyncio.create_task(
                service.save_candidate(
                    trip_id,
                    comparison_id,
                    candidate_id,
                    _save_request(reference_id, trip_revision, place_revision),
                    expected_revision=trip_revision,
                )
            )
            await client.started.wait()
            with Session(database_engine, autoflush=False, expire_on_commit=False) as blocker:
                transaction = blocker.begin()
                blocker.scalar(select(Trip).where(Trip.id == trip_id).with_for_update())
                client.release.set()
                done, _pending = await asyncio.wait({save_task}, timeout=0.1)
                assert not done, "save must wait for the trip root lock"
                await asyncio.sleep(1.3)
                transaction.commit()
            with pytest.raises(DomainError) as error:
                await save_task
            assert error.value.code == "comparison_evidence_expired"
            assert error.value.status_code == 409

        asyncio.run(hold_trip_lock_until_expired())

    with Session(database_engine) as session:
        assert (
            session.scalar(
                select(Place).where(
                    Place.owner_id == owner_id, Place.provider_place_id == "node/6000123"
                )
            )
            is None
        )
        assert (
            session.scalar(
                select(func.count()).select_from(SavedPlace).where(SavedPlace.trip_id == trip_id)
            )
            == 1
        )
