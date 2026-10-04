from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from personal_travel.api.dependencies import OwnerDependency, SessionDependency
from personal_travel.api.schemas import (
    COMMON_ERROR_RESPONSES,
    ErrorResponse,
    TripResearchRequest,
    TripResearchResponse,
)
from personal_travel.auth.contracts import PersonalAIAuthContext
from personal_travel.config import Settings, get_settings
from personal_travel.services.research import ResearchService

RESEARCH_ERROR_RESPONSES = {
    **COMMON_ERROR_RESPONSES,
    503: {
        "model": ErrorResponse,
        "description": "Research is disabled or the configured AI service is unavailable.",
    },
}

router = APIRouter(
    prefix="/trips/{trip_id}/research",
    tags=["research"],
    responses=RESEARCH_ERROR_RESPONSES,
)


@router.post("", response_model=TripResearchResponse)
async def research_trip_day(
    trip_id: UUID,
    request: Request,
    payload: TripResearchRequest,
    session: SessionDependency,
    owner_id: OwnerDependency,
    settings: Annotated[Settings, Depends(get_settings)],
) -> TripResearchResponse:
    auth_context: PersonalAIAuthContext | None = request.scope.get("personal_ai_auth_context")
    return await ResearchService(session, owner_id, settings, auth_context=auth_context).research(
        trip_id, payload
    )
