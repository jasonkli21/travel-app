from uuid import UUID

from fastapi import APIRouter, status

from personal_travel.api.dependencies import ExpectedRevision, OwnerDependency, SessionDependency
from personal_travel.api.schemas import (
    COMMON_ERROR_RESPONSES,
    PlaceCreate,
    PlaceSummaryResponse,
    PlaceUpdate,
)
from personal_travel.api.serializers import serialize_place
from personal_travel.services.places import PlaceService

router = APIRouter(prefix="/places", tags=["places"], responses=COMMON_ERROR_RESPONSES)


@router.get("", response_model=list[PlaceSummaryResponse])
def list_places(
    session: SessionDependency,
    owner_id: OwnerDependency,
) -> list[PlaceSummaryResponse]:
    result: list[PlaceSummaryResponse] = []
    for place in PlaceService(session, owner_id).list():
        serialized = serialize_place(place)
        if serialized is not None:
            result.append(serialized)
    return result


@router.post("", response_model=PlaceSummaryResponse, status_code=status.HTTP_201_CREATED)
def create_place(
    payload: PlaceCreate,
    session: SessionDependency,
    owner_id: OwnerDependency,
) -> PlaceSummaryResponse:
    result = PlaceService(session, owner_id).create(payload)
    serialized = serialize_place(result)
    assert serialized is not None
    return serialized


@router.patch("/{place_id}", response_model=PlaceSummaryResponse)
def update_place(
    place_id: UUID,
    payload: PlaceUpdate,
    session: SessionDependency,
    owner_id: OwnerDependency,
    expected_revision: ExpectedRevision = None,
) -> PlaceSummaryResponse:
    result = PlaceService(session, owner_id).update(
        place_id, payload, expected_revision=expected_revision
    )
    serialized = serialize_place(result)
    assert serialized is not None
    return serialized
