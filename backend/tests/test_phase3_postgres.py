from collections.abc import Iterator
from datetime import date
from os import environ

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from personal_travel.api.dependencies import session_dependency
from personal_travel.api.schemas import TripCreate
from personal_travel.clients.geoapify import GeoapifyClient, GeoapifyPlace, GeoapifyRouteLeg
from personal_travel.db.base import Base
from personal_travel.main import app
from personal_travel.models.itinerary import ItineraryItem
from personal_travel.models.place import Place
from personal_travel.models.reservation import Reservation, SavedPlace
from personal_travel.models.trip import Trip, TripDay
from personal_travel.services.trips import TripService

TEST_DATABASE_URL = environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    TEST_DATABASE_URL is None,
    reason="set TEST_DATABASE_URL to run PostgreSQL-backed Phase 3 tests",
)


@pytest.fixture(scope="session")
def database_engine() -> Iterator[Engine]:
    assert TEST_DATABASE_URL is not None
    engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture(autouse=True)
def clean_database(database_engine: Engine) -> Iterator[None]:
    with database_engine.begin() as connection:
        connection.execute(delete(SavedPlace))
        connection.execute(delete(ItineraryItem))
        connection.execute(delete(Reservation))
        connection.execute(delete(TripDay))
        connection.execute(delete(Trip))
        connection.execute(delete(Place))
    yield


@pytest.fixture
def api_client(database_engine: Engine) -> Iterator[TestClient]:
    def override_session() -> Iterator[Session]:
        with Session(database_engine) as session:
            yield session

    app.dependency_overrides[session_dependency] = override_session
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.pop(session_dependency, None)


def create_trip(client: TestClient) -> dict[str, object]:
    response = client.post(
        "/v1/trips",
        json={
            "title": "Phase 3 test trip",
            "start_date": "2026-10-03",
            "end_date": "2026-10-03",
            "timezone": "UTC",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def import_payload(*, name: str = "Museum", note: str | None = "Candidate") -> dict[str, object]:
    return {
        "provider_place_id": "osm:node:42",
        "name": name,
        "address": "Example City",
        "category": "tourism.museum",
        "latitude": 37.7,
        "longitude": -122.4,
        "provider_source_name": "openstreetmap",
        "provider_source_attribution": "© OpenStreetMap contributors",
        "provider_source_license": "Open Database License",
        "provider_source_url": "https://www.openstreetmap.org/copyright",
        "note": note,
    }


def test_search_is_trip_scoped_and_returns_source_attribution(
    api_client: TestClient,
    database_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trip = create_trip(api_client)
    with Session(database_engine) as session:
        foreign_trip = TripService(session, "another-owner").create(
            TripCreate(
                title="Private trip",
                start_date=date(2026, 10, 3),
                end_date=date(2026, 10, 3),
                timezone="UTC",
            )
        )

    calls: list[str] = []

    async def fake_search(
        _client: GeoapifyClient,
        *,
        query: str,
        limit: int,
        bias: tuple[float, float] | None = None,
    ) -> list[GeoapifyPlace]:
        calls.append(query)
        return [
            GeoapifyPlace(
                provider_place_id="osm:node:42",
                name="Museum",
                address="Example City",
                category="tourism.museum",
                latitude=37.7,
                longitude=-122.4,
                provider_source_name="openstreetmap",
                provider_source_attribution="© OpenStreetMap contributors",
                provider_source_license="Open Database License",
                provider_source_url="https://www.openstreetmap.org/copyright",
            )
        ][:limit]

    monkeypatch.setattr(GeoapifyClient, "search_places", fake_search)
    found = api_client.get(f"/v1/trips/{trip['id']}/places/search", params={"q": "museum"})
    hidden = api_client.get(f"/v1/trips/{foreign_trip.id}/places/search", params={"q": "private"})

    assert found.status_code == 200, found.text
    assert found.json()[0]["provider_source_attribution"] == "© OpenStreetMap contributors"
    assert hidden.status_code == 404
    assert calls == ["museum"]


def test_import_is_idempotent_preserves_edits_and_scopes_identity_by_owner(
    api_client: TestClient,
    database_engine: Engine,
) -> None:
    trip = create_trip(api_client)
    with Session(database_engine) as session:
        foreign_place = Place(
            owner_id="another-owner",
            name="Private owner place",
            provider="geoapify",
            provider_place_id="osm:node:42",
        )
        session.add(foreign_place)
        session.commit()
        foreign_place_id = foreign_place.id

    first = api_client.post(f"/v1/trips/{trip['id']}/saved-places/import", json=import_payload())
    assert first.status_code == 200, first.text
    first_saved = first.json()
    place_id = first_saved["place"]["id"]
    assert place_id != str(foreign_place_id)
    assert first_saved["place"]["provider_source_attribution"] == "© OpenStreetMap contributors"

    edited = api_client.patch(
        f"/v1/places/{place_id}",
        json={"name": "Edited by traveler", "address": "New address"},
    )
    assert edited.status_code == 200, edited.text

    repeated = api_client.post(
        f"/v1/trips/{trip['id']}/saved-places/import",
        json=import_payload(name="Updated provider result", note="Replacement note"),
    )
    saved_list = api_client.get(f"/v1/trips/{trip['id']}/saved-places")

    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["id"] == first_saved["id"]
    assert repeated.json()["note"] == "Candidate"
    assert repeated.json()["place"]["name"] == "Edited by traveler"
    assert repeated.json()["place"]["address"] == "New address"
    assert repeated.json()["place"]["provider_source_attribution"] == "© OpenStreetMap contributors"
    assert saved_list.status_code == 200
    assert len(saved_list.json()) == 1


def test_logistics_estimate_uses_snapshot_after_releasing_database_transaction(
    api_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trip = create_trip(api_client)
    day_id = trip["days"][0]["id"]
    place_ids: list[str] = []
    for name, latitude, longitude in (
        ("Museum", 37.7, -122.4),
        ("Restaurant", 37.71, -122.41),
    ):
        place = api_client.post(
            "/v1/places",
            json={"name": name, "latitude": latitude, "longitude": longitude},
        )
        assert place.status_code == 201, place.text
        place_ids.append(place.json()["id"])

    for title, start_time, end_time, place_id in (
        ("Museum visit", "09:00", "09:30", place_ids[0]),
        ("Lunch", "09:40", "10:00", place_ids[1]),
    ):
        created = api_client.post(
            f"/v1/trips/{trip['id']}/days/{day_id}/items",
            json={
                "title": title,
                "start_time": start_time,
                "end_time": end_time,
                "place_id": place_id,
                "status": "planned",
            },
        )
        assert created.status_code == 201, created.text

    calls: list[tuple[list[tuple[float, float]], str]] = []

    async def fake_route(
        _client: GeoapifyClient,
        *,
        waypoints: list[tuple[float, float]],
        mode: str,
    ) -> list[GeoapifyRouteLeg]:
        calls.append((waypoints, mode))
        return [
            GeoapifyRouteLeg(
                duration_seconds=600,
                distance_meters=1000,
                geometry=[[-122.4, 37.7], [-122.41, 37.71]],
            )
        ]

    monkeypatch.setattr(GeoapifyClient, "route", fake_route)
    response = api_client.post(
        f"/v1/trips/{trip['id']}/logistics/estimate",
        json={"day_id": day_id, "mode": "walk", "buffer_minutes": 5},
    )

    assert response.status_code == 200, response.text
    assert calls == [([(37.7, -122.4), (37.71, -122.41)], "walk")]
    assert response.json()["legs"][0]["warning"] is True
    assert response.json()["legs"][0]["available_gap_seconds"] == 600
    assert response.json()["legs"][0]["geometry"] == [[-122.4, 37.7], [-122.41, 37.71]]
