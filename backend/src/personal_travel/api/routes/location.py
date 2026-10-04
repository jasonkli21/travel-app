from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from personal_travel.api.dependencies import ExpectedRevision, OwnerDependency, SessionDependency
from personal_travel.api.schemas import (
    COMMON_ERROR_RESPONSES,
    ErrorResponse,
    LogisticsEstimateRequest,
    LogisticsEstimateResponse,
    PlaceImportRequest,
    PlaceSearchResponse,
    SavedPlaceResponse,
)
from personal_travel.api.serializers import serialize_saved_place
from personal_travel.services.location import LocationService

PROVIDER_ERROR_RESPONSES = {
    **COMMON_ERROR_RESPONSES,
    502: {
        "model": ErrorResponse,
        "description": "The location provider returned an unusable response.",
    },
    503: {
        "model": ErrorResponse,
        "description": "The location provider is not configured or unavailable.",
    },
}

router = APIRouter(
    prefix="/trips/{trip_id}",
    tags=["location"],
    responses=PROVIDER_ERROR_RESPONSES,
)


@router.get("/places/search", response_model=list[PlaceSearchResponse])
async def search_places(
    trip_id: UUID,
    session: SessionDependency,
    owner_id: OwnerDependency,
    q: Annotated[str, Query(min_length=2, max_length=160)],
    limit: Annotated[int, Query(ge=1, le=10)] = 10,
) -> list[PlaceSearchResponse]:
    return await LocationService(session, owner_id).search_places(trip_id, q, limit=limit)


@router.post("/saved-places/import", response_model=SavedPlaceResponse)
def import_place(
    trip_id: UUID,
    payload: PlaceImportRequest,
    session: SessionDependency,
    owner_id: OwnerDependency,
    expected_revision: ExpectedRevision = None,
) -> SavedPlaceResponse:
    saved_place = LocationService(session, owner_id).import_place(
        trip_id, payload, expected_revision=expected_revision
    )
    return serialize_saved_place(saved_place)


@router.post("/logistics/estimate", response_model=LogisticsEstimateResponse)
async def estimate_logistics(
    trip_id: UUID,
    payload: LogisticsEstimateRequest,
    session: SessionDependency,
    owner_id: OwnerDependency,
) -> LogisticsEstimateResponse:
    return await LocationService(session, owner_id).estimate_logistics(trip_id, payload)
