import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from personal_travel.models.reservation import Reservation


class SqlAlchemyReservationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(
        self,
        *,
        owner_id: str,
        trip_id: uuid.UUID,
        reservation_id: uuid.UUID,
        for_update: bool = False,
    ) -> Reservation | None:
        statement = (
            select(Reservation)
            .where(
                Reservation.owner_id == owner_id,
                Reservation.trip_id == trip_id,
                Reservation.id == reservation_id,
            )
            .options(selectinload(Reservation.place))
        )
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def add(self, reservation: Reservation) -> Reservation:
        self._session.add(reservation)
        return reservation

    def delete(self, reservation: Reservation) -> None:
        self._session.delete(reservation)
