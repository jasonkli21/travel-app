"""Regressions for the Phase 0–4 audit, against the migrated runtime schema."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from personal_travel.api.schemas import ItemCreate, MoveItemRequest, TripCreate
from personal_travel.services.itinerary import ItineraryService
from personal_travel.services.trips import TripService

pytestmark = pytest.mark.usefixtures("clean_database")


def trip_payload(start: str, end: str, timezone: str = "America/New_York") -> dict[str, str]:
    return {"title": "Audit trip", "start_date": start, "end_date": end, "timezone": timezone}


def test_timed_move_rebases_instants_and_preserves_wall_clock(
    api_client: TestClient, database_engine: Engine
) -> None:
    trip = api_client.post("/v1/trips", json=trip_payload("2026-03-07", "2026-03-09")).json()
    first, _, last = trip["days"]
    created = api_client.post(
        f"/v1/trips/{trip['id']}/days/{first['id']}/items",
        json={"title": "Breakfast", "start_time": "09:00", "end_time": "10:00"},
    )
    item = created.json()["days"][0]["items"][0]
    moved = api_client.post(
        f"/v1/trips/{trip['id']}/items/{item['id']}/move",
        json={"destination_day_id": last["id"], "position": 0},
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["days"][2]["items"][0]["start_time"] == "09:00"
    with database_engine.connect() as connection:
        starts_at = connection.scalar(
            text("SELECT starts_at FROM itinerary_items WHERE id = :id"), {"id": item["id"]}
        )
        assert starts_at == datetime(2026, 3, 9, 13, tzinfo=UTC)
    # A subsequent timezone edit must no longer reject the moved item's date.
    edited = api_client.patch(f"/v1/trips/{trip['id']}", json={"timezone": "UTC"})
    assert edited.status_code == 200, edited.text


@pytest.mark.parametrize(
    "start,end,wall_time,error",
    [
        ("2026-03-07", "2026-03-08", "02:30", "invalid_local_time"),
        ("2026-10-31", "2026-11-01", "01:30", "ambiguous_local_time"),
    ],
)
def test_move_to_dst_gap_or_fold_rolls_back_entire_order(
    api_client: TestClient, start: str, end: str, wall_time: str, error: str
) -> None:
    trip = api_client.post("/v1/trips", json=trip_payload(start, end)).json()
    first, last = trip["days"]
    for title in ("Timed", "Untimed"):
        response = api_client.post(
            f"/v1/trips/{trip['id']}/days/{first['id']}/items",
            json={"title": title, "start_time": wall_time if title == "Timed" else None},
        )
        assert response.status_code == 201, response.text
    before = api_client.get(f"/v1/trips/{trip['id']}").json()
    item_id = before["days"][0]["items"][0]["id"]
    moved = api_client.post(
        f"/v1/trips/{trip['id']}/items/{item_id}/move",
        json={"destination_day_id": last["id"], "position": 0},
    )
    assert moved.status_code == 422
    assert moved.json()["error"]["code"] == error
    assert api_client.get(f"/v1/trips/{trip['id']}").json() == before


def test_reorder_and_delete_compact_under_immediate_unique_constraint(
    api_client: TestClient,
) -> None:
    trip = api_client.post("/v1/trips", json=trip_payload("2026-01-01", "2026-01-01")).json()
    path = f"/v1/trips/{trip['id']}"
    for title in ("A", "B", "C"):
        result = api_client.post(
            f"{path}/days/{trip['days'][0]['id']}/items", json={"title": title}
        )
        assert result.status_code == 201, result.text
    items = result.json()["days"][0]["items"]
    result = api_client.post(
        f"{path}/items/{items[2]['id']}/move",
        json={"destination_day_id": trip["days"][0]["id"], "position": 0},
    )
    assert result.status_code == 200, result.text
    assert [item["title"] for item in result.json()["days"][0]["items"]] == ["C", "A", "B"]
    assert api_client.delete(f"{path}/items/{items[0]['id']}").status_code == 204
    items = api_client.get(path).json()["days"][0]["items"]
    assert [(item["title"], item["sort_order"]) for item in items] == [("C", 0), ("B", 1)]


def test_retained_aggregate_is_refreshed_after_writes(
    api_client: TestClient, database_engine: Engine
) -> None:
    trip = api_client.post("/v1/trips", json=trip_payload("2026-01-01", "2026-01-01")).json()
    with Session(database_engine, autoflush=False, expire_on_commit=False) as session:
        retained = TripService(session, "local").get(UUID(trip["id"]))
        session.commit()
        # Holding an aggregate should not make its collections stale at the next read.
        ItineraryService(session, "local").create_item(
            UUID(trip["id"]), retained.days[0].id, ItemCreate(title="Appended")
        )
        refreshed = TripService(session, "local").get(retained.id)
        assert [item.title for item in refreshed.days[0].items] == ["Appended"]


def test_concurrent_cross_day_moves_keep_unique_contiguous_positions(
    database_engine: Engine,
) -> None:
    with Session(database_engine, autoflush=False, expire_on_commit=False) as session:
        trip = TripService(session, "local").create(
            TripCreate(**trip_payload("2026-01-01", "2026-01-02"))
        )
        trip_id, first_id, last_id = trip.id, trip.days[0].id, trip.days[1].id
        ids = []
        for title in ("A", "B"):
            item = ItineraryService(session, "local").create_item(
                trip_id, first_id, ItemCreate(title=title)
            )
            ids.append(item.id)

    def move(item_id: UUID) -> None:
        with Session(database_engine, autoflush=False, expire_on_commit=False) as session:
            ItineraryService(session, "local").move_item(
                trip_id, item_id, MoveItemRequest(destination_day_id=last_id, position=0)
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(move, ids))
    with Session(database_engine) as session:
        result = TripService(session, "local").get(trip_id)
        assert result.days[0].items == []
        assert {item.id for item in result.days[1].items} == set(ids)
        assert [item.sort_order for item in result.days[1].items] == [0, 1]


@pytest.mark.parametrize("latitude,longitude", [(0, None), (91, 0), (0, 181), ("NaN", 0)])
def test_sql_rejects_invalid_place_coordinates(
    database_engine: Engine, latitude: object, longitude: object
) -> None:
    with pytest.raises(IntegrityError), database_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO places (id, owner_id, name, latitude, longitude) "
                "VALUES ('a68c6bdd-b993-45d2-932a-a6c84c9f6832', 'local', 'Invalid', :lat, :lon)"
            ),
            {"lat": latitude, "lon": longitude},
        )


@pytest.mark.parametrize("position", [0, 1, 2])
def test_delete_any_position_compacts(api_client: TestClient, position: int) -> None:
    trip = api_client.post("/v1/trips", json=trip_payload("2026-01-01", "2026-01-01")).json()
    path = f"/v1/trips/{trip['id']}"
    for title in ("A", "B", "C"):
        result = api_client.post(
            f"{path}/days/{trip['days'][0]['id']}/items", json={"title": title}
        )
        assert result.status_code == 201
    items = result.json()["days"][0]["items"]
    assert api_client.delete(f"{path}/items/{items[position]['id']}").status_code == 204
    assert [item["sort_order"] for item in api_client.get(path).json()["days"][0]["items"]] == [
        0,
        1,
    ]
