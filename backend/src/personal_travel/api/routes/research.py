from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from personal_travel.api.dependencies import ExpectedRevision, OwnerDependency, SessionDependency
from personal_travel.api.schemas import (
    COMMON_ERROR_RESPONSES,
    ErrorResponse,
    SavedPlaceResponse,
    TravelComparisonCandidateSaveRequest,
    TravelComparisonRequest,
    TravelComparisonResponse,
    TripResearchRequest,
    TripResearchResponse,
)
from personal_travel.api.serializers import serialize_saved_place
from personal_travel.auth.contracts import PersonalAIAuthContext
from personal_travel.config import Settings, get_settings
from personal_travel.services.research import ResearchService
from personal_travel.services.travel_comparisons import TravelComparisonService

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


@router.post("/compare", response_model=TravelComparisonResponse)
async def compare_trip_places(
    trip_id: UUID,
    request: Request,
    payload: TravelComparisonRequest,
    session: SessionDependency,
    owner_id: OwnerDependency,
    settings: Annotated[Settings, Depends(get_settings)],
) -> TravelComparisonResponse:
    auth_context: PersonalAIAuthContext | None = request.scope.get("personal_ai_auth_context")
    return await TravelComparisonService(
        session, owner_id, settings, auth_context=auth_context
    ).compare(trip_id, payload)


@router.post(
    "/comparisons/{comparison_id}/candidates/{candidate_id}/save",
    response_model=SavedPlaceResponse,
    status_code=201,
)
async def save_trip_comparison_candidate(
    trip_id: UUID,
    comparison_id: UUID,
    candidate_id: UUID,
    request: Request,
    payload: TravelComparisonCandidateSaveRequest,
    session: SessionDependency,
    owner_id: OwnerDependency,
    settings: Annotated[Settings, Depends(get_settings)],
    expected_revision: ExpectedRevision = None,
) -> SavedPlaceResponse:
    auth_context: PersonalAIAuthContext | None = request.scope.get("personal_ai_auth_context")
    saved_place = await TravelComparisonService(
        session, owner_id, settings, auth_context=auth_context
    ).save_candidate(
        trip_id,
        comparison_id,
        candidate_id,
        payload,
        expected_revision=expected_revision,
    )
    return serialize_saved_place(saved_place)
