from uuid import UUID

from fastapi import APIRouter, Response, status

from personal_travel.api.dependencies import ExpectedRevision, OwnerDependency, SessionDependency
from personal_travel.api.schemas import (
    COMMON_ERROR_RESPONSES,
    ManualSavedPlaceCreate,
    SavedPlaceCreate,
    SavedPlaceResponse,
    SavedPlaceUpdate,
)
from personal_travel.api.serializers import serialize_saved_place
from personal_travel.services.saved_places import SavedPlaceService

router = APIRouter(
    prefix="/trips/{trip_id}", tags=["saved-places"], responses=COMMON_ERROR_RESPONSES
)


@router.get("/saved-places", response_model=list[SavedPlaceResponse])
def list_saved_places(
    trip_id: UUID,
    session: SessionDependency,
    owner_id: OwnerDependency,
) -> list[SavedPlaceResponse]:
    return [
        serialize_saved_place(saved_place)
        for saved_place in SavedPlaceService(session, owner_id).list(trip_id)
    ]


@router.post(
    "/saved-places",
    response_model=SavedPlaceResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_saved_place(
    trip_id: UUID,
    payload: SavedPlaceCreate,
    session: SessionDependency,
    owner_id: OwnerDependency,
    expected_revision: ExpectedRevision = None,
) -> SavedPlaceResponse:
    saved_place = SavedPlaceService(session, owner_id).create(
        trip_id, payload, expected_revision=expected_revision
    )
    return serialize_saved_place(saved_place)


@router.post(
    "/saved-places/manual",
    response_model=SavedPlaceResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_manual_saved_place(
    trip_id: UUID,
    payload: ManualSavedPlaceCreate,
    session: SessionDependency,
    owner_id: OwnerDependency,
    expected_revision: ExpectedRevision = None,
) -> SavedPlaceResponse:
    saved_place = SavedPlaceService(session, owner_id).create_manual(
        trip_id, payload, expected_revision=expected_revision
    )
    return serialize_saved_place(saved_place)


@router.patch("/saved-places/{saved_place_id}", response_model=SavedPlaceResponse)
def update_saved_place(
    trip_id: UUID,
    saved_place_id: UUID,
    payload: SavedPlaceUpdate,
    session: SessionDependency,
    owner_id: OwnerDependency,
    expected_revision: ExpectedRevision = None,
) -> SavedPlaceResponse:
    saved_place = SavedPlaceService(session, owner_id).update(
        trip_id, saved_place_id, payload, expected_revision=expected_revision
    )
    return serialize_saved_place(saved_place)


@router.delete("/saved-places/{saved_place_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_saved_place(
    trip_id: UUID,
    saved_place_id: UUID,
    session: SessionDependency,
    owner_id: OwnerDependency,
    expected_revision: ExpectedRevision = None,
) -> Response:
    SavedPlaceService(session, owner_id).delete(
        trip_id, saved_place_id, expected_revision=expected_revision
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
