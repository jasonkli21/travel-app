from uuid import UUID

from fastapi import APIRouter, status

from personal_travel.api.dependencies import ExpectedRevision, OwnerDependency, SessionDependency
from personal_travel.api.schemas import (
    COMMON_ERROR_RESPONSES,
    ItemCreate,
    ItemUpdate,
    MoveItemRequest,
    TripDetailResponse,
)
from personal_travel.api.serializers import serialize_detail
from personal_travel.services.itinerary import ItineraryService
from personal_travel.services.trips import TripService

router = APIRouter(prefix="/trips/{trip_id}", tags=["itinerary"], responses=COMMON_ERROR_RESPONSES)


@router.post(
    "/days/{day_id}/items",
    response_model=TripDetailResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_item(
    trip_id: UUID,
    day_id: UUID,
    payload: ItemCreate,
    session: SessionDependency,
    owner_id: OwnerDependency,
    expected_revision: ExpectedRevision = None,
) -> TripDetailResponse:
    service = ItineraryService(session, owner_id)
    service.create_item(trip_id, day_id, payload, expected_revision=expected_revision)
    return serialize_detail(TripService(session, owner_id).get(trip_id))


@router.patch("/items/{item_id}", response_model=TripDetailResponse)
def update_item(
    trip_id: UUID,
    item_id: UUID,
    payload: ItemUpdate,
    session: SessionDependency,
    owner_id: OwnerDependency,
    expected_revision: ExpectedRevision = None,
) -> TripDetailResponse:
    service = ItineraryService(session, owner_id)
    service.update_item(trip_id, item_id, payload, expected_revision=expected_revision)
    return serialize_detail(TripService(session, owner_id).get(trip_id))


@router.delete("/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_item(
    trip_id: UUID,
    item_id: UUID,
    session: SessionDependency,
    owner_id: OwnerDependency,
    expected_revision: ExpectedRevision = None,
) -> None:
    ItineraryService(session, owner_id).delete_item(
        trip_id, item_id, expected_revision=expected_revision
    )


@router.post("/items/{item_id}/move", response_model=TripDetailResponse)
def move_item(
    trip_id: UUID,
    item_id: UUID,
    payload: MoveItemRequest,
    session: SessionDependency,
    owner_id: OwnerDependency,
    expected_revision: ExpectedRevision = None,
) -> TripDetailResponse:
    service = ItineraryService(session, owner_id)
    service.move_item(trip_id, item_id, payload, expected_revision=expected_revision)
    return serialize_detail(TripService(session, owner_id).get(trip_id))
