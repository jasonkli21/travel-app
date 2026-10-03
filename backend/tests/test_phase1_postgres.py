from concurrent.futures import ThreadPoolExecutor
from os import environ

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from personal_travel.api.schemas import ItemCreate, PlaceCreate, TripCreate
from personal_travel.services.itinerary import ItineraryService
from personal_travel.services.places import PlaceService
from personal_travel.services.trips import TripService

TEST_DATABASE_URL = environ.get("TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.usefixtures("clean_database"),
    pytest.mark.skipif(
        TEST_DATABASE_URL is None,
        reason="set TEST_DATABASE_URL to run PostgreSQL-backed Phase 1 tests",
    ),
]


def create_trip(client: TestClient, *, start_date: str = "2026-01-02") -> dict[str, object]:
    response = client.post(
        "/v1/trips",
        json={
            "title": "Test trip",
            "start_date": start_date,
            "end_date": "2026-01-04",
            "timezone": "America/Los_Angeles",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_api_range_reconciliation_reindexes_front_and_rolls_back(
    api_client: TestClient,
) -> None:
    trip = create_trip(api_client)
    original_day = next(day for day in trip["days"] if day["date"] == "2026-01-02")

    titled = api_client.patch(
        f"/v1/trips/{trip['id']}/days/{original_day['id']}",
        json={"title": "Arrival"},
    )
    assert titled.status_code == 200, titled.text

    expanded = api_client.patch(f"/v1/trips/{trip['id']}", json={"start_date": "2026-01-01"})
    assert expanded.status_code == 200, expanded.text
    expanded_days = expanded.json()["days"]
    assert [day["day_index"] for day in expanded_days] == [1, 2, 3, 4]
    expanded_original = next(day for day in expanded_days if day["date"] == "2026-01-02")
    assert expanded_original["id"] == original_day["id"]
    assert expanded_original["title"] == "Arrival"

    shrunk = api_client.patch(f"/v1/trips/{trip['id']}", json={"start_date": "2026-01-02"})
    assert shrunk.status_code == 200, shrunk.text
    shrunk_days = shrunk.json()["days"]
    assert [day["date"] for day in shrunk_days] == ["2026-01-02", "2026-01-03", "2026-01-04"]
    assert shrunk_days[0]["id"] == original_day["id"]
    assert shrunk_days[0]["title"] == "Arrival"

    item_response = api_client.post(
        f"/v1/trips/{trip['id']}/days/{original_day['id']}/items",
        json={"title": "Check in"},
    )
    assert item_response.status_code == 201, item_response.text

    blocked = api_client.patch(f"/v1/trips/{trip['id']}", json={"start_date": "2026-01-03"})
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "trip_days_contain_items"

    unchanged = api_client.get(f"/v1/trips/{trip['id']}")
    assert unchanged.status_code == 200
    assert unchanged.json()["start_date"] == "2026-01-02"
    assert len(unchanged.json()["days"][0]["items"]) == 1


def test_api_item_mutations_places_and_owner_scope(
    api_client: TestClient,
    database_engine: Engine,
) -> None:
    trip = create_trip(api_client)
    first_day, second_day = trip["days"][:2]

    place_response = api_client.post("/v1/places", json={"name": "Museum"})
    assert place_response.status_code == 201, place_response.text
    place_id = place_response.json()["id"]

    first_item = api_client.post(
        f"/v1/trips/{trip['id']}/days/{first_day['id']}/items",
        json={"title": "First", "place_id": place_id},
    )
    second_item = api_client.post(
        f"/v1/trips/{trip['id']}/days/{first_day['id']}/items",
        json={"title": "Second"},
    )
    assert first_item.status_code == second_item.status_code == 201
    first_item_id = first_item.json()["days"][0]["items"][0]["id"]
    second_item_id = second_item.json()["days"][0]["items"][1]["id"]

    with Session(database_engine, expire_on_commit=False) as session:
        other_trip = TripService(session, "other").create(
            TripCreate(
                title="Other owner's trip",
                start_date="2026-02-01",
                end_date="2026-02-01",
                timezone="UTC",
            )
        )
        other_place = PlaceService(session, "other").create(PlaceCreate(name="Private place"))
        other_item = ItineraryService(session, "other").create_item(
            other_trip.id,
            other_trip.days[0].id,
            ItemCreate(title="Private item"),
        )

    assert api_client.get(f"/v1/trips/{other_trip.id}").status_code == 404
    assert (
        api_client.patch(
            f"/v1/trips/{trip['id']}/items/{first_item_id}",
            json={"place_id": str(other_place.id)},
        ).status_code
        == 404
    )
    assert (
        api_client.patch(
            f"/v1/trips/{trip['id']}/items/{other_item.id}", json={"title": "No access"}
        ).status_code
        == 404
    )

    moved = api_client.post(
        f"/v1/trips/{trip['id']}/items/{second_item_id}/move",
        json={"destination_day_id": second_day["id"], "position": 0},
    )
    assert moved.status_code == 200, moved.text
    moved_days = moved.json()["days"]
    assert moved_days[0]["items"][0]["id"] == first_item_id
    assert moved_days[1]["items"][0]["id"] == second_item_id

    cleared = api_client.patch(
        f"/v1/trips/{trip['id']}/items/{first_item_id}", json={"place_id": None}
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["days"][0]["items"][0]["place"] is None

    deleted = api_client.delete(f"/v1/trips/{trip['id']}/items/{first_item_id}")
    assert deleted.status_code == 204

    owner_override = api_client.post(
        "/v1/trips",
        json={
            "owner_id": "other",
            "title": "Should be rejected",
            "start_date": "2026-03-01",
            "end_date": "2026-03-01",
        },
    )
    assert owner_override.status_code == 422


def test_concurrent_item_appends_keep_contiguous_order(database_engine: Engine) -> None:
    with Session(database_engine, autoflush=False, expire_on_commit=False) as session:
        trip = TripService(session, "local").create(
            TripCreate(
                title="Concurrent trip",
                start_date="2026-04-01",
                end_date="2026-04-01",
                timezone="UTC",
            )
        )
        trip_id = trip.id
        day_id = trip.days[0].id

    def append_item(title: str) -> None:
        with Session(database_engine, autoflush=False, expire_on_commit=False) as session:
            ItineraryService(session, "local").create_item(
                trip_id,
                day_id,
                ItemCreate(title=title),
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(append_item, "First"), executor.submit(append_item, "Second")]
        for future in futures:
            future.result()

    with Session(database_engine, autoflush=False, expire_on_commit=False) as session:
        result = TripService(session, "local").get(trip_id)
        items = result.days[0].items
        assert [item.sort_order for item in items] == [0, 1]
        assert {item.title for item in items} == {"First", "Second"}


def test_openapi_documents_error_and_local_time_contract(api_client: TestClient) -> None:
    schema = api_client.get("/openapi.json").json()
    trip_path = schema["paths"]["/v1/trips/{trip_id}"]
    assert "ErrorResponse" in str(trip_path["get"]["responses"]["404"])
    assert "ErrorResponse" in str(trip_path["patch"]["responses"]["409"])

    item_schema = schema["components"]["schemas"]["ItemCreate"]
    assert "Local wall-clock time" in item_schema["properties"]["start_time"]["description"]
