from uuid import UUID

from fastapi import APIRouter, Response, status

from personal_travel.api.dependencies import ExpectedRevision, OwnerDependency, SessionDependency
from personal_travel.api.schemas import (
    COMMON_ERROR_RESPONSES,
    DayUpdate,
    TripCreate,
    TripDetailResponse,
    TripSummaryResponse,
    TripUpdate,
)
from personal_travel.api.serializers import serialize_detail, serialize_summary
from personal_travel.services.trips import TripService

router = APIRouter(prefix="/trips", tags=["trips"], responses=COMMON_ERROR_RESPONSES)


@router.post("", response_model=TripDetailResponse, status_code=status.HTTP_201_CREATED)
def create_trip(
    payload: TripCreate,
    session: SessionDependency,
    owner_id: OwnerDependency,
) -> TripDetailResponse:
    return serialize_detail(TripService(session, owner_id).create(payload))


@router.get("", response_model=list[TripSummaryResponse])
def list_trips(
    session: SessionDependency,
    owner_id: OwnerDependency,
) -> list[TripSummaryResponse]:
    return [
        serialize_summary(trip, day_count=days, item_count=items)
        for trip, days, items in TripService(session, owner_id).list()
    ]


@router.get("/{trip_id}", response_model=TripDetailResponse)
def get_trip(
    trip_id: UUID,
    session: SessionDependency,
    owner_id: OwnerDependency,
) -> TripDetailResponse:
    return serialize_detail(TripService(session, owner_id).get(trip_id))


@router.patch("/{trip_id}", response_model=TripDetailResponse)
def update_trip(
    trip_id: UUID,
    payload: TripUpdate,
    session: SessionDependency,
    owner_id: OwnerDependency,
    expected_revision: ExpectedRevision = None,
) -> TripDetailResponse:
    return serialize_detail(
        TripService(session, owner_id).update(trip_id, payload, expected_revision=expected_revision)
    )


@router.delete("/{trip_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_trip(
    trip_id: UUID,
    session: SessionDependency,
    owner_id: OwnerDependency,
    expected_revision: ExpectedRevision = None,
) -> Response:
    TripService(session, owner_id).delete(trip_id, expected_revision=expected_revision)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/{trip_id}/days/{day_id}", response_model=TripDetailResponse)
def update_day(
    trip_id: UUID,
    day_id: UUID,
    payload: DayUpdate,
    session: SessionDependency,
    owner_id: OwnerDependency,
    expected_revision: ExpectedRevision = None,
) -> TripDetailResponse:
    service = TripService(session, owner_id)
    service.update_day(trip_id, day_id, payload, expected_revision=expected_revision)
    return serialize_detail(service.get(trip_id))
