from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from personal_travel.api import middleware as request_middleware
from personal_travel.api.dependencies import session_dependency
from personal_travel.api.routes import personal_ai_context as context_routes
from personal_travel.api.schemas.personal_ai_context import (
    ContextEntityReference,
    ContextReadRequest,
)
from personal_travel.auth.contracts import VerifiedPrincipal
from personal_travel.config import Settings, get_settings
from personal_travel.main import app
from personal_travel.models.itinerary import ItineraryItem
from personal_travel.models.reservation import Reservation
from personal_travel.models.trip import Trip, TripDay


def _trip(*, days: list[TripDay] | None = None) -> Trip:
    now = datetime.now(UTC)
    return Trip(
        id=uuid4(),
        owner_id="context-owner",
        title="A private trip",
        start_date=date(2026, 10, 11),
        end_date=date(2026, 10, 12),
        timezone="America/Los_Angeles",
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
    assert "Sample Air" in reservation_record.payload["reservation_summary"]
    assert "confirmed" in reservation_record.payload["reservation_summary"]
    assert "PRIVATE-CONFIRMATION-123" not in serialized
    assert "private-mailbox-reference" not in serialized
    assert "Private booking notes" not in serialized
    assert "Private itinerary notes" not in serialized


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


def test_research_context_marks_fresh_citations_external_and_omits_expired_evidence(
    local_context_client,
    monkeypatch: pytest.MonkeyPatch,
):
    client, _settings = local_context_client
    now = datetime.now(UTC)
    current_id, expired_id = uuid4(), uuid4()
    citation = SimpleNamespace(
        title="Current source",
        url="https://evidence.example/current",
        observed_at=now - timedelta(minutes=5),
        expires_at=now + timedelta(hours=1),
    )
    current = SimpleNamespace(
        id=current_id,
        state="completed",
        expires_at=now + timedelta(minutes=45),
        answer="A synthetic, currently cited fact.",
        citations=[citation],
        revision=2,
    )
    expired = SimpleNamespace(
        id=expired_id,
        state="completed",
        expires_at=now - timedelta(seconds=1),
        answer="This evidence is stale.",
        citations=[citation],
        revision=2,
    )

    class FakeContextClient:
        async def get_research_context(self, session_id):
            return {current_id: current, expired_id: expired}[session_id]

    monkeypatch.setattr(
        context_routes,
        "_context_client",
        lambda _request, timeout_seconds=None: FakeContextClient(),
    )
    response = client.post(
        "/v1/integrations/personal-ai/context",
        json={
            "operation": "search",
            "entity_refs": [
                {
                    "entity_type": "research_session",
                    "entity_id": str(current_id),
                    "application_id": "travel",
                    "workspace_id": None,
                },
                {
                    "entity_type": "research_session",
                    "entity_id": str(expired_id),
                    "application_id": "travel",
                    "workspace_id": None,
                },
            ],
            "fields": ["passage", "observed_at", "expires_at", "citations"],
            "max_results": 2,
            "max_bytes": 16_384,
        },
    )

    assert response.status_code == 200
    assert len(response.json()["items"]) == 1
    record = response.json()["items"][0]
    assert record["entity_id"] == str(current_id)
    assert record["authority"] == "external"
    assert record["expires_at"] is not None
    assert record["source_refs"][0]["kind"] == "url"


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
