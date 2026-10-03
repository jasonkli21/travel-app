from collections.abc import Iterator
from datetime import UTC, datetime
from os import environ
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from personal_travel.api.dependencies import session_dependency
from personal_travel.api.schemas import ReservationCreate, TripCreate
from personal_travel.db.base import Base
from personal_travel.main import app
from personal_travel.models.itinerary import ItineraryItem
from personal_travel.models.place import Place
from personal_travel.models.reservation import Reservation, SavedPlace
from personal_travel.models.trip import Trip, TripDay
from personal_travel.services.reservations import ReservationService
from personal_travel.services.trips import TripService

TEST_DATABASE_URL = environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    TEST_DATABASE_URL is None,
    reason="set TEST_DATABASE_URL to run PostgreSQL-backed Phase 2 tests",
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


def create_trip(
    client: TestClient,
    *,
    timezone: str = "UTC",
    start_date: str = "2026-05-10",
    end_date: str = "2026-05-11",
) -> dict[str, object]:
    response = client.post(
        "/v1/trips",
        json={
            "title": "Phase 2 test trip",
            "start_date": start_date,
            "end_date": end_date,
            "timezone": timezone,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_reservations_saved_places_and_conflicts(api_client: TestClient) -> None:
    trip = create_trip(api_client)
    day = trip["days"][0]

    place_response = api_client.post(
        "/v1/places",
        json={
            "name": "  Central Station ",
            "category": "Transit",
            "phone": "+1 555 0100",
            "website_url": "https://station.example",
        },
    )
    assert place_response.status_code == 201, place_response.text
    place = place_response.json()
    assert place["category"] == "Transit"

    saved = api_client.post(
        f"/v1/trips/{trip['id']}/saved-places",
        json={"place_id": place["id"], "note": "Arrival candidate"},
    )
    assert saved.status_code == 201, saved.text
    assert saved.json()["place"]["website_url"] == "https://station.example"
    duplicate = api_client.post(
        f"/v1/trips/{trip['id']}/saved-places", json={"place_id": place["id"]}
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "saved_place_exists"

    item_response = api_client.post(
        f"/v1/trips/{trip['id']}/days/{day['id']}/items",
        json={"title": "Station transfer", "start_time": "10:00", "end_time": "11:00"},
    )
    assert item_response.status_code == 201, item_response.text
    item_id = item_response.json()["days"][0]["items"][0]["id"]

    reservation_response = api_client.post(
        f"/v1/trips/{trip['id']}/reservations",
        json={
            "reservation_type": "train",
            "status": "confirmed",
            "provider_name": "Railway",
            "confirmation_code": "ABC123",
            "start_date": "2026-05-10",
            "start_time": "10:30",
            "end_date": "2026-05-10",
            "end_time": "11:30",
            "place_id": place["id"],
        },
    )
    assert reservation_response.status_code == 201, reservation_response.text
    reservation = reservation_response.json()
    assert reservation["start_time"] == "10:30"
    assert [conflict["item_id"] for conflict in reservation["conflicts"]] == [item_id]

    linked = api_client.patch(
        f"/v1/trips/{trip['id']}/items/{item_id}",
        json={"reservation_id": reservation["id"]},
    )
    assert linked.status_code == 200, linked.text
    linked_item = linked.json()["days"][0]["items"][0]
    assert linked_item["reservation"]["id"] == reservation["id"]
    assert linked_item["reservation"]["conflict_count"] == 0
    assert api_client.get(f"/v1/trips/{trip['id']}/reservations").json()[0]["conflicts"] == []

    second_item_response = api_client.post(
        f"/v1/trips/{trip['id']}/days/{day['id']}/items",
        json={"title": "Overlapping walk", "start_time": "10:45", "end_time": "11:15"},
    )
    assert second_item_response.status_code == 201, second_item_response.text
    second_item_id = second_item_response.json()["days"][0]["items"][1]["id"]
    third_item_response = api_client.post(
        f"/v1/trips/{trip['id']}/days/{day['id']}/items",
        json={"title": "Second overlap", "start_time": "10:50", "end_time": "11:05"},
    )
    assert third_item_response.status_code == 201, third_item_response.text
    third_item_id = third_item_response.json()["days"][0]["items"][2]["id"]
    with_conflict = api_client.get(f"/v1/trips/{trip['id']}/reservations")
    assert {conflict["item_id"] for conflict in with_conflict.json()[0]["conflicts"]} == {
        second_item_id,
        third_item_id,
    }

    for item_id in (second_item_id, third_item_id):
        cancelled_item = api_client.patch(
            f"/v1/trips/{trip['id']}/items/{item_id}",
            json={"status": "cancelled"},
        )
        assert cancelled_item.status_code == 200, cancelled_item.text
    assert api_client.get(f"/v1/trips/{trip['id']}/reservations").json()[0]["conflicts"] == []

    cancelled = api_client.patch(
        f"/v1/trips/{trip['id']}/reservations/{reservation['id']}",
        json={"status": "cancelled"},
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["conflicts"] == []

    deleted = api_client.delete(f"/v1/trips/{trip['id']}/reservations/{reservation['id']}")
    assert deleted.status_code == 204
    after_delete = api_client.get(f"/v1/trips/{trip['id']}")
    assert after_delete.status_code == 200
    assert after_delete.json()["days"][0]["items"][0]["reservation"] is None

    saved_id = saved.json()["id"]
    updated_saved = api_client.patch(
        f"/v1/trips/{trip['id']}/saved-places/{saved_id}",
        json={"note": "Updated candidate"},
    )
    assert updated_saved.status_code == 200
    assert updated_saved.json()["note"] == "Updated candidate"
    assert api_client.delete(f"/v1/trips/{trip['id']}/saved-places/{saved_id}").status_code == 204


def test_reservation_links_and_records_are_owner_and_trip_scoped(
    api_client: TestClient,
    database_engine: Engine,
) -> None:
    first_trip = create_trip(api_client)
    second_trip = create_trip(api_client, start_date="2026-06-01", end_date="2026-06-01")
    first_reservation = api_client.post(
        f"/v1/trips/{first_trip['id']}/reservations",
        json={"provider_name": "Hotel"},
    ).json()
    assert first_reservation["conflicts"] == []
    second_item = api_client.post(
        f"/v1/trips/{second_trip['id']}/days/{second_trip['days'][0]['id']}/items",
        json={"title": "Private item"},
    )
    assert second_item.status_code == 201
    second_item_id = second_item.json()["days"][0]["items"][0]["id"]
    cross_trip_link = api_client.patch(
        f"/v1/trips/{second_trip['id']}/items/{second_item_id}",
        json={"reservation_id": first_reservation["id"]},
    )
    assert cross_trip_link.status_code == 404

    with Session(database_engine, expire_on_commit=False) as session:
        other_trip = TripService(session, "other").create(
            TripCreate(
                title="Other owner",
                start_date="2026-07-01",
                end_date="2026-07-01",
                timezone="UTC",
            )
        )
        other_reservation = ReservationService(session, "other").create(
            other_trip.id,
            ReservationCreate(provider_name="Private hotel"),
        )

    hidden = api_client.get(f"/v1/trips/{other_trip.id}/reservations")
    assert hidden.status_code == 404
    hidden_update = api_client.patch(
        f"/v1/trips/{first_trip['id']}/reservations/{other_reservation.id}",
        json={"status": "confirmed"},
    )
    assert hidden_update.status_code == 404


def test_reservation_dst_gap_is_rejected(api_client: TestClient) -> None:
    trip = create_trip(
        api_client,
        timezone="America/New_York",
        start_date="2026-03-08",
        end_date="2026-03-08",
    )
    response = api_client.post(
        f"/v1/trips/{trip['id']}/reservations",
        json={
            "provider_name": "Flight",
            "start_date": "2026-03-08",
            "start_time": "02:30",
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_local_time"


def test_timezone_edit_preserves_reservation_wall_clock_values(api_client: TestClient) -> None:
    trip = create_trip(
        api_client,
        timezone="America/Los_Angeles",
        start_date="2026-01-10",
        end_date="2026-01-10",
    )
    created = api_client.post(
        f"/v1/trips/{trip['id']}/reservations",
        json={
            "provider_name": "Hotel",
            "start_date": "2026-01-10",
            "start_time": "10:00",
            "end_date": "2026-01-10",
            "end_time": "11:00",
        },
    )
    assert created.status_code == 201, created.text

    updated_trip = api_client.patch(
        f"/v1/trips/{trip['id']}",
        json={"timezone": "America/New_York"},
    )
    assert updated_trip.status_code == 200, updated_trip.text
    reservation = api_client.get(f"/v1/trips/{trip['id']}/reservations").json()[0]
    assert reservation["start_date"] == "2026-01-10"
    assert reservation["start_time"] == "10:00"
    assert reservation["end_date"] == "2026-01-10"
    assert reservation["end_time"] == "11:00"


def test_reservations_with_same_schedule_use_creation_order(
    api_client: TestClient,
    database_engine: Engine,
) -> None:
    trip = create_trip(api_client, start_date="2026-08-02", end_date="2026-08-02")
    payload = {
        "provider_name": "Same-time provider",
        "start_date": "2026-08-02",
        "start_time": "10:00",
    }
    first = api_client.post(f"/v1/trips/{trip['id']}/reservations", json=payload)
    second = api_client.post(f"/v1/trips/{trip['id']}/reservations", json=payload)
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text

    with Session(database_engine) as session:
        first_record = session.get(Reservation, UUID(first.json()["id"]))
        second_record = session.get(Reservation, UUID(second.json()["id"]))
        assert first_record is not None
        assert second_record is not None
        first_record.created_at = datetime(2026, 1, 1, tzinfo=UTC)
        second_record.created_at = datetime(2026, 1, 2, tzinfo=UTC)
        session.commit()

    listed = api_client.get(f"/v1/trips/{trip['id']}/reservations")
    assert listed.status_code == 200, listed.text
    assert [reservation["id"] for reservation in listed.json()] == [
        first.json()["id"],
        second.json()["id"],
    ]


def test_one_sided_reservation_schedule_is_a_point_conflict(api_client: TestClient) -> None:
    trip = create_trip(api_client, start_date="2026-08-01", end_date="2026-08-01")
    day = trip["days"][0]
    item = api_client.post(
        f"/v1/trips/{trip['id']}/days/{day['id']}/items",
        json={"title": "Timed activity", "start_time": "10:00", "end_time": "11:00"},
    )
    assert item.status_code == 201, item.text
    item_id = item.json()["days"][0]["items"][0]["id"]
    reservation = api_client.post(
        f"/v1/trips/{trip['id']}/reservations",
        json={"provider_name": "Timed booking", "start_date": "2026-08-01", "start_time": "10:00"},
    )
    assert reservation.status_code == 201, reservation.text
    assert reservation.json()["conflicts"][0]["item_id"] == item_id
