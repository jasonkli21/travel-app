"""PostgreSQL lifecycle and atomic-apply checks for itinerary proposals."""

import asyncio
import json
import logging
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from os import environ
from threading import Barrier, Event, Lock
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import personal_travel.services.proposals as proposal_service_module
from personal_travel.api.dependencies import session_dependency
from personal_travel.clients.personal_ai import PersonalAIClient, PersonalAIProposalUnknown
from personal_travel.config import Settings, get_settings
from personal_travel.domain.proposals import (
    ProposalAddItem,
    ProposalMoveItem,
    ProposalRemoveItem,
    ProposalSetItemTimes,
)
from personal_travel.domain.upstream_proposals import (
    UPSTREAM_REVISION,
    OperationEvidenceSupport,
    ProposalCitation,
    TravelItineraryContext,
    UpstreamProposalResult,
)
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


def request_proposal(
    client: TestClient,
    trip: dict[str, object],
    key: UUID,
    *,
    removable_item_ids: tuple[str, ...] = (),
    research_session_ids: tuple[str, ...] = (),
) -> object:
    return client.post(
        f"/v1/trips/{trip['id']}/proposals",
        headers={"X-Expected-Revision": str(trip["revision"])},
        json={
            "idempotency_key": str(key),
            "instruction": "Move this visit slightly later",
            "removable_item_ids": list(removable_item_ids),
            "research_session_ids": list(research_session_ids),
        },
    )


def test_detail_retains_operation_evidence_support_and_upstream_revision(
    proposal_client: TestClient,
    database_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trip, _item_id, _place_id = create_trip_with_item(proposal_client)
    evidence_handle = "e_evidencehandle0001"

    async def supported_create(
        _client: PersonalAIClient,
        *,
        payload: dict[str, object],
        idempotency_key: UUID,
    ) -> UpstreamProposalResult:
        assert payload["idempotency_key"] == str(idempotency_key)
        context = TravelItineraryContext.model_validate_json(json.dumps(payload["context"]))
        now = datetime.now(UTC)
        return UpstreamProposalResult(
            proposal_id=uuid4(),
            state="proposed",
            support_mode="research_evidence",
            trip_handle=context.trip_handle,
            operations=(
                ProposalSetItemTimes(
                    kind="set_item_times",
                    item_handle=context.days[0].items[0].handle,
                    start_time="09:30",
                ),
            ),
            operation_support=(
                OperationEvidenceSupport(
                    operation_index=0,
                    evidence_handles=(evidence_handle,),
                ),
            ),
            citations=(
                ProposalCitation(
                    evidence_handle=evidence_handle,
                    url="https://example.test/travel-evidence",
                    title="Travel evidence",
                    observed_at=now,
                    expires_at=now + timedelta(hours=1),
                ),
            ),
            created_at=now,
            expires_at=now + timedelta(hours=1),
        )

    monkeypatch.setattr(PersonalAIClient, "create_itinerary_proposal", supported_create)
    created = request_proposal(
        proposal_client,
        trip,
        uuid4(),
        research_session_ids=(str(uuid4()),),
    )
    assert created.status_code == 201, created.text
    proposal = created.json()
    expected_support = [{"operation_index": 0, "evidence_handles": [evidence_handle]}]
    assert proposal["upstream_revision"] == UPSTREAM_REVISION
    assert proposal["operation_support"] == expected_support

    detail = proposal_client.get(f"/v1/trips/{trip['id']}/proposals/{proposal['proposal_id']}")
    assert detail.status_code == 200
    assert detail.json()["upstream_revision"] == UPSTREAM_REVISION
    assert detail.json()["operation_support"] == expected_support
    with Session(database_engine) as session:
        row = session.scalar(
            select(ItineraryProposal).where(ItineraryProposal.id == UUID(proposal["proposal_id"]))
        )
        assert row is not None
        assert row.upstream_revision == UPSTREAM_REVISION
        assert row.operation_support == expected_support


def test_invalid_upstream_payload_is_safe_and_does_not_change_trip(
    proposal_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    trip, _item_id, _place_id = create_trip_with_item(proposal_client)
    before = proposal_client.get(f"/v1/trips/{trip['id']}").json()
    requests: list[tuple[str, str]] = []
    private_upstream_detail = "PRIVATE UPSTREAM RESPONSE BODY"

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(404, request=request)
        now = datetime.now(UTC)
        payload = {
            "schema_version": "itinerary-proposal-v1",
            "proposal_id": str(uuid4()),
            "state": "proposed",
            "policy_version": "itinerary-proposal-policy-v2",
            "support_mode": "context_only",
            "trip_handle": "h_triphandle000000001",
            "operations": [],
            "operation_support": [],
            "citations": [],
            "failure_code": None,
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(hours=1)).isoformat(),
        }
        payload["unrecognized"] = private_upstream_detail
        return httpx.Response(200, json=payload, request=request)

    transport = httpx.MockTransport(handle)
    original_init = ProposalService.__init__

    def init_with_fake_http(
        service: ProposalService,
        session: Session,
        owner_id: str,
        settings: Settings,
        client: PersonalAIClient | None = None,
        *,
        clock=None,
    ) -> None:
        if client is None:
            client = PersonalAIClient(
                base_url="http://personal-ai.test", timeout_seconds=0.1, transport=transport
            )
        original_init(service, session, owner_id, settings, client, clock=clock)

    monkeypatch.setattr(ProposalService, "__init__", init_with_fake_http)
    with caplog.at_level(logging.DEBUG):
        response = request_proposal(proposal_client, trip, uuid4())

    assert response.status_code == 201, response.text
    assert response.json()["state"] == "outcome_unknown"
    assert private_upstream_detail not in response.text
    assert private_upstream_detail not in caplog.text
    assert [method for method, _ in requests].count("POST") == 1
    assert [method for method, _ in requests].count("GET") == 1
    after = proposal_client.get(f"/v1/trips/{trip['id']}").json()
    assert after["revision"] == before["revision"]
    assert after["days"] == before["days"]


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
    assert proposal["operations"][0]["start_time"] == "09:30"
    assert "end_time" not in proposal["operations"][0]
    assert "PRIVATE itinerary note" not in response.text

    detail_urls = (
        f"/v1/trips/{trip['id']}/proposals/{proposal['proposal_id']}",
        f"/v1/trips/{trip['id']}/proposals/by-key/{key}",
    )
    for url in detail_urls:
        detail = proposal_client.get(url)
        assert detail.status_code == 200
        operation = detail.json()["operations"][0]
        assert operation["start_time"] == "09:30"
        assert "end_time" not in operation

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
    with Session(database_engine) as session:
        row = session.scalar(
            select(ItineraryProposal).where(ItineraryProposal.id == UUID(proposal["proposal_id"]))
        )
        assert row is not None
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()
    expired_replay = proposal_client.post(
        apply_url, headers={"X-Expected-Revision": str(trip["revision"])}
    )
    assert expired_replay.status_code == 200
    assert expired_replay.json() == outcome

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
    key = uuid4()
    response = request_proposal(proposal_client, trip, key)
    assert response.status_code == 201, response.text
    proposal = response.json()
    assert proposal["preview"]["after"][0]["items"][0]["start_time"] is None
    assert proposal["preview"]["after"][0]["items"][0]["end_time"] == "10:00"
    assert proposal["operations"][0]["start_time"] is None
    assert "end_time" not in proposal["operations"][0]
    detail_urls = (
        f"/v1/trips/{trip['id']}/proposals/{proposal['proposal_id']}",
        f"/v1/trips/{trip['id']}/proposals/by-key/{key}",
    )
    for url in detail_urls:
        detail = proposal_client.get(url)
        assert detail.status_code == 200
        operation = detail.json()["operations"][0]
        assert operation["start_time"] is None
        assert "end_time" not in operation


def test_sql_apply_matches_full_preview_for_ordered_multi_operation_batch(
    proposal_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created_trip = proposal_client.post(
        "/v1/trips",
        json={
            "title": "Ordered proposal batch",
            "start_date": "2026-10-03",
            "end_date": "2026-10-04",
            "timezone": "UTC",
        },
    )
    assert created_trip.status_code == 201, created_trip.text
    trip = created_trip.json()
    first_day, second_day = trip["days"]
    item_ids: dict[str, str] = {}
    item_values = [
        ("First visit", first_day["id"], "09:00", "10:00"),
        ("Second visit", first_day["id"], "11:00", "12:00"),
        ("Selected removal", first_day["id"], "13:00", "14:00"),
        ("Destination visit", second_day["id"], "10:00", "11:00"),
    ]
    for title, day_id, start_time, end_time in item_values:
        response = proposal_client.post(
            f"/v1/trips/{trip['id']}/days/{day_id}/items",
            headers={"X-Expected-Revision": str(trip["revision"])},
            json={
                "item_type": "activity",
                "title": title,
                "start_time": start_time,
                "end_time": end_time,
                "status": "planned",
            },
        )
        assert response.status_code == 201, response.text
        trip = response.json()
        item_ids[title] = next(
            item["id"] for day in trip["days"] for item in day["items"] if item["title"] == title
        )

    place = proposal_client.post("/v1/places", json={"name": "Candidate gallery"})
    assert place.status_code == 201, place.text
    candidate = proposal_client.post(
        f"/v1/trips/{trip['id']}/saved-places",
        headers={"X-Expected-Revision": str(trip["revision"])},
        json={"place_id": place.json()["id"]},
    )
    assert candidate.status_code == 201, candidate.text
    trip = proposal_client.get(f"/v1/trips/{trip['id']}").json()

    async def multi_operation_create(
        _client: PersonalAIClient,
        *,
        payload: dict[str, object],
        idempotency_key: UUID,
    ) -> UpstreamProposalResult:
        assert payload["idempotency_key"] == str(idempotency_key)
        context = TravelItineraryContext.model_validate_json(json.dumps(payload["context"]))
        days = context.days
        handles = {item.label: item.handle for day in context.days for item in day.items}
        candidate_handle = context.candidates[0].handle
        first = handles["First visit"]
        now = datetime.now(UTC)
        return UpstreamProposalResult(
            proposal_id=uuid4(),
            state="proposed",
            support_mode="context_only",
            trip_handle=context.trip_handle,
            operations=(
                ProposalMoveItem(
                    kind="move_item", item_handle=first, day_handle=days[1].handle, position=1
                ),
                ProposalSetItemTimes(kind="set_item_times", item_handle=first, start_time="08:30"),
                ProposalMoveItem(
                    kind="move_item", item_handle=first, day_handle=days[1].handle, position=0
                ),
                ProposalSetItemTimes(kind="set_item_times", item_handle=first, start_time="09:15"),
                ProposalAddItem(
                    kind="add_item",
                    day_handle=days[1].handle,
                    candidate_handle=candidate_handle,
                    item_type="activity",
                    position=2,
                    start_time="14:00",
                    end_time="15:00",
                ),
                ProposalRemoveItem(kind="remove_item", item_handle=handles["Selected removal"]),
                ProposalSetItemTimes(
                    kind="set_item_times",
                    item_handle=handles["Second visit"],
                    end_time=None,
                ),
            ),
            operation_support=(),
            citations=(),
            created_at=now,
            expires_at=now + timedelta(hours=1),
        )

    monkeypatch.setattr(PersonalAIClient, "create_itinerary_proposal", multi_operation_create)
    created = request_proposal(
        proposal_client,
        trip,
        uuid4(),
        removable_item_ids=(item_ids["Selected removal"],),
    )
    assert created.status_code == 201, created.text
    proposal = created.json()
    assert proposal["state"] == "ready"
    assert proposal["preview"]["operation_count"] == 7

    applied = proposal_client.post(
        f"/v1/trips/{trip['id']}/proposals/{proposal['proposal_id']}/apply",
        headers={"X-Expected-Revision": str(trip["revision"])},
    )
    assert applied.status_code == 200, applied.text
    actual_trip = proposal_client.get(f"/v1/trips/{trip['id']}").json()
    expected_days = proposal["preview"]["after"]
    assert len(actual_trip["days"]) == len(expected_days)
    for actual_day, expected_day in zip(actual_trip["days"], expected_days, strict=True):
        assert actual_day["day_index"] == expected_day["day_index"]
        assert actual_day["date"] == expected_day["date"]
        actual_items = [
            {
                "title": item["title"],
                "item_type": item["item_type"],
                "status": item["status"],
                "sort_order": item["sort_order"],
                "start_time": item["start_time"],
                "end_time": item["end_time"],
            }
            for item in actual_day["items"]
        ]
        expected_items = [
            {
                "title": item["title"],
                "item_type": item["item_type"],
                "status": item["status"],
                "sort_order": item["sort_order"],
                "start_time": item["start_time"],
                "end_time": item["end_time"],
            }
            for item in expected_day["items"]
        ]
        assert actual_items == expected_items


def test_expired_ready_proposal_cannot_apply(
    proposal_client: TestClient,
    database_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trip, _item_id, _place_id = create_trip_with_item(proposal_client)
    monkeypatch.setattr(PersonalAIClient, "create_itinerary_proposal", fake_time_change())
    created = request_proposal(proposal_client, trip, uuid4())
    assert created.status_code == 201, created.text
    detail = created.json()
    proposal_id = UUID(detail["proposal_id"])

    with Session(database_engine) as session:
        row = session.scalar(select(ItineraryProposal).where(ItineraryProposal.id == proposal_id))
        assert row is not None
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()

    get_url = f"/v1/trips/{trip['id']}/proposals/{proposal_id}"
    expired = proposal_client.get(get_url)
    assert expired.status_code == 200
    assert expired.json()["state"] == "expired"
    assert expired.json()["lifecycle_state"] == "ready"

    apply = proposal_client.post(
        f"{get_url}/apply", headers={"X-Expected-Revision": str(trip["revision"])}
    )
    assert apply.status_code == 409
    assert apply.json()["error"]["code"] == "proposal_expired"
    unchanged = proposal_client.get(f"/v1/trips/{trip['id']}").json()
    assert unchanged["revision"] == trip["revision"]


def test_expiry_is_checked_after_place_lock_wait_for_apply_and_detail(
    proposal_client: TestClient,
    database_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trip, _item_id, _place_id = create_trip_with_item(proposal_client)
    monkeypatch.setattr(PersonalAIClient, "create_itinerary_proposal", fake_time_change())
    created = request_proposal(proposal_client, trip, uuid4())
    assert created.status_code == 201, created.text
    proposal = created.json()
    proposal_id = UUID(proposal["proposal_id"])
    expires_at = datetime.now(UTC) + timedelta(seconds=5)
    with Session(database_engine) as session:
        row = session.scalar(select(ItineraryProposal).where(ItineraryProposal.id == proposal_id))
        assert row is not None
        row.expires_at = expires_at
        session.commit()

    current_time = [expires_at - timedelta(milliseconds=1)]
    monkeypatch.setattr(ProposalService, "_now", lambda _service: current_time[0])
    original_current_revisions = proposal_service_module._current_place_revisions

    def finish_place_lock_wait(
        session: Session,
        owner_id: str,
        footprint: list[dict[str, object]],
        *,
        exclusive: bool,
    ):
        revisions = original_current_revisions(session, owner_id, footprint, exclusive=exclusive)
        current_time[0] = expires_at + timedelta(milliseconds=1)
        return revisions

    monkeypatch.setattr(proposal_service_module, "_current_place_revisions", finish_place_lock_wait)
    apply = proposal_client.post(
        f"/v1/trips/{trip['id']}/proposals/{proposal_id}/apply",
        headers={"X-Expected-Revision": str(trip["revision"])},
    )
    assert apply.status_code == 409
    assert apply.json()["error"]["code"] == "proposal_expired"
    unchanged = proposal_client.get(f"/v1/trips/{trip['id']}").json()
    assert unchanged["revision"] == trip["revision"]
    assert unchanged["days"][0]["items"][0]["start_time"] == "09:00"

    current_time[0] = expires_at - timedelta(milliseconds=1)
    detail = proposal_client.get(f"/v1/trips/{trip['id']}/proposals/{proposal_id}")
    assert detail.status_code == 200
    assert detail.json()["state"] == "expired"


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

    async def two_step_time_change(
        _client: PersonalAIClient,
        *,
        payload: dict[str, object],
        idempotency_key: UUID,
    ) -> UpstreamProposalResult:
        assert payload["idempotency_key"] == str(idempotency_key)
        context = TravelItineraryContext.model_validate_json(json.dumps(payload["context"]))
        item_handle = context.days[0].items[0].handle
        now = datetime.now(UTC)
        return UpstreamProposalResult(
            proposal_id=uuid4(),
            state="proposed",
            support_mode="context_only",
            trip_handle=context.trip_handle,
            operations=(
                ProposalSetItemTimes(
                    kind="set_item_times", item_handle=item_handle, start_time="09:30"
                ),
                ProposalSetItemTimes(
                    kind="set_item_times", item_handle=item_handle, end_time="10:30"
                ),
            ),
            operation_support=(),
            citations=(),
            created_at=now,
            expires_at=now + timedelta(hours=1),
        )

    monkeypatch.setattr(PersonalAIClient, "create_itinerary_proposal", two_step_time_change)
    created = request_proposal(proposal_client, trip, uuid4())
    proposal_id = created.json()["proposal_id"]
    apply_url = f"/v1/trips/{trip['id']}/proposals/{proposal_id}/apply"
    original_time_conversion = proposal_service_module.local_datetime_for_item
    calls = 0

    def fail_on_later_operation(*args: object, **kwargs: object):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("injected later operation failure")
        return original_time_conversion(*args, **kwargs)

    with monkeypatch.context() as scoped:
        scoped.setattr(proposal_service_module, "local_datetime_for_item", fail_on_later_operation)
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


def test_two_trips_lock_shared_places_in_stable_order(
    proposal_client: TestClient,
    database_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    places: dict[str, str] = {}
    for name in ("Shared first", "Shared second"):
        response = proposal_client.post("/v1/places", json={"name": name})
        assert response.status_code == 201, response.text
        places[name] = response.json()["id"]

    trips: list[dict[str, object]] = []
    candidate_orders = [
        ("Shared first", "Shared second"),
        ("Shared second", "Shared first"),
    ]
    for index, order in enumerate(candidate_orders):
        response = proposal_client.post(
            "/v1/trips",
            json={
                "title": f"Shared place proposal {index + 1}",
                "start_date": "2026-10-03",
                "end_date": "2026-10-03",
                "timezone": "UTC",
            },
        )
        assert response.status_code == 201, response.text
        trip = response.json()
        response = proposal_client.post(
            f"/v1/trips/{trip['id']}/days/{trip['days'][0]['id']}/items",
            headers={"X-Expected-Revision": str(trip["revision"])},
            json={
                "item_type": "activity",
                "title": f"Trip {index + 1} visit",
                "start_time": "09:00",
                "end_time": "10:00",
                "status": "planned",
            },
        )
        assert response.status_code == 201, response.text
        trip = response.json()
        for name in order:
            saved = proposal_client.post(
                f"/v1/trips/{trip['id']}/saved-places",
                headers={"X-Expected-Revision": str(trip["revision"])},
                json={"place_id": places[name]},
            )
            assert saved.status_code == 201, saved.text
            trip = proposal_client.get(f"/v1/trips/{trip['id']}").json()
        trips.append(trip)

    monkeypatch.setattr(PersonalAIClient, "create_itinerary_proposal", fake_time_change())
    proposals = []
    for trip in trips:
        created = request_proposal(proposal_client, trip, uuid4())
        assert created.status_code == 201, created.text
        assert created.json()["state"] == "ready"
        proposals.append(created.json())

    barrier = Barrier(2)
    original_apply = ProposalService._apply_tx

    def synchronized_apply(
        service: ProposalService, trip_id: UUID, proposal_id: UUID, expected_revision: int | None
    ):
        barrier.wait(timeout=3)
        return original_apply(service, trip_id, proposal_id, expected_revision)

    observed_lock_sql: list[str] = []
    observed_lock = Lock()

    def capture_lock_order(connection, cursor, statement, parameters, context, executemany):
        del connection, cursor, parameters, context, executemany
        normalized = statement.upper()
        if "FROM PLACES" in normalized and (
            "FOR SHARE" in normalized or "FOR UPDATE" in normalized
        ):
            with observed_lock:
                observed_lock_sql.append(normalized)

    event.listen(database_engine, "before_cursor_execute", capture_lock_order)
    try:
        with monkeypatch.context() as scoped:
            scoped.setattr(ProposalService, "_apply_tx", synchronized_apply)
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [
                    pool.submit(
                        proposal_client.post,
                        f"/v1/trips/{trip['id']}/proposals/{proposal['proposal_id']}/apply",
                        headers={"X-Expected-Revision": str(trip["revision"])},
                    )
                    for trip, proposal in zip(trips, proposals, strict=True)
                ]
                applied = [future.result(timeout=8) for future in futures]
    finally:
        event.remove(database_engine, "before_cursor_execute", capture_lock_order)

    assert [response.status_code for response in applied] == [200, 200]
    assert observed_lock_sql
    assert all("ORDER BY PLACES.ID" in statement for statement in observed_lock_sql)
    for trip in trips:
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
        persisted_outcome = state["applied_outcome"]
        returned_outcome = applied.json()
        persisted_outcome["applied_at"] = datetime.fromisoformat(persisted_outcome["applied_at"])
        returned_outcome["applied_at"] = datetime.fromisoformat(returned_outcome["applied_at"])
        assert persisted_outcome == returned_outcome
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
    post_keys: list[UUID] = []
    remote_result: UpstreamProposalResult | None = None

    async def ambiguous_create(
        _client: PersonalAIClient,
        *,
        payload: dict[str, object],
        idempotency_key: UUID,
    ) -> UpstreamProposalResult:
        nonlocal post_calls, remote_result
        post_calls += 1
        post_keys.append(idempotency_key)
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
        return remote_result

    monkeypatch.setattr(PersonalAIClient, "create_itinerary_proposal", ambiguous_create)
    monkeypatch.setattr(PersonalAIClient, "get_itinerary_proposal_by_key", lookup)
    key = uuid4()
    created = request_proposal(proposal_client, trip, key)
    assert created.status_code == 201, created.text
    assert created.json()["state"] == "outcome_unknown"
    assert lookup_keys == []

    reconciled = proposal_client.get(f"/v1/trips/{trip['id']}/proposals/by-key/{key}")
    assert reconciled.status_code == 200, reconciled.text
    assert reconciled.json()["state"] == "ready"
    assert post_calls == 1
    assert len(lookup_keys) == 1
    assert lookup_keys == post_keys
