from collections.abc import Iterator
from os import environ
from typing import Any, Literal, NoReturn
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from personal_travel.api.schemas import ManualSavedPlaceCreate, TripCreate
from personal_travel.clients.personal_ai import PersonalAIClient, PersonalAIError
from personal_travel.config import Settings, get_settings
from personal_travel.main import app
from personal_travel.models.place import Place
from personal_travel.models.reservation import SavedPlace
from personal_travel.services.errors import DomainError
from personal_travel.services.saved_places import SavedPlaceService
from personal_travel.services.trips import TripService

TEST_DATABASE_URL = environ.get("TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.usefixtures("clean_database"),
    pytest.mark.skipif(
        TEST_DATABASE_URL is None,
        reason="set TEST_DATABASE_URL to run PostgreSQL-backed Phase 4 tests",
    ),
]


@pytest.fixture(autouse=True)
def enable_research() -> Iterator[None]:
    app.dependency_overrides[get_settings] = lambda: Settings(personal_ai_research_enabled=True)
    yield
    app.dependency_overrides.pop(get_settings, None)


def create_trip(client: TestClient, title: str) -> dict[str, Any]:
    response = client.post(
        "/v1/trips",
        json={
            "title": title,
            "start_date": "2026-10-03",
            "end_date": "2026-10-03",
            "timezone": "UTC",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_manual_saved_place_creates_owner_place_and_trip_candidate_atomically(
    database_engine: Engine,
) -> None:
    with Session(database_engine, expire_on_commit=False) as session:
        trip = TripService(session, "local").create(
            TripCreate(
                title="Manual candidate test",
                start_date="2026-10-03",
                end_date="2026-10-03",
                timezone="UTC",
            )
        )
        saved = SavedPlaceService(session, "local").create_manual(
            trip.id,
            ManualSavedPlaceCreate(
                name="Traveler-entered cafe",
                address="Example City",
                category="Cafe",
                note="Check opening hours",
            ),
        )
        place_id = saved.place_id
        saved_place_id = saved.id

    with Session(database_engine, autoflush=False, expire_on_commit=False) as session:
        place = session.get(Place, place_id)
        saved_place = session.get(SavedPlace, saved_place_id)
        assert place is not None
        assert saved_place is not None
        assert place.owner_id == "local"
        assert place.provider is None
        assert place.name == "Traveler-entered cafe"
        assert saved_place.owner_id == "local"
        assert saved_place.trip_id == trip.id
        assert saved_place.place_id == place.id
        assert saved_place.note == "Check opening hours"
        assert session.scalar(select(func.count()).select_from(Place)) == 1
        assert session.scalar(select(func.count()).select_from(SavedPlace)) == 1


def test_manual_saved_place_rejects_foreign_trip_without_creating_place(
    database_engine: Engine,
) -> None:
    with Session(database_engine, autoflush=False, expire_on_commit=False) as session:
        foreign_trip = TripService(session, "another-owner").create(
            TripCreate(
                title="Foreign trip",
                start_date="2026-10-03",
                end_date="2026-10-03",
                timezone="UTC",
            )
        )
        with pytest.raises(DomainError) as error:
            SavedPlaceService(session, "local").create_manual(
                foreign_trip.id,
                ManualSavedPlaceCreate(name="Must not be created"),
            )
        assert error.value.status_code == 404

    with Session(database_engine, autoflush=False, expire_on_commit=False) as session:
        assert session.scalar(select(func.count()).select_from(Place)) == 0
        assert session.scalar(select(func.count()).select_from(SavedPlace)) == 0


def test_research_route_rejects_foreign_trip_and_day_before_ai_call_and_sanitizes_failure(
    api_client: TestClient,
    database_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local_trip = create_trip(api_client, "Local trip")
    other_local_trip = create_trip(api_client, "Second local trip")
    with Session(database_engine, autoflush=False, expire_on_commit=False) as session:
        foreign_trip = TripService(session, "another-owner").create(
            TripCreate(
                title="Foreign trip",
                start_date="2026-10-03",
                end_date="2026-10-03",
                timezone="UTC",
            )
        )

    foreign_trip_id = str(foreign_trip.id)
    foreign_day_id = str(foreign_trip.days[0].id)

    calls: list[dict[str, object]] = []

    async def fail_research(
        _client: PersonalAIClient,
        *,
        question: str,
        freshness: Literal["general", "current"],
        idempotency_key: UUID,
    ) -> NoReturn:
        calls.append(
            {
                "question": question,
                "freshness": freshness,
                "idempotency_key": idempotency_key,
            }
        )
        raise PersonalAIError("secret upstream response body")

    monkeypatch.setattr(PersonalAIClient, "research", fail_research)

    def request_payload(day_id: str) -> dict[str, str]:
        return {
            "day_id": day_id,
            "question": "Find a quiet dinner",
            "freshness": "current",
            "idempotency_key": "1caa92f9-c49b-42cc-9282-e9255b4a7a0d",
        }

    foreign_response = api_client.post(
        f"/v1/trips/{foreign_trip_id}/research",
        json=request_payload(foreign_day_id),
    )
    assert foreign_response.status_code == 404
    assert calls == []

    mismatched_day_response = api_client.post(
        f"/v1/trips/{local_trip['id']}/research",
        json=request_payload(other_local_trip["days"][0]["id"]),
    )
    assert mismatched_day_response.status_code == 404
    assert calls == []

    upstream_failure_response = api_client.post(
        f"/v1/trips/{local_trip['id']}/research",
        json=request_payload(local_trip["days"][0]["id"]),
    )
    assert upstream_failure_response.status_code == 503
    assert upstream_failure_response.json()["error"]["code"] == "research_unavailable"
    assert "secret upstream response body" not in upstream_failure_response.text
    assert len(calls) == 1
