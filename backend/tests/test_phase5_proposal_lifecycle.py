"""PostgreSQL lifecycle and atomic-apply checks for itinerary proposals."""

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from os import environ
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from personal_travel.api.dependencies import session_dependency
from personal_travel.clients.personal_ai import PersonalAIClient
from personal_travel.config import Settings, get_settings
from personal_travel.domain.proposals import ProposalSetItemTimes
from personal_travel.domain.upstream_proposals import TravelItineraryContext, UpstreamProposalResult
from personal_travel.main import app
from personal_travel.models.proposal import ItineraryProposal

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


def create_trip_with_item(client: TestClient) -> tuple[dict[str, object], str]:
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
        },
    )
    assert item_response.status_code == 201, item_response.text
    next_trip = item_response.json()
    return next_trip, next_trip["days"][0]["items"][0]["id"]


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
    trip, item_id = create_trip_with_item(proposal_client)

    # Handles are request-scoped, so return an operation targeting the context
    # item without observing any authoritative database IDs upstream.
    async def create_from_payload(
        _client: PersonalAIClient,
        *,
        payload: dict[str, object],
        idempotency_key: UUID,
    ) -> UpstreamProposalResult:
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

    monkeypatch.setattr(PersonalAIClient, "create_itinerary_proposal", create_from_payload)
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
    trip, _item_id = create_trip_with_item(proposal_client)

    async def clear_start(
        _client: PersonalAIClient,
        *,
        payload: dict[str, object],
        idempotency_key: UUID,
    ) -> UpstreamProposalResult:
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
                    start_time=None,
                ),
            ),
            operation_support=(),
            citations=(),
            created_at=now,
            expires_at=now + timedelta(hours=1),
        )

    monkeypatch.setattr(PersonalAIClient, "create_itinerary_proposal", clear_start)
    response = request_proposal(proposal_client, trip, uuid4())
    assert response.status_code == 201, response.text
    proposal = response.json()
    assert proposal["preview"]["after"][0]["items"][0]["start_time"] is None
    assert proposal["preview"]["after"][0]["items"][0]["end_time"] == "10:00"
