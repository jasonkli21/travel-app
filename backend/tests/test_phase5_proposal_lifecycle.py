"""PostgreSQL lifecycle and atomic-apply checks for itinerary proposals."""

import asyncio
import json
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from os import environ
from threading import Barrier, Event
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import personal_travel.services.proposals as proposal_service_module
from personal_travel.api.dependencies import session_dependency
from personal_travel.clients.personal_ai import PersonalAIClient, PersonalAIProposalUnknown
from personal_travel.config import Settings, get_settings
from personal_travel.domain.proposals import ProposalSetItemTimes
from personal_travel.domain.upstream_proposals import TravelItineraryContext, UpstreamProposalResult
from personal_travel.main import app
from personal_travel.models.proposal import ItineraryProposal
from personal_travel.services.proposals import ProposalService

TEST_DATABASE_URL = environ.get("TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.usefixtures("clean_database"),
    pytest.mark.skipif(TEST_DATABASE_URL is None, reason="requires disposable PostgreSQL"),
]


@pytest.fixture(autouse=True)
def enable_proposals() -> Iterator[None]:
    app.dependency_overrides[get_settings] = lambda: Settings(
        personal_ai_proposals_enabled=True,
        personal_ai_proposal_timeout_seconds=4,
    )
    yield
    app.dependency_overrides.pop(get_settings, None)


@pytest.fixture
def proposal_client(database_engine: Engine) -> Iterator[TestClient]:
    def override_session() -> Iterator[Session]:
        with Session(database_engine, autoflush=False, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[session_dependency] = override_session
    try:
        with TestClient(app, base_url="http://localhost") as client:
            yield client
    finally:
        app.dependency_overrides.pop(session_dependency, None)


def create_trip_with_item(client: TestClient) -> tuple[dict[str, object], str, str]:
    response = client.post(
        "/v1/trips",
        json={
            "title": "Proposal integration",
            "start_date": "2026-10-03",
            "end_date": "2026-10-03",
            "timezone": "UTC",
        },
    )
    assert response.status_code == 201, response.text
    trip = response.json()
    place_response = client.post("/v1/places", json={"name": "Museum building"})
    assert place_response.status_code == 201, place_response.text
    place_id = place_response.json()["id"]
    item_response = client.post(
        f"/v1/trips/{trip['id']}/days/{trip['days'][0]['id']}/items",
        headers={"X-Expected-Revision": str(trip["revision"])},
        json={
            "item_type": "activity",
            "title": "Museum",
            "notes": "PRIVATE itinerary note",
            "start_time": "09:00",
            "end_time": "10:00",
            "status": "planned",
            "place_id": place_id,
        },
    )
    assert item_response.status_code == 201, item_response.text
    next_trip = item_response.json()
    return next_trip, next_trip["days"][0]["items"][0]["id"], place_id


def fake_time_change(start_time: str | None = "09:30"):
    async def create(
        _client: PersonalAIClient,
        *,
        payload: dict[str, object],
        idempotency_key: UUID,
    ) -> UpstreamProposalResult:
        assert payload["schema_version"] == "itinerary-proposal-v1"
        assert payload["idempotency_key"] == str(idempotency_key)
        assert "PRIVATE itinerary note" not in str(payload)
        context = TravelItineraryContext.model_validate_json(json.dumps(payload["context"]))
        now = datetime.now(UTC)
        return UpstreamProposalResult(
            proposal_id=uuid4(),
            state="proposed",
            support_mode="context_only",
            trip_handle=context.trip_handle,
            operations=(
                ProposalSetItemTimes(
                    kind="set_item_times",
                    item_handle=context.days[0].items[0].handle,
                    start_time=start_time,
                ),
            ),
            operation_support=(),
            citations=(),
            created_at=now,
            expires_at=now + timedelta(hours=1),
        )

    return create


def request_proposal(client: TestClient, trip: dict[str, object], key: UUID) -> object:
    return client.post(
        f"/v1/trips/{trip['id']}/proposals",
        headers={"X-Expected-Revision": str(trip["revision"])},
        json={"idempotency_key": str(key), "instruction": "Move this visit slightly later"},
    )


def test_generation_preview_apply_and_exact_outcome_replay(
    proposal_client: TestClient,
    database_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trip, item_id, _place_id = create_trip_with_item(proposal_client)

    # Handles are request-scoped, so return an operation targeting the context
    # item without observing any authoritative database IDs upstream.
    monkeypatch.setattr(PersonalAIClient, "create_itinerary_proposal", fake_time_change())
    key = uuid4()
    response = request_proposal(proposal_client, trip, key)
    assert response.status_code == 201, response.text
    proposal = response.json()
    assert proposal["state"] == "ready"
    assert proposal["support_mode"] == "context_only"
    assert proposal["preview"]["after"][0]["items"][0]["start_time"] == "09:30"
    assert proposal["preview"]["after"][0]["items"][0]["end_time"] == "10:00"
    assert "PRIVATE itinerary note" not in response.text

    mismatch = proposal_client.post(
        f"/v1/trips/{trip['id']}/proposals",
        headers={"X-Expected-Revision": str(trip["revision"])},
        json={"idempotency_key": str(key), "instruction": "A different request"},
    )
    assert mismatch.status_code == 409
    assert mismatch.json()["error"]["code"] == "idempotency_key_reused"

    apply_url = f"/v1/trips/{trip['id']}/proposals/{proposal['proposal_id']}/apply"
    applied = proposal_client.post(
        apply_url, headers={"X-Expected-Revision": str(trip["revision"])}
    )
    assert applied.status_code == 200, applied.text
    outcome = applied.json()
    assert outcome["state"] == "applied"
    assert outcome["applied_revision"] == trip["revision"] + 1

    replay = proposal_client.post(apply_url, headers={"X-Expected-Revision": str(trip["revision"])})
    assert replay.status_code == 200
    assert replay.json() == outcome

    refreshed = proposal_client.get(f"/v1/trips/{trip['id']}").json()
    item = refreshed["days"][0]["items"][0]
    assert item["id"] == item_id
    assert item["start_time"] == "09:30"
    assert item["end_time"] == "10:00"
    with Session(database_engine) as session:
        row = session.scalar(
            select(ItineraryProposal).where(ItineraryProposal.id == UUID(proposal["proposal_id"]))
        )
        assert row is not None
        assert row.state == "applied"
        assert "instruction" not in row.base_snapshot
        assert row.applied_outcome is not None
        assert row.applied_outcome["applied_revision"] == outcome["applied_revision"]
        assert row.applied_outcome["preview"] == outcome["preview"]


def test_explicit_null_clears_only_the_present_time_field(
    proposal_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trip, _item_id, _place_id = create_trip_with_item(proposal_client)
    monkeypatch.setattr(PersonalAIClient, "create_itinerary_proposal", fake_time_change(None))
    response = request_proposal(proposal_client, trip, uuid4())
    assert response.status_code == 201, response.text
    proposal = response.json()
    assert proposal["preview"]["after"][0]["items"][0]["start_time"] is None
    assert proposal["preview"]["after"][0]["items"][0]["end_time"] == "10:00"


def test_shared_place_footprint_stales_only_dependent_proposals(
    proposal_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trip, _item_id, place_id = create_trip_with_item(proposal_client)
    unrelated_place = proposal_client.post("/v1/places", json={"name": "Unrelated place"}).json()
    monkeypatch.setattr(PersonalAIClient, "create_itinerary_proposal", fake_time_change())
    created = request_proposal(proposal_client, trip, uuid4())
    assert created.status_code == 201, created.text
    detail = created.json()
    assert detail["state"] == "ready"
    assert detail["base_place_revisions"] == [{"place_id": place_id, "revision": 0}]

    unrelated_update = proposal_client.patch(
        f"/v1/places/{unrelated_place['id']}",
        headers={"X-Expected-Revision": str(unrelated_place["revision"])},
        json={"name": "Unrelated place edited"},
    )
    assert unrelated_update.status_code == 200, unrelated_update.text
    still_ready = proposal_client.get(f"/v1/trips/{trip['id']}/proposals/{detail['proposal_id']}")
    assert still_ready.json()["state"] == "ready"

    dependent_place = proposal_client.patch(
        f"/v1/places/{place_id}",
        headers={"X-Expected-Revision": "0"},
        json={"name": "Museum building edited"},
    )
    assert dependent_place.status_code == 200, dependent_place.text
    stale = proposal_client.get(f"/v1/trips/{trip['id']}/proposals/{detail['proposal_id']}")
    assert stale.json()["state"] == "stale"
    assert stale.json()["current_trip_revision"] == trip["revision"]
    assert stale.json()["current_place_revisions"] == [{"place_id": place_id, "revision": 1}]

    rejected_apply = proposal_client.post(
        f"/v1/trips/{trip['id']}/proposals/{detail['proposal_id']}/apply",
        headers={"X-Expected-Revision": str(trip["revision"])},
    )
    assert rejected_apply.status_code == 409
    assert rejected_apply.json()["error"]["code"] == "stale_proposal"


def test_manual_itinerary_edit_stales_preview_before_apply(
    proposal_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trip, item_id, _place_id = create_trip_with_item(proposal_client)
    monkeypatch.setattr(PersonalAIClient, "create_itinerary_proposal", fake_time_change())
    created = request_proposal(proposal_client, trip, uuid4())
    proposal_id = created.json()["proposal_id"]

    edited = proposal_client.patch(
        f"/v1/trips/{trip['id']}/items/{item_id}",
        headers={"X-Expected-Revision": str(trip["revision"])},
        json={"start_time": "09:15"},
    )
    assert edited.status_code == 200, edited.text
    current_trip = edited.json()
    detail = proposal_client.get(f"/v1/trips/{trip['id']}/proposals/{proposal_id}")
    assert detail.json()["state"] == "stale"
    assert detail.json()["current_trip_revision"] == current_trip["revision"]

    stale_apply = proposal_client.post(
        f"/v1/trips/{trip['id']}/proposals/{proposal_id}/apply",
        headers={"X-Expected-Revision": str(current_trip["revision"])},
    )
    assert stale_apply.status_code == 409
    assert stale_apply.json()["error"]["code"] == "stale_proposal"
    refreshed = proposal_client.get(f"/v1/trips/{trip['id']}").json()
    assert refreshed["days"][0]["items"][0]["start_time"] == "09:15"
    assert refreshed["revision"] == current_trip["revision"]


def test_rejection_is_idempotent_and_terminal(
    proposal_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trip, _item_id, _place_id = create_trip_with_item(proposal_client)
    monkeypatch.setattr(PersonalAIClient, "create_itinerary_proposal", fake_time_change())
    created = request_proposal(proposal_client, trip, uuid4())
    proposal_id = created.json()["proposal_id"]
    reject_url = f"/v1/trips/{trip['id']}/proposals/{proposal_id}/reject"

    first = proposal_client.post(reject_url)
    second = proposal_client.post(reject_url)
    assert first.status_code == second.status_code == 200
    assert first.json()["state"] == second.json()["state"] == "rejected"

    apply = proposal_client.post(
        f"/v1/trips/{trip['id']}/proposals/{proposal_id}/apply",
        headers={"X-Expected-Revision": str(trip["revision"])},
    )
    assert apply.status_code == 409
    assert apply.json()["error"]["code"] == "proposal_rejected"
    refreshed = proposal_client.get(f"/v1/trips/{trip['id']}").json()
    assert refreshed["revision"] == trip["revision"]


def test_sql_failure_after_mutation_rolls_back_items_revision_and_proposal(
    proposal_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trip, item_id, _place_id = create_trip_with_item(proposal_client)
    monkeypatch.setattr(PersonalAIClient, "create_itinerary_proposal", fake_time_change())
    created = request_proposal(proposal_client, trip, uuid4())
    proposal_id = created.json()["proposal_id"]
    apply_url = f"/v1/trips/{trip['id']}/proposals/{proposal_id}/apply"
    original_apply = proposal_service_module._apply_operations

    def mutate_then_fail(*args: object, **kwargs: object) -> None:
        original_apply(*args, **kwargs)
        raise RuntimeError("injected SQL apply failure")

    with monkeypatch.context() as scoped:
        scoped.setattr(proposal_service_module, "_apply_operations", mutate_then_fail)
        failed = proposal_client.post(
            apply_url, headers={"X-Expected-Revision": str(trip["revision"])}
        )
    assert failed.status_code == 500

    unchanged = proposal_client.get(f"/v1/trips/{trip['id']}").json()
    assert unchanged["revision"] == trip["revision"]
    assert unchanged["days"][0]["items"][0]["id"] == item_id
    assert unchanged["days"][0]["items"][0]["start_time"] == "09:00"
    still_ready = proposal_client.get(f"/v1/trips/{trip['id']}/proposals/{proposal_id}")
    assert still_ready.json()["lifecycle_state"] == "ready"

    recovered = proposal_client.post(
        apply_url, headers={"X-Expected-Revision": str(trip["revision"])}
    )
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["applied_revision"] == trip["revision"] + 1


def test_same_key_concurrent_proposal_reserves_once_and_returns_busy(
    proposal_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trip, _item_id, _place_id = create_trip_with_item(proposal_client)
    request_started = Event()
    release = Event()
    calls = 0

    async def slow_create(
        _client: PersonalAIClient,
        *,
        payload: dict[str, object],
        idempotency_key: UUID,
    ) -> UpstreamProposalResult:
        nonlocal calls
        calls += 1
        request_started.set()
        if not await asyncio.to_thread(release.wait, 3):
            raise TimeoutError("test release was not signaled")
        context = TravelItineraryContext.model_validate_json(json.dumps(payload["context"]))
        now = datetime.now(UTC)
        return UpstreamProposalResult(
            proposal_id=uuid4(),
            state="proposed",
            support_mode="context_only",
            trip_handle=context.trip_handle,
            operations=(
                ProposalSetItemTimes(
                    kind="set_item_times",
                    item_handle=context.days[0].items[0].handle,
                    start_time="09:30",
                ),
            ),
            operation_support=(),
            citations=(),
            created_at=now,
            expires_at=now + timedelta(hours=1),
        )

    monkeypatch.setattr(PersonalAIClient, "create_itinerary_proposal", slow_create)
    key = uuid4()
    with ThreadPoolExecutor(max_workers=1) as pool:
        first_future = pool.submit(request_proposal, proposal_client, trip, key)
        assert request_started.wait(2)
        second = request_proposal(proposal_client, trip, key)
        release.set()
        first = first_future.result(timeout=5)

    assert first.status_code == 201, first.text
    assert second.status_code == 409, second.text
    assert second.json()["error"]["code"] == "proposal_busy"
    assert calls == 1


def test_two_concurrent_apply_requests_store_one_revision_and_same_outcome(
    proposal_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trip, _item_id, _place_id = create_trip_with_item(proposal_client)
    monkeypatch.setattr(PersonalAIClient, "create_itinerary_proposal", fake_time_change())
    created = request_proposal(proposal_client, trip, uuid4())
    proposal_id = created.json()["proposal_id"]
    apply_url = f"/v1/trips/{trip['id']}/proposals/{proposal_id}/apply"
    barrier = Barrier(2)
    original_apply = ProposalService._apply_tx

    def synchronized_apply(
        service: ProposalService, trip_id: UUID, proposal_id: UUID, expected_revision: int | None
    ):
        barrier.wait(timeout=3)
        return original_apply(service, trip_id, proposal_id, expected_revision)

    with monkeypatch.context() as scoped:
        scoped.setattr(ProposalService, "_apply_tx", synchronized_apply)
        with ThreadPoolExecutor(max_workers=2) as pool:
            requests = [
                pool.submit(
                    proposal_client.post,
                    apply_url,
                    headers={"X-Expected-Revision": str(trip["revision"])},
                )
                for _ in range(2)
            ]
            first, second = [future.result(timeout=8) for future in requests]

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    refreshed = proposal_client.get(f"/v1/trips/{trip['id']}").json()
    assert refreshed["revision"] == trip["revision"] + 1


def test_two_distinct_concurrent_proposals_share_a_base_and_only_one_can_apply(
    proposal_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trip, _item_id, _place_id = create_trip_with_item(proposal_client)
    barrier = Barrier(2)
    fake_create = fake_time_change()

    async def synchronized_create(
        client: PersonalAIClient,
        *,
        payload: dict[str, object],
        idempotency_key: UUID,
    ) -> UpstreamProposalResult:
        await asyncio.to_thread(barrier.wait, 3)
        return await fake_create(client, payload=payload, idempotency_key=idempotency_key)

    monkeypatch.setattr(PersonalAIClient, "create_itinerary_proposal", synchronized_create)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(request_proposal, proposal_client, trip, uuid4()) for _ in range(2)]
        first, second = [future.result(timeout=8) for future in futures]

    assert first.status_code == second.status_code == 201
    first_proposal, second_proposal = first.json(), second.json()
    assert first_proposal["proposal_id"] != second_proposal["proposal_id"]
    assert first_proposal["state"] == second_proposal["state"] == "ready"
    assert first_proposal["base_trip_revision"] == second_proposal["base_trip_revision"]

    first_applied = proposal_client.post(
        f"/v1/trips/{trip['id']}/proposals/{first_proposal['proposal_id']}/apply",
        headers={"X-Expected-Revision": str(trip["revision"])},
    )
    assert first_applied.status_code == 200, first_applied.text
    stale_second = proposal_client.get(
        f"/v1/trips/{trip['id']}/proposals/{second_proposal['proposal_id']}"
    )
    assert stale_second.json()["state"] == "stale"
    second_apply = proposal_client.post(
        f"/v1/trips/{trip['id']}/proposals/{second_proposal['proposal_id']}/apply",
        headers={"X-Expected-Revision": str(trip["revision"] + 1)},
    )
    assert second_apply.status_code == 409
    assert second_apply.json()["error"]["code"] == "stale_proposal"
    refreshed = proposal_client.get(f"/v1/trips/{trip['id']}").json()
    assert refreshed["revision"] == trip["revision"] + 1


def test_apply_and_reject_race_has_one_terminal_winner(
    proposal_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trip, _item_id, _place_id = create_trip_with_item(proposal_client)
    monkeypatch.setattr(PersonalAIClient, "create_itinerary_proposal", fake_time_change())
    created = request_proposal(proposal_client, trip, uuid4())
    proposal_id = created.json()["proposal_id"]
    apply_url = f"/v1/trips/{trip['id']}/proposals/{proposal_id}/apply"
    reject_url = f"/v1/trips/{trip['id']}/proposals/{proposal_id}/reject"
    barrier = Barrier(2)
    original_apply = ProposalService._apply_tx
    original_reject = ProposalService._reject_tx

    def synchronized_apply(
        service: ProposalService, trip_id: UUID, value_id: UUID, expected_revision: int | None
    ):
        barrier.wait(timeout=3)
        return original_apply(service, trip_id, value_id, expected_revision)

    def synchronized_reject(service: ProposalService, trip_id: UUID, value_id: UUID) -> None:
        barrier.wait(timeout=3)
        return original_reject(service, trip_id, value_id)

    with monkeypatch.context() as scoped:
        scoped.setattr(ProposalService, "_apply_tx", synchronized_apply)
        scoped.setattr(ProposalService, "_reject_tx", synchronized_reject)
        with ThreadPoolExecutor(max_workers=2) as pool:
            apply_future = pool.submit(
                proposal_client.post,
                apply_url,
                headers={"X-Expected-Revision": str(trip["revision"])},
            )
            reject_future = pool.submit(proposal_client.post, reject_url)
            applied = apply_future.result(timeout=8)
            rejected = reject_future.result(timeout=8)

    assert sorted((applied.status_code, rejected.status_code)) == [200, 409]
    state = proposal_client.get(f"/v1/trips/{trip['id']}/proposals/{proposal_id}").json()
    if applied.status_code == 200:
        assert state["lifecycle_state"] == "applied"
        assert state["applied_outcome"] == applied.json()
        assert rejected.json()["error"]["code"] == "proposal_already_applied"
        revision = trip["revision"] + 1
    else:
        assert state["lifecycle_state"] == "rejected"
        assert applied.json()["error"]["code"] == "proposal_rejected"
        revision = trip["revision"]
    refreshed = proposal_client.get(f"/v1/trips/{trip['id']}").json()
    assert refreshed["revision"] == revision


def test_unknown_generation_reconciles_by_same_key_without_a_second_post(
    proposal_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trip, _item_id, _place_id = create_trip_with_item(proposal_client)
    post_calls = 0
    lookup_keys: list[UUID] = []
    remote_result: UpstreamProposalResult | None = None

    async def ambiguous_create(
        _client: PersonalAIClient,
        *,
        payload: dict[str, object],
        idempotency_key: UUID,
    ) -> UpstreamProposalResult:
        nonlocal post_calls, remote_result
        post_calls += 1
        assert payload["idempotency_key"] == str(idempotency_key)
        context = TravelItineraryContext.model_validate_json(json.dumps(payload["context"]))
        now = datetime.now(UTC)
        remote_result = UpstreamProposalResult(
            proposal_id=uuid4(),
            state="proposed",
            support_mode="context_only",
            trip_handle=context.trip_handle,
            operations=(
                ProposalSetItemTimes(
                    kind="set_item_times",
                    item_handle=context.days[0].items[0].handle,
                    start_time="09:30",
                ),
            ),
            operation_support=(),
            citations=(),
            created_at=now,
            expires_at=now + timedelta(hours=1),
        )
        raise PersonalAIProposalUnknown("response was ambiguous")

    async def lookup(_client: PersonalAIClient, key: UUID) -> UpstreamProposalResult | None:
        lookup_keys.append(key)
        return remote_result if len(lookup_keys) > 1 else None

    monkeypatch.setattr(PersonalAIClient, "create_itinerary_proposal", ambiguous_create)
    monkeypatch.setattr(PersonalAIClient, "get_itinerary_proposal_by_key", lookup)
    key = uuid4()
    created = request_proposal(proposal_client, trip, key)
    assert created.status_code == 201, created.text
    assert created.json()["state"] == "outcome_unknown"

    reconciled = proposal_client.get(f"/v1/trips/{trip['id']}/proposals/by-key/{key}")
    assert reconciled.status_code == 200, reconciled.text
    assert reconciled.json()["state"] == "ready"
    assert post_calls == 1
    assert len(lookup_keys) == 2
    assert lookup_keys[0] == lookup_keys[1]
