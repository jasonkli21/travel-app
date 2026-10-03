import asyncio
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from personal_travel.api.schemas import ResearchCitationResponse, TripResearchRequest
from personal_travel.clients.personal_ai import PersonalAIClient
from personal_travel.config import Settings
from personal_travel.services.errors import DomainError
from personal_travel.services.research import ResearchService, compose_research_question


def test_trip_research_request_normalizes_and_bounds_question() -> None:
    request = TripResearchRequest(
        day_id=uuid4(),
        question="  Find   a quiet vegetarian dinner  ",
        idempotency_key=uuid4(),
    )

    assert request.question == "Find a quiet vegetarian dinner"
    assert request.freshness == "current"
    with pytest.raises(ValidationError):
        TripResearchRequest(
            day_id=uuid4(),
            question="x" * 301,
            idempotency_key=uuid4(),
        )


def test_research_question_projects_only_bounded_day_context() -> None:
    trip_id = uuid4()
    private_reservation = SimpleNamespace(
        confirmation_code="SECRET-CONFIRMATION",
        source_reference="https://private.example/booking",
        notes="SECRET RESERVATION NOTE",
    )
    item = SimpleNamespace(
        status="planned",
        sort_order=0,
        title="Museum visit",
        place=SimpleNamespace(name="Example Museum"),
        starts_at=datetime(2026, 10, 3, 17, tzinfo=UTC),
        reservation=private_reservation,
        notes="SECRET ITINERARY NOTE",
    )
    cancelled = SimpleNamespace(
        status="cancelled",
        sort_order=1,
        title="Do not send",
        place=None,
        starts_at=None,
        reservation=None,
        notes=None,
    )
    trip = SimpleNamespace(
        id=trip_id,
        owner_id="PRIVATE-OWNER",
        start_date=date(2026, 10, 3),
        end_date=date(2026, 10, 5),
        timezone="America/Los_Angeles",
        title="PRIVATE TRIP TITLE",
    )
    day = SimpleNamespace(
        id=uuid4(),
        trip_id=trip_id,
        date=date(2026, 10, 3),
        title="Downtown day",
        items=[item, cancelled],
    )

    question = compose_research_question(trip, day, "Find a quiet dinner")

    assert question.startswith("Trip context: Trip 2026-10-03 to 2026-10-05")
    assert "Downtown day" in question
    assert "Museum visit at Example Museum" in question
    assert "SECRET" not in question
    assert "PRIVATE" not in question
    assert "Do not send" not in question
    assert len(question.split("\nQuestion: ", maxsplit=1)[0].removeprefix("Trip context: ")) <= 190
    assert len(question) <= 500


def test_research_question_keeps_long_user_query_within_downstream_limit() -> None:
    trip = SimpleNamespace(
        start_date=date(2026, 10, 3),
        end_date=date(2026, 10, 3),
        timezone="America/Los_Angeles",
    )
    day = SimpleNamespace(date=date(2026, 10, 3), title="A long day title" * 20, items=[])

    question = compose_research_question(trip, day, "x" * 300)

    assert len(question) <= 500
    assert len(question.split("\nQuestion: ", maxsplit=1)[0].removeprefix("Trip context: ")) <= 190
    assert question.endswith("x" * 300)


def test_research_citation_rejects_non_http_links_and_credentials() -> None:
    base = {
        "number": 1,
        "evidence_id": uuid4(),
        "source_observation_id": uuid4(),
        "title": None,
        "observed_at": datetime.now(UTC),
        "expires_at": datetime.now(UTC) + timedelta(days=1),
    }

    with pytest.raises(ValidationError):
        ResearchCitationResponse(url="javascript:alert(1)", **base)
    with pytest.raises(ValidationError):
        ResearchCitationResponse(url="https://user:password@example.test/path", **base)


def test_disabled_research_gate_does_not_contact_the_ai_client() -> None:
    class NoCallClient:
        async def research(self, **_kwargs: object) -> None:
            raise AssertionError("disabled research must not call the AI client")

    service = ResearchService(
        cast(Session, None),
        "local",
        Settings(personal_ai_research_enabled=False),
        client=cast(PersonalAIClient, NoCallClient()),
    )
    with pytest.raises(DomainError) as error:
        asyncio.run(
            service.research(
                uuid4(),
                TripResearchRequest(
                    day_id=uuid4(),
                    question="Find a quiet dinner",
                    idempotency_key=uuid4(),
                ),
            )
        )

    assert error.value.code == "research_unavailable"
