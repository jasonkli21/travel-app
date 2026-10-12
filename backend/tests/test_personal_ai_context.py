from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from personal_travel.api import middleware as request_middleware
from personal_travel.api.dependencies import session_dependency
from personal_travel.api.routes import personal_ai_context as context_routes
from personal_travel.api.schemas.personal_ai_context import (
    ContextEntityReference,
    ContextReadRequest,
    ContextReadResponse,
    PersonalAISidecarActionPolicy,
    SidecarTurnRequest,
)
from personal_travel.auth.contracts import VerifiedPrincipal
from personal_travel.config import Settings, get_settings
from personal_travel.main import app
from personal_travel.models.itinerary import ItineraryItem
from personal_travel.models.place import Place
from personal_travel.models.reservation import Reservation, SavedPlace
from personal_travel.models.sidecar_anchor import PersonalAISidecarAnchor
from personal_travel.models.trip import Trip, TripDay
from personal_travel.services.errors import DomainError


def _trip(*, days: list[TripDay] | None = None) -> Trip:
    now = datetime.now(UTC)
    return Trip(
        id=uuid4(),
        owner_id="context-owner",
        title="A private trip",
        start_date=date(2026, 10, 11),
        end_date=date(2026, 10, 12),
        timezone="America/Los_Angeles",
        destination="Tokyo, Japan",
        cross_domain_sharing_allowed=False,
        revision=4,
        days=days or [],
        reservations=[],
        saved_places=[],
        created_at=now,
        updated_at=now,
    )


def _day(*, items: list[ItineraryItem] | None = None) -> TripDay:
    now = datetime.now(UTC)
    return TripDay(
        id=uuid4(),
        day_index=1,
        date=date(2026, 10, 11),
        title="Arrival",
        items=items or [],
        created_at=now,
        updated_at=now,
    )


def _context_request(
    *,
    operation: str = "entity",
    refs: tuple[ContextEntityReference, ...],
    fields: tuple[str, ...],
    max_bytes: int = 16_384,
) -> ContextReadRequest:
    return ContextReadRequest(
        operation=operation,
        entity_refs=refs,
        fields=fields,
        max_results=16,
        max_bytes=max_bytes,
    )


def test_selected_empty_day_is_projected_once():
    day = _day()
    trip = _trip(days=[day])
    request = _context_request(
        refs=(
            ContextEntityReference(
                entity_type="trip",
                entity_id=str(trip.id),
                application_id="travel",
            ),
            ContextEntityReference(
                entity_type="day",
                entity_id=str(day.id),
                application_id="travel",
            ),
        ),
        fields=("day_date", "day_title"),
    )

    records = context_routes._context_records(trip, request)

    assert len(records) == 1
    assert records[0].entity_type == "day"
    assert records[0].payload == {"day_date": "2026-10-11", "day_title": "Arrival"}


def test_travel_shopping_export_is_narrow_consent_bound_and_revisioned():
    trip = _trip()
    trip.cross_domain_sharing_allowed = True
    request = ContextReadRequest(
        operation="federation",
        purpose="shopping_trip_context",
        destination_application_id="shopping",
        entity_refs=(ContextEntityReference(
            entity_type="trip", entity_id=str(trip.id), application_id="travel"
        ),),
        fields=("destination", "start_date", "end_date"),
        max_results=1,
        max_bytes=4096,
    )

    record = context_routes._context_records(trip, request)[0]

    assert record.payload == {
        "destination": "Tokyo, Japan",
        "start_date": "2026-10-11",
        "end_date": "2026-10-12",
    }
    assert record.source_version == "4"
    assert record.permission_dependencies[0].permission_id == f"travel_context_read:{trip.id}"
    assert record.permission_dependencies[0].version == "4"
    assert record.permission_dependencies[0].purpose == "shopping_trip_context"
    assert record.permission_dependencies[0].source_application_id == "travel"
    assert record.permission_dependencies[0].destination_application_id == "shopping"

    trip.destination = None
    trip.revision += 1
    empty_destination = context_routes._context_records(trip, request)[0]
    assert empty_destination.payload["destination"] is None
    assert empty_destination.source_version == "5"

    trip.cross_domain_sharing_allowed = False
    verify = request.model_copy(
        update={"operation": "verify", "fields": ("trip_revision",)}
    )
    assert context_routes._context_records(trip, verify) == ()
    with pytest.raises(HTTPException) as error:
        context_routes._context_records(trip, request)
    assert error.value.status_code == 404


def test_travel_federation_contract_rejects_broad_or_wrong_destination_reads():
    ref = ContextEntityReference(
        entity_type="trip", entity_id=str(uuid4()), application_id="travel"
    )
    with pytest.raises(ValueError, match="travel_federation_export_scope_invalid"):
        ContextReadRequest(
            operation="federation",
            purpose="shopping_trip_context",
            destination_application_id="shopping",
            entity_refs=(ref,),
            fields=("destination", "timezone"),
            max_results=1,
            max_bytes=4096,
        )
    with pytest.raises(ValueError):
        ContextReadRequest(
            operation="federation",
            purpose="shopping_trip_context",
            destination_application_id="shopping",
            entity_refs=(ref.model_copy(update={"application_id": "health"}),),
            fields=("destination", "start_date", "end_date"),
            max_results=1,
            max_bytes=4096,
        )


def test_selected_itinerary_context_uses_minimal_booking_summary_and_omits_private_fields():
    now = datetime.now(UTC)
    trip = _trip()
    reservation = Reservation(
        id=uuid4(),
        owner_id=trip.owner_id,
        trip_id=trip.id,
        reservation_type="flight",
        status="confirmed",
        provider_name="Sample Air",
        confirmation_code="PRIVATE-CONFIRMATION-123",
        starts_at=datetime(2026, 10, 11, 9, tzinfo=UTC),
        ends_at=datetime(2026, 10, 11, 11, tzinfo=UTC),
        source_reference="private-mailbox-reference",
        notes="Private booking notes",
        created_at=now,
        updated_at=now,
    )
    item = ItineraryItem(
        id=uuid4(),
        trip_day_id=uuid4(),
        reservation_id=reservation.id,
        item_type="flight",
        title="Morning flight",
        status="booked",
        notes="Private itinerary notes",
        starts_at=reservation.starts_at,
        ends_at=reservation.ends_at,
        sort_order=0,
        created_at=now,
        updated_at=now,
        reservation=reservation,
    )
    day = _day(items=[item])
    item.trip_day = day
    item.trip_day_id = day.id
    trip.days = [day]
    trip.reservations = [reservation]
    request = _context_request(
        refs=(
            ContextEntityReference(
                entity_type="trip", entity_id=str(trip.id), application_id="travel"
            ),
            ContextEntityReference(
                entity_type="itinerary_item",
                entity_id=str(item.id),
                application_id="travel",
            ),
        ),
        fields=("item_title", "item_status", "item_schedule", "reservation_summary"),
    )

    records = context_routes._context_records(trip, request)
    serialized = "\n".join(item.model_dump_json() for item in records)
    reservation_record = next(item for item in records if item.entity_type == "reservation")

    assert reservation_record.sensitivity == "sensitive"
    assert reservation_record.selection_binding is not None
    assert reservation_record.selection_binding.entity_type == "itinerary_item"
    assert reservation_record.selection_binding.entity_id == str(item.id)
    assert reservation_record.selection_binding.relationship == "itinerary_item_reservation_summary"
    assert "Sample Air" in reservation_record.payload["reservation_summary"]
    assert "confirmed" in reservation_record.payload["reservation_summary"]
    assert "PRIVATE-CONFIRMATION-123" not in serialized
    assert "private-mailbox-reference" not in serialized
    assert "Private booking notes" not in serialized
    assert "Private itinerary notes" not in serialized


@pytest.mark.parametrize(
    ("anchor_type", "expected_relationship"),
    (
        ("trip", "trip_reservation_summary"),
        ("day", "day_reservation_summary"),
        ("itinerary_item", "itinerary_item_reservation_summary"),
        ("place", "place_reservation_summary"),
        ("reservation", None),
    ),
)
def test_reservation_expansion_is_bound_to_each_selected_anchor(
    anchor_type: str, expected_relationship: str | None
):
    now = datetime.now(UTC)
    trip = _trip()
    reservation = Reservation(
        id=uuid4(),
        owner_id=trip.owner_id,
        trip_id=trip.id,
        reservation_type="lodging",
        status="confirmed",
        provider_name="Example Stay",
        created_at=now,
        updated_at=now,
    )
    anchor_id: UUID | None = None
    if anchor_type in {"day", "itinerary_item"}:
        item = ItineraryItem(
            id=uuid4(),
            trip_day_id=uuid4(),
            reservation_id=reservation.id,
            item_type="lodging",
            title="Hotel",
            status="booked",
            notes=None,
            starts_at=None,
            ends_at=None,
            sort_order=0,
            created_at=now,
            updated_at=now,
            reservation=reservation,
        )
        day = _day(items=[item])
        item.trip_day = day
        item.trip_day_id = day.id
        trip.days = [day]
        anchor_id = day.id if anchor_type == "day" else item.id
    elif anchor_type == "place":
        place = Place(
            id=uuid4(),
            owner_id=trip.owner_id,
            name="Example Hotel",
            created_at=now,
            updated_at=now,
        )
        reservation.place = place
        reservation.place_id = place.id
        anchor_id = place.id
    trip.reservations = [reservation]

    references = [
        ContextEntityReference(entity_type="trip", entity_id=str(trip.id), application_id="travel")
    ]
    if anchor_type != "trip":
        references.append(
            ContextEntityReference(
                entity_type=anchor_type,
                entity_id=str(reservation.id if anchor_type == "reservation" else anchor_id),
                application_id="travel",
            )
        )
    request = _context_request(
        refs=tuple(references),
        fields=("reservation_summary",),
    )

    records = context_routes._context_records(trip, request)
    summary = next(record for record in records if record.entity_type == "reservation")

    assert summary.sensitivity == "sensitive"
    if expected_relationship is None:
        assert summary.selection_binding is None
    else:
        assert summary.selection_binding is not None
        assert summary.selection_binding.entity_type == anchor_type
        expected_anchor_id = trip.id if anchor_type == "trip" else anchor_id
        assert summary.selection_binding.entity_id == str(expected_anchor_id)
        assert summary.selection_binding.relationship == expected_relationship


def test_travel_writer_emits_shared_v2_reservation_binding_fixture():
    fixture = json.loads(
        (Path(__file__).parent / "fixtures/travel-context-read-v2-reservation.json").read_text()
    )
    response = ContextReadResponse.model_validate(fixture)
    record = response.items[0]

    assert response.schema_version == "travel-context-read-v2"
    assert record.entity_type == "reservation"
    assert record.payload == {"reservation_summary": "lodging · Example Stay · confirmed"}
    assert record.sensitivity == "sensitive"
    assert record.selection_binding is not None
    assert record.selection_binding.entity_type == "day"
    assert record.selection_binding.relationship == "day_reservation_summary"
    assert "destination" not in context_routes.TRIP_FIELDS


@pytest.fixture
def local_context_client(monkeypatch: pytest.MonkeyPatch):
    settings = Settings(
        _env_file=None,
        owner_id="context-owner",
        personal_ai_context_enabled=True,
    )
    monkeypatch.setattr(app.state, "auth_settings_provider", lambda: settings)
    previous = app.dependency_overrides.copy()
    app.dependency_overrides.update(
        {
            session_dependency: lambda: object(),
            get_settings: lambda: settings,
        }
    )
    try:
        with TestClient(app, base_url="http://localhost") as client:
            yield client, settings
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


def test_context_route_reuses_owner_scoped_trip_service_and_enforces_response_byte_limit(
    local_context_client,
    monkeypatch: pytest.MonkeyPatch,
):
    client, settings = local_context_client
    trip = _trip()
    trip.owner_id = settings.owner_id
    seen_owners: list[str] = []

    class FakeTripService:
        def __init__(self, _session, owner_id: str):
            seen_owners.append(owner_id)

        def get(self, trip_id):
            assert trip_id == trip.id
            assert seen_owners[-1] == trip.owner_id
            return trip

    monkeypatch.setattr(context_routes, "TripService", FakeTripService)
    body = {
        "operation": "current",
        "entity_refs": [
            {
                "entity_type": "trip",
                "entity_id": str(trip.id),
                "application_id": "travel",
                "workspace_id": None,
            }
        ],
        "fields": ["trip_title"],
        "max_results": 1,
        "max_bytes": 8192,
    }

    response = client.post("/v1/integrations/personal-ai/context", json=body)
    too_small = client.post(
        "/v1/integrations/personal-ai/context",
        json={**body, "max_bytes": 1},
    )

    assert response.status_code == 200
    assert response.json()["workspace_id"] is None
    assert response.json()["items"][0]["payload"] == {"trip_title": "A private trip"}
    assert too_small.status_code == 413
    assert seen_owners == [settings.owner_id, settings.owner_id]


def test_sidecar_anchor_rejects_using_trip_a_conversation_through_trip_b():
    trip_a, trip_b, conversation_id = uuid4(), uuid4(), uuid4()
    anchor = PersonalAISidecarAnchor(
        conversation_id=conversation_id,
        owner_id="context-owner",
        trip_id=trip_a,
    )

    class FakeSession:
        def get(self, model, identifier):
            assert model is PersonalAISidecarAnchor
            return anchor if identifier == conversation_id else None

    context_routes._require_sidecar_anchor(
        FakeSession(),
        owner_id="context-owner",
        trip_id=trip_a,
        conversation_id=conversation_id,
    )
    with pytest.raises(DomainError) as error:
        context_routes._require_sidecar_anchor(
            FakeSession(),
            owner_id="context-owner",
            trip_id=trip_b,
            conversation_id=conversation_id,
        )
    assert error.value.status_code == 404


def test_context_v2_rejects_the_removed_travel_research_proxy_operation(
    local_context_client,
):
    client, _settings = local_context_client
    trip = _trip()
    response = client.post(
        "/v1/integrations/personal-ai/context",
        json={
            "operation": "search",
            "entity_refs": [
                {
                    "entity_type": "trip",
                    "entity_id": str(trip.id),
                    "application_id": "travel",
                    "workspace_id": None,
                },
            ],
            "fields": ["trip_title"],
            "max_results": 1,
            "max_bytes": 16_384,
        },
    )

    assert response.status_code == 422


def test_saved_place_with_provider_attribution_stays_travel_authoritative():
    trip = _trip()
    place = Place(
        id=uuid4(),
        owner_id=trip.owner_id,
        name="Saved cafe",
        address="Example address",
        category="cafe",
        provider_source_name="OpenStreetMap",
        provider_source_attribution="© OpenStreetMap contributors",
        provider_source_url="https://www.openstreetmap.org/way/123",
        revision=2,
        created_at=trip.created_at,
        updated_at=trip.updated_at,
    )
    trip.saved_places = [
        SavedPlace(
            owner_id=trip.owner_id,
            trip_id=trip.id,
            place_id=place.id,
            place=place,
        )
    ]
    request = _context_request(
        refs=(
            ContextEntityReference(
                entity_type="trip", entity_id=str(trip.id), application_id="travel"
            ),
            ContextEntityReference(
                entity_type="place", entity_id=str(place.id), application_id="travel"
            ),
        ),
        fields=("place_name", "place_source", "place_attribution"),
    )

    (record,) = context_routes._context_records(trip, request)

    assert record.authority == "user_asserted"
    assert record.observed_at == trip.updated_at.astimezone(UTC).isoformat()
    assert record.expires_at is None
    assert record.source_refs[-1].uri == place.provider_source_url


def test_sidecar_action_policy_is_passed_through_from_personal_ai(
    local_context_client,
    monkeypatch: pytest.MonkeyPatch,
):
    client, _settings = local_context_client
    trip = _trip()

    class FakeTripService:
        def __init__(self, _session, _owner_id):
            pass

        def get(self, trip_id):
            assert trip_id == trip.id
            return trip

    class FakeContextClient:
        async def get_sidecar_action_policy(self):
            return PersonalAISidecarActionPolicy(
                copy_allowed=False,
                draft_insert_target_ids=(),
            )

    monkeypatch.setattr(context_routes, "TripService", FakeTripService)
    monkeypatch.setattr(
        context_routes,
        "_context_client",
        lambda *_args, **_kwargs: FakeContextClient(),
    )
    response = client.get(f"/v1/trips/{trip.id}/assistant/execution-options")

    assert response.status_code == 200
    assert response.json() == {"copy_allowed": False, "draft_insert_target_ids": []}


def test_travel_host_forwards_the_same_typed_federation_choice_to_the_generic_continuation():
    trip = _trip()
    conversation_id = uuid4()
    grant_id = uuid4()
    selected = {
        "provider_id": "federation.health.dietary",
        "fields": ["dietary_restrictions"],
        "federation": {
            "grant_id": str(grant_id),
            "issuance_id": str(uuid4()),
            "revision": 3,
            "epoch": 2,
            "source": {"application_id": "health", "workspace_id": None},
            "category": "dietary_restrictions",
            "purpose": "health_dietary_restrictions_to_travel",
            "entities": [
                {
                    "entity_type": "health_profile_item",
                    "entity_id": str(uuid4()),
                    "application_id": "health",
                    "workspace_id": None,
                }
            ],
        },
    }
    turn = SidecarTurnRequest(
        content="Plan a meal that fits my dietary needs.",
        idempotency_key=uuid4(),
        federation_context=(selected,),
    )

    payload = context_routes._turn_payload(trip, conversation_id, turn)

    assert payload["choice"] == {"mode": "automatic"}
    assert payload["federation_context"] == [selected]
    assert payload["launch_context"]["application_id"] == "travel"
    assert payload["launch_context"]["conversation_id"] == str(conversation_id)
    assert payload["launch_context"]["entity_refs"][0]["entity_id"] == str(trip.id)


def test_travel_host_omits_empty_federation_selection():
    trip = _trip()
    payload = context_routes._turn_payload(
        trip,
        uuid4(),
        SidecarTurnRequest(content="Continue without shared context.", idempotency_key=uuid4()),
    )

    assert "federation_context" not in payload


def test_hosted_context_read_requires_service_and_user_identity_separately(
    monkeypatch: pytest.MonkeyPatch,
):
    settings = Settings(
        _env_file=None,
        deployment_mode="hosted",
        owner_id="local-must-not-authorize",
        travel_auth_mode="google_oidc",
        google_oauth_client_id="synthetic-client",
        google_oauth_client_secret="synthetic-credential",
        google_oauth_redirect_uri="https://travel.example.test/auth/google/callback",
        google_oauth_allowed_email="owner@gmail.com",
        allowed_hosts="api.travel.example.test",
        cors_origins="https://travel.example.test",
        personal_ai_auth_mode="google_cloud_run_iam",
        personal_ai_context_enabled=True,
        personal_ai_context_service_iam_audience="https://personal-ai.example.test",
        personal_ai_context_service_account="travel-ai@demo.iam.gserviceaccount.com",
        personal_ai_user_id_token_audience="synthetic-client",
        personal_ai_service_iam_audience="https://personal-ai.example.test",
        personal_ai_service_account="travel-ai@demo.iam.gserviceaccount.com",
        personal_ai_base_url="https://personal-ai.example.test",
    )
    service_checks: list[tuple[str, str, str]] = []
    user_checks: list[str] = []

    def verify_service(token: str, *, audience: str, expected_service_account: str) -> None:
        service_checks.append((token, audience, expected_service_account))

    def verify_user(token: str, _settings: Settings) -> VerifiedPrincipal:
        user_checks.append(token)
        return VerifiedPrincipal(
            issuer="https://accounts.google.com",
            subject="verified-user-subject",
            owner_id="verified-google-owner",
            email="owner@gmail.com",
            issued_at=int(datetime.now(UTC).timestamp()) - 1,
            expires_at=int(datetime.now(UTC).timestamp()) + 300,
        )

    trip = _trip()
    trip.owner_id = "verified-google-owner"
    seen_owners: list[str] = []

    class FakeTripService:
        def __init__(self, _session, owner_id: str):
            seen_owners.append(owner_id)

        def get(self, trip_id):
            assert trip_id == trip.id
            assert seen_owners[-1] == "verified-google-owner"
            return trip

    monkeypatch.setattr(request_middleware, "verify_google_service_token", verify_service)
    monkeypatch.setattr(request_middleware, "principal_from_google_token", verify_user)
    monkeypatch.setattr(context_routes, "TripService", FakeTripService)
    monkeypatch.setattr(app.state, "auth_settings_provider", lambda: settings)
    previous = app.dependency_overrides.copy()
    app.dependency_overrides.update(
        {
            session_dependency: lambda: object(),
            get_settings: lambda: settings,
        }
    )
    body = {
        "operation": "current",
        "entity_refs": [
            {
                "entity_type": "trip",
                "entity_id": str(trip.id),
                "application_id": "travel",
                "workspace_id": None,
            }
        ],
        "fields": ["trip_title"],
        "max_results": 1,
        "max_bytes": 8192,
    }
    try:
        with TestClient(app, base_url="http://localhost") as client:
            service_only = client.post(
                "/v1/integrations/personal-ai/context",
                headers={
                    "X-Application-ID": "travel",
                    "Authorization": "Bearer synthetic-service-token",
                },
                json=body,
            )
            complete = client.post(
                "/v1/integrations/personal-ai/context",
                headers={
                    "X-Application-ID": "travel",
                    "Authorization": "Bearer synthetic-service-token",
                    "X-User-ID-Token": "synthetic-user-id-token",
                },
                json=body,
            )
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)

    assert service_only.status_code == 401
    assert complete.status_code == 200, complete.text
    assert service_checks == [
        (
            "synthetic-service-token",
            "https://personal-ai.example.test",
            "travel-ai@demo.iam.gserviceaccount.com",
        )
    ]
    assert user_checks == ["synthetic-user-id-token"]
    assert seen_owners == ["verified-google-owner"]
