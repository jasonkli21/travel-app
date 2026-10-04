from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from personal_travel.api.schemas import (
    ResearchCitationResponse,
    TripResearchRequest,
    TripResearchResponse,
)
from personal_travel.auth.contracts import PersonalAIAuthContext
from personal_travel.clients.personal_ai import PersonalAIClient, PersonalAIError
from personal_travel.config import Settings
from personal_travel.models.trip import Trip, TripDay
from personal_travel.services.errors import DomainError, not_found
from personal_travel.services.time_utils import local_time_string
from personal_travel.services.trips import TripService

if TYPE_CHECKING:
    from personal_travel.models.itinerary import ItineraryItem

MAX_RESEARCH_CONTEXT_CHARS = 190
MAX_RESEARCH_QUESTION_CHARS = 500
_QUESTION_PREFIX = "Trip context: "
_QUESTION_SEPARATOR = "\nQuestion: "


class ResearchService:
    def __init__(
        self,
        session: Session,
        owner_id: str,
        settings: Settings,
        client: PersonalAIClient | None = None,
        *,
        auth_context: PersonalAIAuthContext | None = None,
    ) -> None:
        self._session = session
        self._owner_id = owner_id
        self._settings = settings
        self._client = client or PersonalAIClient(auth_context=auth_context)
        self._trips = TripService(session, owner_id)

    async def research(self, trip_id: UUID, data: TripResearchRequest) -> TripResearchResponse:
        if not self._settings.personal_ai_research_enabled:
            raise DomainError(
                "research_unavailable",
                "Research is unavailable until it is enabled for this travel service.",
                status_code=503,
            )

        question = await run_in_threadpool(self._question_snapshot, trip_id, data)

        try:
            result = await self._client.research(
                question=question,
                freshness=data.freshness,
                idempotency_key=data.idempotency_key,
            )
        except PersonalAIError as exc:
            raise DomainError(
                "research_unavailable",
                "Research could not be completed right now. Your trip was not changed.",
                status_code=503,
            ) from exc

        return TripResearchResponse(
            session_id=result.session_id,
            state=result.state,
            answer=result.answer,
            failure_code=result.failure_code,
            expires_at=result.expires_at,
            citations=[
                ResearchCitationResponse.model_validate(citation.model_dump())
                for citation in result.citations
            ],
        )

    def _question_snapshot(self, trip_id: UUID, data: TripResearchRequest) -> str:
        try:
            trip = self._trips.get(trip_id)
            day = next((candidate for candidate in trip.days if candidate.id == data.day_id), None)
            if day is None:
                raise not_found("trip day")
            return compose_research_question(trip, day, data.question)
        finally:
            self._session.rollback()


def compose_research_question(trip: Trip, day: TripDay, user_question: str) -> str:
    """Build a deterministic, bounded travel context without private item fields."""

    context_limit = min(
        MAX_RESEARCH_CONTEXT_CHARS,
        MAX_RESEARCH_QUESTION_CHARS
        - len(_QUESTION_PREFIX)
        - len(_QUESTION_SEPARATOR)
        - len(user_question),
    )
    context = (
        f"Trip {trip.start_date.isoformat()} to {trip.end_date.isoformat()} "
        f"({trip.timezone}); day {day.date.isoformat()}"
    )
    if len(context) > context_limit:
        raise DomainError(
            "research_context_too_long",
            "The selected trip context is too long for a research request.",
            status_code=422,
        )

    title = _compact(day.title)
    if title:
        context = _append_context(context, f"; day title {title}", context_limit)

    included = 0
    for item in sorted(day.items, key=lambda candidate: candidate.sort_order):
        if item.status == "cancelled":
            continue
        label = _item_context(item, trip.timezone)
        if not label:
            continue
        candidate = _append_context(context, f"; plan {label}", context_limit)
        if candidate == context:
            break
        context = candidate
        included += 1
        if included >= 3:
            break

    question = f"{_QUESTION_PREFIX}{context}{_QUESTION_SEPARATOR}{user_question}"
    if len(question) > MAX_RESEARCH_QUESTION_CHARS:
        raise DomainError(
            "research_context_too_long",
            "The selected trip context is too long for a research request.",
            status_code=422,
        )
    return question


def _append_context(context: str, segment: str, limit: int) -> str:
    available = limit - len(context)
    if available <= 0:
        return context
    if len(segment) <= available:
        return f"{context}{segment}"
    # Text labels are convenience context. If only a small amount fits, preserve
    # the required trip/date/timezone prefix and clip the optional label.
    if available < 10:
        return context
    return f"{context}{segment[:available]}"


def _item_context(item: ItineraryItem, timezone_name: str) -> str:
    title = _compact(item.title)
    place_name = _compact(item.place.name) if item.place is not None else None
    if place_name and place_name.casefold() not in title.casefold():
        title = f"{title} at {place_name}"
    if len(title) > 64:
        title = f"{title[:61].rstrip()}…"
    local_time = local_time_string(item.starts_at, timezone_name)
    return f"{local_time} {title}" if local_time else title


def _compact(value: str | None) -> str:
    if value is None:
        return ""
    return " ".join(value.split())
