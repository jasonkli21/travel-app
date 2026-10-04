from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from personal_travel.api.schemas import ItemCreate
from personal_travel.services.errors import DomainError
from personal_travel.services.itinerary import ItineraryService

pytestmark = pytest.mark.usefixtures("clean_database")


def create_trip(client: TestClient) -> dict[str, object]:
    response = client.post(
        "/v1/trips",
        json={
            "title": "Revision trip",
            "start_date": "2026-05-10",
            "end_date": "2026-05-11",
            "timezone": "America/Los_Angeles",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def revision_header(revision: int) -> dict[str, str]:
    return {"X-Expected-Revision": str(revision)}


def current_revision(client: TestClient, trip_id: str) -> int:
    response = client.get(f"/v1/trips/{trip_id}")
    assert response.status_code == 200, response.text
    return response.json()["revision"]


def test_every_trip_mutation_family_advances_revision_and_noops_do_not(
    api_client: TestClient,
) -> None:
    trip = create_trip(api_client)
    trip_id = trip["id"]
    first_day, second_day = trip["days"]
    assert trip["revision"] == 0

    updated_trip = api_client.patch(
        f"/v1/trips/{trip_id}",
        json={"title": "Updated trip"},
        headers=revision_header(0),
    )
    assert updated_trip.status_code == 200, updated_trip.text
    assert updated_trip.json()["revision"] == 1
    empty_patch = api_client.patch(f"/v1/trips/{trip_id}", json={}, headers=revision_header(1))
    assert empty_patch.status_code == 200, empty_patch.text
    assert empty_patch.json()["revision"] == 1

    titled_day = api_client.patch(
        f"/v1/trips/{trip_id}/days/{first_day['id']}",
        json={"title": "Arrival"},
        headers=revision_header(1),
    )
    assert titled_day.status_code == 200, titled_day.text
    assert titled_day.json()["revision"] == 2

    place_response = api_client.post("/v1/places", json={"name": "Museum"})
    assert place_response.status_code == 201, place_response.text
    place = place_response.json()
    assert place["revision"] == 0
    edited_place = api_client.patch(
        f"/v1/places/{place['id']}",
        json={"name": "City Museum"},
        headers=revision_header(0),
    )
    assert edited_place.status_code == 200, edited_place.text
    assert edited_place.json()["revision"] == 1
    place_noop = api_client.patch(
        f"/v1/places/{place['id']}",
        json={"name": "City Museum"},
        headers=revision_header(1),
    )
    assert place_noop.status_code == 200, place_noop.text
    assert place_noop.json()["revision"] == 1
    assert current_revision(api_client, trip_id) == 2

    created_item = api_client.post(
        f"/v1/trips/{trip_id}/days/{first_day['id']}/items",
        json={"title": "Museum visit", "start_time": "09:00", "end_time": "10:00"},
        headers=revision_header(2),
    )
    assert created_item.status_code == 201, created_item.text
    assert created_item.json()["revision"] == 3
    item = created_item.json()["days"][0]["items"][0]
    item_noop = api_client.patch(
        f"/v1/trips/{trip_id}/items/{item['id']}",
        json={"title": "Museum visit"},
        headers=revision_header(3),
    )
    assert item_noop.status_code == 200, item_noop.text
    assert item_noop.json()["revision"] == 3
    item_edit = api_client.patch(
        f"/v1/trips/{trip_id}/items/{item['id']}",
        json={"title": "Morning museum"},
        headers=revision_header(3),
    )
    assert item_edit.status_code == 200, item_edit.text
    assert item_edit.json()["revision"] == 4

    same_position = api_client.post(
        f"/v1/trips/{trip_id}/items/{item['id']}/move",
        json={"destination_day_id": first_day["id"], "position": 0},
        headers=revision_header(4),
    )
    assert same_position.status_code == 200, same_position.text
    assert same_position.json()["revision"] == 4
    moved_item = api_client.post(
        f"/v1/trips/{trip_id}/items/{item['id']}/move",
        json={"destination_day_id": second_day["id"], "position": 0},
        headers=revision_header(4),
    )
    assert moved_item.status_code == 200, moved_item.text
    assert moved_item.json()["revision"] == 5
    deleted_item = api_client.delete(
        f"/v1/trips/{trip_id}/items/{item['id']}", headers=revision_header(5)
    )
    assert deleted_item.status_code == 204
    assert current_revision(api_client, trip_id) == 6

    reservation = api_client.post(
        f"/v1/trips/{trip_id}/reservations",
        json={"provider_name": "Railway"},
        headers=revision_header(6),
    )
    assert reservation.status_code == 201, reservation.text
    reservation_id = reservation.json()["id"]
    assert current_revision(api_client, trip_id) == 7
    reservation_noop = api_client.patch(
        f"/v1/trips/{trip_id}/reservations/{reservation_id}",
        json={"provider_name": "Railway"},
        headers=revision_header(7),
    )
    assert reservation_noop.status_code == 200, reservation_noop.text
    assert current_revision(api_client, trip_id) == 7
    reservation_edit = api_client.patch(
        f"/v1/trips/{trip_id}/reservations/{reservation_id}",
        json={"provider_name": "Updated Railway"},
        headers=revision_header(7),
    )
    assert reservation_edit.status_code == 200, reservation_edit.text
    assert current_revision(api_client, trip_id) == 8
    reservation_delete = api_client.delete(
        f"/v1/trips/{trip_id}/reservations/{reservation_id}", headers=revision_header(8)
    )
    assert reservation_delete.status_code == 204
    assert current_revision(api_client, trip_id) == 9

    saved_place = api_client.post(
        f"/v1/trips/{trip_id}/saved-places",
        json={"place_id": place["id"], "note": None},
        headers=revision_header(9),
    )
    assert saved_place.status_code == 201, saved_place.text
    saved_place_id = saved_place.json()["id"]
    assert current_revision(api_client, trip_id) == 10
    saved_noop = api_client.patch(
        f"/v1/trips/{trip_id}/saved-places/{saved_place_id}",
        json={"note": None},
        headers=revision_header(10),
    )
    assert saved_noop.status_code == 200, saved_noop.text
    assert current_revision(api_client, trip_id) == 10
    saved_edit = api_client.patch(
        f"/v1/trips/{trip_id}/saved-places/{saved_place_id}",
        json={"note": "Candidate note"},
        headers=revision_header(10),
    )
    assert saved_edit.status_code == 200, saved_edit.text
    assert current_revision(api_client, trip_id) == 11
    saved_delete = api_client.delete(
        f"/v1/trips/{trip_id}/saved-places/{saved_place_id}", headers=revision_header(11)
    )
    assert saved_delete.status_code == 204
    assert current_revision(api_client, trip_id) == 12

    manual_candidate = api_client.post(
        f"/v1/trips/{trip_id}/saved-places/manual",
        json={"name": "Manual candidate", "note": "Entered by traveler"},
        headers=revision_header(12),
    )
    assert manual_candidate.status_code == 201, manual_candidate.text
    assert manual_candidate.json()["place"]["revision"] == 0
    assert current_revision(api_client, trip_id) == 13

    imported_payload = {
        "provider_place_id": "provider:revision-test",
        "name": "Provider candidate",
        "latitude": 37.7,
        "longitude": -122.4,
        "provider_source_name": "test provider",
        "provider_source_attribution": "test attribution",
        "note": "Imported",
    }
    imported = api_client.post(
        f"/v1/trips/{trip_id}/saved-places/import",
        json=imported_payload,
        headers=revision_header(13),
    )
    assert imported.status_code == 200, imported.text
    assert imported.json()["place"]["revision"] == 0
    assert current_revision(api_client, trip_id) == 14
    repeated_import = api_client.post(
        f"/v1/trips/{trip_id}/saved-places/import",
        json={**imported_payload, "name": "Ignored changed provider name"},
        headers=revision_header(14),
    )
    assert repeated_import.status_code == 200, repeated_import.text
    assert current_revision(api_client, trip_id) == 14

    rebased_trip = api_client.patch(
        f"/v1/trips/{trip_id}",
        json={"start_date": "2026-05-09", "timezone": "UTC"},
        headers=revision_header(14),
    )
    assert rebased_trip.status_code == 200, rebased_trip.text
    assert rebased_trip.json()["revision"] == 15


def test_stale_preconditions_fail_before_mutation_and_legacy_headers_remain_optional(
    api_client: TestClient,
) -> None:
    trip = create_trip(api_client)
    trip_id = trip["id"]
    updated = api_client.patch(
        f"/v1/trips/{trip_id}", json={"title": "New title"}, headers=revision_header(0)
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["revision"] == 1

    stale = api_client.patch(
        f"/v1/trips/{trip_id}", json={"title": "Must not be saved"}, headers=revision_header(0)
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "stale_revision"
    assert stale.json()["error"]["details"] == {
        "aggregate": "trip",
        "expected_revision": 0,
        "current_revision": 1,
    }
    current = api_client.get(f"/v1/trips/{trip_id}").json()
    assert current["title"] == "New title"
    assert current["revision"] == 1

    legacy_write = api_client.patch(f"/v1/trips/{trip_id}", json={"title": "Legacy write"})
    assert legacy_write.status_code == 200, legacy_write.text
    assert legacy_write.json()["revision"] == 2

    place = api_client.post("/v1/places", json={"name": "Shared place"}).json()
    place_stale = api_client.patch(
        f"/v1/places/{place['id']}",
        json={"name": "Stale place update"},
        headers=revision_header(2),
    )
    assert place_stale.status_code == 409
    assert place_stale.json()["error"]["code"] == "stale_revision"
    assert api_client.get(f"/v1/trips/{trip_id}").json()["revision"] == 2


def test_shared_place_revision_changes_without_fanning_out_to_trips(
    api_client: TestClient,
) -> None:
    first_trip = create_trip(api_client)
    second_trip = create_trip(api_client)
    place_response = api_client.post("/v1/places", json={"name": "Shared library"})
    assert place_response.status_code == 201, place_response.text
    place = place_response.json()

    for trip in (first_trip, second_trip):
        saved = api_client.post(
            f"/v1/trips/{trip['id']}/saved-places",
            json={"place_id": place["id"], "note": None},
            headers=revision_header(0),
        )
        assert saved.status_code == 201, saved.text

    place_edit = api_client.patch(
        f"/v1/places/{place['id']}",
        json={"name": "Shared library, renamed"},
        headers=revision_header(0),
    )
    assert place_edit.status_code == 200, place_edit.text
    assert place_edit.json()["revision"] == 1
    assert current_revision(api_client, first_trip["id"]) == 1
    assert current_revision(api_client, second_trip["id"]) == 1


def test_concurrent_same_revision_writes_allow_only_one_committer(
    api_client: TestClient,
    database_engine: Engine,
) -> None:
    trip = create_trip(api_client)
    trip_id = UUID(trip["id"])
    day_id = UUID(trip["days"][0]["id"])
    barrier = Barrier(2)

    def create_item(title: str) -> str:
        barrier.wait(timeout=10)
        try:
            with Session(database_engine, autoflush=False, expire_on_commit=False) as session:
                ItineraryService(session, "local").create_item(
                    trip_id,
                    day_id,
                    ItemCreate(title=title),
                    expected_revision=0,
                )
            return "committed"
        except DomainError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(create_item, ("First", "Second")))

    assert sorted(outcomes) == ["committed", "stale_revision"]
    current = api_client.get(f"/v1/trips/{trip_id}").json()
    assert current["revision"] == 1
    items = [item for day in current["days"] for item in day["items"]]
    assert len(items) == 1


def test_failed_commit_rolls_back_item_and_revision(
    api_client: TestClient,
    database_engine: Engine,
) -> None:
    trip = create_trip(api_client)
    trip_id = UUID(trip["id"])
    day_id = UUID(trip["days"][0]["id"])

    with Session(database_engine, autoflush=False, expire_on_commit=False) as session:

        def fail_commit(_session: Session) -> None:
            raise RuntimeError("injected commit failure")

        event.listen(session, "before_commit", fail_commit)
        try:
            with pytest.raises(RuntimeError, match="injected commit failure"):
                ItineraryService(session, "local").create_item(
                    trip_id, day_id, ItemCreate(title="Rolled back"), expected_revision=0
                )
        finally:
            event.remove(session, "before_commit", fail_commit)
            session.rollback()

    unchanged = api_client.get(f"/v1/trips/{trip_id}").json()
    assert unchanged["revision"] == 0
    assert unchanged["days"][0]["items"] == []


@pytest.mark.parametrize(
    "start_date,end_date,wall_time,error_code",
    [
        ("2026-03-07", "2026-03-08", "02:30", "invalid_local_time"),
        ("2026-10-31", "2026-11-01", "01:30", "ambiguous_local_time"),
    ],
)
def test_dst_move_rejection_preserves_revision_order_and_schedule(
    api_client: TestClient,
    start_date: str,
    end_date: str,
    wall_time: str,
    error_code: str,
) -> None:
    response = api_client.post(
        "/v1/trips",
        json={
            "title": "DST revision trip",
            "start_date": start_date,
            "end_date": end_date,
            "timezone": "America/New_York",
        },
    )
    assert response.status_code == 201, response.text
    trip = response.json()
    trip_id = trip["id"]
    first_day, destination_day = trip["days"]
    created = api_client.post(
        f"/v1/trips/{trip_id}/days/{first_day['id']}/items",
        json={"title": "Timed item", "start_time": wall_time, "end_time": "03:00"},
        headers=revision_header(0),
    )
    assert created.status_code == 201, created.text
    before = created.json()
    item_id = before["days"][0]["items"][0]["id"]

    rejected = api_client.post(
        f"/v1/trips/{trip_id}/items/{item_id}/move",
        json={"destination_day_id": destination_day["id"], "position": 0},
        headers=revision_header(1),
    )
    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == error_code
    after = api_client.get(f"/v1/trips/{trip_id}").json()
    assert after["revision"] == before["revision"] == 1
    assert after["days"][0]["items"] == before["days"][0]["items"]
    assert after["days"][1]["items"] == []


def test_database_rejects_negative_revisions(database_engine: Engine) -> None:
    with database_engine.begin() as connection:
        trip_id = connection.scalar(text("SELECT id FROM trips LIMIT 1"))
        assert trip_id is None
    with pytest.raises(IntegrityError), database_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO trips (id,owner_id,title,start_date,end_date,timezone,revision) "
                "VALUES ('8cd39b43-51e8-4893-a37b-56f8269c2891','local','Invalid',"
                "'2026-01-01','2026-01-01','UTC',-1)"
            )
        )
