"""Owner-scoped trip-document metadata and recoverable byte lifecycle."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from personal_travel.models.import_source import SourceAttachment
from personal_travel.models.reservation import Reservation
from personal_travel.models.trip import Trip
from personal_travel.services.errors import DomainError, not_found
from personal_travel.services.revisions import require_expected_revision

SessionFactoryLike = Callable[[], Session]
MAX_TRIP_ATTACHMENTS = 50


@dataclass(frozen=True, slots=True)
class AttachmentRegistration:
    attachment_id: UUID
    object_key: str
    created: bool
    metadata: dict[str, object]


@dataclass(frozen=True, slots=True)
class AttachmentDescriptor:
    attachment_id: UUID
    object_key: str
    sha256: str
    media_type: str
    byte_size: int
    display_filename: str
    state: str


def attachment_metadata(item: SourceAttachment, trip_revision: int) -> dict[str, object]:
    return {
        "id": str(item.id),
        "trip_id": str(item.trip_id) if item.trip_id is not None else None,
        "reservation_id": str(item.reservation_id) if item.reservation_id is not None else None,
        "display_filename": item.display_filename or "travel-document",
        "media_type": item.media_type,
        "byte_size": item.byte_size,
        "state": item.state,
        "expires_at": item.expires_at.isoformat() if item.expires_at else None,
        "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
        "trip_revision": trip_revision,
    }


def _key_conflict() -> DomainError:
    return DomainError(
        "attachment_request_conflict",
        "This upload key was already used for different document content.",
        status_code=409,
    )


class AttachmentService:
    """Use short-lived SQL sessions; file operations stay outside transactions."""

    def __init__(self, session_factory: SessionFactoryLike) -> None:
        self._session_factory = session_factory

    def list(self, owner_id: str, trip_id: UUID) -> list[dict[str, object]]:
        with self._session_factory() as session:
            trip = session.scalar(
                select(Trip)
                .where(Trip.id == trip_id, Trip.owner_id == owner_id)
                .with_for_update(read=True)
            )
            if trip is None:
                raise not_found("trip")
            items = session.scalars(
                select(SourceAttachment)
                .where(
                    SourceAttachment.owner_id == owner_id,
                    SourceAttachment.trip_id == trip_id,
                    SourceAttachment.purpose == "trip_attachment",
                )
                .order_by(SourceAttachment.created_at, SourceAttachment.id)
                .limit(MAX_TRIP_ATTACHMENTS)
            ).all()
            return [
                attachment_metadata(item, trip.revision) | {"_object_key": item.object_key}
                for item in items
            ]

    def ensure_target(self, owner_id: str, trip_id: UUID, reservation_id: UUID | None) -> None:
        with self._session_factory() as session:
            found = session.scalar(
                select(Trip.id).where(Trip.id == trip_id, Trip.owner_id == owner_id)
            )
            if found is None:
                raise not_found("trip")
            if reservation_id is not None:
                reservation = session.scalar(
                    select(Reservation.id).where(
                        Reservation.id == reservation_id,
                        Reservation.trip_id == trip_id,
                        Reservation.owner_id == owner_id,
                    )
                )
                if reservation is None:
                    raise not_found("reservation")

    def register_upload(
        self,
        *,
        owner_id: str,
        trip_id: UUID,
        expected_revision: int,
        request_key: str,
        fingerprint: str,
        object_key: str,
        sha256: str,
        media_type: str,
        byte_size: int,
        display_filename: str,
        reservation_id: UUID | None,
    ) -> AttachmentRegistration:
        try:
            with self._session_factory() as session, session.begin():
                trip = session.scalar(
                    select(Trip)
                    .where(Trip.id == trip_id, Trip.owner_id == owner_id)
                    .with_for_update()
                )
                if trip is None:
                    raise not_found("trip")
                existing = session.scalar(
                    select(SourceAttachment)
                    .where(
                        SourceAttachment.owner_id == owner_id,
                        SourceAttachment.upload_request_key == request_key,
                    )
                    .with_for_update()
                )
                if existing is not None:
                    if (
                        existing.purpose != "trip_attachment"
                        or existing.trip_id != trip_id
                        or existing.upload_request_fingerprint != fingerprint
                    ):
                        raise _key_conflict()
                    return AttachmentRegistration(
                        existing.id,
                        existing.object_key,
                        False,
                        attachment_metadata(existing, trip.revision),
                    )

                require_expected_revision(trip.revision, expected_revision, aggregate="trip")
                if reservation_id is not None:
                    reservation = session.scalar(
                        select(Reservation.id).where(
                            Reservation.id == reservation_id,
                            Reservation.trip_id == trip_id,
                            Reservation.owner_id == owner_id,
                        )
                    )
                    if reservation is None:
                        raise not_found("reservation")
                count = session.scalar(
                    select(func.count())
                    .select_from(SourceAttachment)
                    .where(
                        SourceAttachment.owner_id == owner_id,
                        SourceAttachment.trip_id == trip_id,
                        SourceAttachment.purpose == "trip_attachment",
                    )
                )
                if (count or 0) >= MAX_TRIP_ATTACHMENTS:
                    raise DomainError(
                        "attachment_limit_reached",
                        f"A trip can have at most {MAX_TRIP_ATTACHMENTS} documents.",
                        status_code=409,
                    )
                item = SourceAttachment(
                    owner_id=owner_id,
                    trip_id=trip_id,
                    reservation_id=reservation_id,
                    purpose="trip_attachment",
                    object_key=object_key,
                    sha256=sha256,
                    media_type=media_type,
                    byte_size=byte_size,
                    display_filename=display_filename,
                    state="pending",
                    expires_at=None,
                    upload_request_key=request_key,
                    upload_request_fingerprint=fingerprint,
                )
                session.add(item)
                trip.revision += 1
                session.flush()
                return AttachmentRegistration(
                    item.id,
                    item.object_key,
                    True,
                    attachment_metadata(item, trip.revision),
                )
        except IntegrityError:
            # Unique request keys arbitrate simultaneous submissions. Resolve
            # the winner in a new transaction and apply the same fingerprint rule.
            with self._session_factory() as session:
                trip = session.scalar(
                    select(Trip).where(Trip.id == trip_id, Trip.owner_id == owner_id)
                )
                existing = session.scalar(
                    select(SourceAttachment).where(
                        SourceAttachment.owner_id == owner_id,
                        SourceAttachment.upload_request_key == request_key,
                    )
                )
                if (
                    trip is not None
                    and existing is not None
                    and existing.purpose == "trip_attachment"
                    and existing.trip_id == trip_id
                    and existing.upload_request_fingerprint == fingerprint
                ):
                    return AttachmentRegistration(
                        existing.id,
                        existing.object_key,
                        False,
                        attachment_metadata(existing, trip.revision),
                    )
            raise _key_conflict() from None

    def mark_ready(self, owner_id: str, trip_id: UUID, attachment_id: UUID) -> dict[str, object]:
        with self._session_factory() as session, session.begin():
            trip = session.scalar(
                select(Trip).where(Trip.id == trip_id, Trip.owner_id == owner_id).with_for_update()
            )
            if trip is None:
                raise not_found("trip")
            item = session.scalar(
                select(SourceAttachment)
                .where(
                    SourceAttachment.id == attachment_id,
                    SourceAttachment.owner_id == owner_id,
                    SourceAttachment.trip_id == trip_id,
                    SourceAttachment.purpose == "trip_attachment",
                )
                .with_for_update()
            )
            if item is None:
                raise not_found("attachment")
            if item.state == "pending":
                item.state = "ready"
                item.updated_at = datetime.now(UTC)
            elif item.state != "ready":
                raise DomainError(
                    "attachment_unavailable", "The document is unavailable.", status_code=410
                )
            return attachment_metadata(item, trip.revision)

    def patch(
        self,
        *,
        owner_id: str,
        trip_id: UUID,
        attachment_id: UUID,
        expected_revision: int,
        display_filename: str | None,
        reservation_id: UUID | None,
        update_label: bool,
        update_reservation: bool,
    ) -> dict[str, object]:
        with self._session_factory() as session, session.begin():
            trip = session.scalar(
                select(Trip).where(Trip.id == trip_id, Trip.owner_id == owner_id).with_for_update()
            )
            if trip is None:
                raise not_found("trip")
            require_expected_revision(trip.revision, expected_revision, aggregate="trip")
            item = session.scalar(
                select(SourceAttachment)
                .where(
                    SourceAttachment.id == attachment_id,
                    SourceAttachment.owner_id == owner_id,
                    SourceAttachment.trip_id == trip_id,
                    SourceAttachment.purpose == "trip_attachment",
                )
                .with_for_update()
            )
            if item is None:
                raise not_found("attachment")
            if item.state != "ready":
                raise DomainError(
                    "attachment_unavailable", "The document is unavailable.", status_code=410
                )
            if update_reservation and reservation_id is not None:
                reservation = session.scalar(
                    select(Reservation.id).where(
                        Reservation.id == reservation_id,
                        Reservation.trip_id == trip_id,
                        Reservation.owner_id == owner_id,
                    )
                )
                if reservation is None:
                    raise not_found("reservation")
            changed = False
            if update_label and display_filename != item.display_filename:
                item.display_filename = display_filename
                changed = True
            if update_reservation and reservation_id != item.reservation_id:
                item.reservation_id = reservation_id
                changed = True
            if changed:
                trip.revision += 1
                item.updated_at = datetime.now(UTC)
                session.flush()
            return attachment_metadata(item, trip.revision)

    def download_descriptor(
        self, owner_id: str, trip_id: UUID, attachment_id: UUID
    ) -> AttachmentDescriptor:
        with self._session_factory() as session:
            trip = session.scalar(
                select(Trip)
                .where(Trip.id == trip_id, Trip.owner_id == owner_id)
                .with_for_update(read=True)
            )
            if trip is None:
                raise not_found("trip")
            item = session.scalar(
                select(SourceAttachment).where(
                    SourceAttachment.id == attachment_id,
                    SourceAttachment.owner_id == owner_id,
                    SourceAttachment.trip_id == trip_id,
                    SourceAttachment.purpose == "trip_attachment",
                )
            )
            if item is None:
                raise not_found("attachment")
            if item.state != "ready" or (
                item.expires_at is not None and item.expires_at <= datetime.now(UTC)
            ):
                raise DomainError(
                    "attachment_unavailable", "The document is unavailable.", status_code=410
                )
            return AttachmentDescriptor(
                item.id,
                item.object_key,
                item.sha256,
                item.media_type,
                item.byte_size,
                item.display_filename or "travel-document",
                item.state,
            )

    def begin_delete(
        self,
        *,
        owner_id: str,
        trip_id: UUID,
        attachment_id: UUID,
        expected_revision: int,
    ) -> str:
        with self._session_factory() as session, session.begin():
            trip = session.scalar(
                select(Trip).where(Trip.id == trip_id, Trip.owner_id == owner_id).with_for_update()
            )
            if trip is None:
                raise not_found("trip")
            require_expected_revision(trip.revision, expected_revision, aggregate="trip")
            item = session.scalar(
                select(SourceAttachment)
                .where(
                    SourceAttachment.id == attachment_id,
                    SourceAttachment.owner_id == owner_id,
                    SourceAttachment.trip_id == trip_id,
                    SourceAttachment.purpose == "trip_attachment",
                )
                .with_for_update()
            )
            if item is None:
                raise not_found("attachment")
            if item.state != "deleting":
                item.state = "deleting"
                item.updated_at = datetime.now(UTC)
                trip.revision += 1
            return item.object_key

    def finish_delete(self, owner_id: str, trip_id: UUID, attachment_id: UUID) -> None:
        with self._session_factory() as session, session.begin():
            item = session.scalar(
                select(SourceAttachment)
                .where(
                    SourceAttachment.id == attachment_id,
                    SourceAttachment.owner_id == owner_id,
                    SourceAttachment.trip_id == trip_id,
                    SourceAttachment.purpose == "trip_attachment",
                    SourceAttachment.state == "deleting",
                )
                .with_for_update()
            )
            if item is None:
                raise not_found("attachment")
            session.delete(item)


def upload_fingerprint(
    *,
    trip_id: UUID,
    media_type: str,
    sha256: str,
    byte_size: int,
    display_filename: str,
    reservation_id: UUID | None,
) -> str:
    content = "\0".join(
        (
            str(trip_id),
            media_type,
            sha256,
            str(byte_size),
            display_filename,
            str(reservation_id or ""),
        )
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
