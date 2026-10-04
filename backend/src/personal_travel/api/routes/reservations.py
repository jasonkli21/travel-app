from uuid import UUID

from fastapi import APIRouter, Response, status

from personal_travel.api.dependencies import ExpectedRevision, OwnerDependency, SessionDependency
from personal_travel.api.schemas import (
    COMMON_ERROR_RESPONSES,
    ReservationCreate,
    ReservationResponse,
    ReservationUpdate,
)
from personal_travel.api.serializers import serialize_reservation
from personal_travel.services.conflicts import calculate_reservation_conflicts
from personal_travel.services.errors import not_found
from personal_travel.services.reservations import ReservationService
from personal_travel.services.trips import TripService

router = APIRouter(
    prefix="/trips/{trip_id}", tags=["reservations"], responses=COMMON_ERROR_RESPONSES
)


def _reservation_response(
    trip_id: UUID,
    reservation_id: UUID,
    session: SessionDependency,
    owner_id: OwnerDependency,
) -> ReservationResponse:
    trip = TripService(session, owner_id).get(trip_id)
    reservation = next(
        (candidate for candidate in trip.reservations if candidate.id == reservation_id), None
    )
    if reservation is None:
        # This should only be reachable after a concurrent delete; use the same safe 404 contract.
        raise not_found("reservation")
    conflicts = calculate_reservation_conflicts(trip)
    return serialize_reservation(reservation, trip, conflicts.get(reservation.id, []))


@router.get("/reservations", response_model=list[ReservationResponse])
def list_reservations(
    trip_id: UUID,
    session: SessionDependency,
    owner_id: OwnerDependency,
) -> list[ReservationResponse]:
    service = ReservationService(session, owner_id)
    reservation_ids = [reservation.id for reservation in service.list(trip_id)]
    trip = TripService(session, owner_id).get(trip_id)
    conflicts = calculate_reservation_conflicts(trip)
    reservations = {reservation.id: reservation for reservation in trip.reservations}
    return [
        serialize_reservation(reservations[reservation_id], trip, conflicts.get(reservation_id, []))
        for reservation_id in reservation_ids
    ]


@router.post(
    "/reservations",
    response_model=ReservationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_reservation(
    trip_id: UUID,
    payload: ReservationCreate,
    session: SessionDependency,
    owner_id: OwnerDependency,
    expected_revision: ExpectedRevision = None,
) -> ReservationResponse:
    reservation = ReservationService(session, owner_id).create(
        trip_id, payload, expected_revision=expected_revision
    )
    return _reservation_response(trip_id, reservation.id, session, owner_id)


@router.patch("/reservations/{reservation_id}", response_model=ReservationResponse)
def update_reservation(
    trip_id: UUID,
    reservation_id: UUID,
    payload: ReservationUpdate,
    session: SessionDependency,
    owner_id: OwnerDependency,
    expected_revision: ExpectedRevision = None,
) -> ReservationResponse:
    ReservationService(session, owner_id).update(
        trip_id, reservation_id, payload, expected_revision=expected_revision
    )
    return _reservation_response(trip_id, reservation_id, session, owner_id)


@router.delete("/reservations/{reservation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_reservation(
    trip_id: UUID,
    reservation_id: UUID,
    session: SessionDependency,
    owner_id: OwnerDependency,
    expected_revision: ExpectedRevision = None,
) -> Response:
    ReservationService(session, owner_id).delete(
        trip_id, reservation_id, expected_revision=expected_revision
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
