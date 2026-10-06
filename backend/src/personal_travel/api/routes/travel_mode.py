"""One revision-consistent read projection for the read-focused travel page."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.orm import Session

from personal_travel.api.dependencies import OwnerDependency, SessionDependency
from personal_travel.api.schemas.attachments import AttachmentResponse
from personal_travel.api.schemas.travel_mode import TravelModeResponse
from personal_travel.api.serializers import serialize_detail, serialize_reservation
from personal_travel.config import get_settings
from personal_travel.models.import_source import SourceAttachment
from personal_travel.models.place import Place
from personal_travel.models.reservation import Reservation
from personal_travel.models.trip import Trip
from personal_travel.services.conflicts import calculate_reservation_conflicts
from personal_travel.services.source_store import LocalSourceStore
from personal_travel.services.trips import TripService

router = APIRouter(prefix="/trips/{trip_id}/travel", tags=["travel-mode"])
MAX_TRAVEL_ATTACHMENTS = 50


@dataclass(frozen=True, slots=True)
class _AttachmentSnapshot:
    attachment_id: UUID
    object_key: str
    sha256: str
    media_type: str
    byte_size: int
    display_filename: str
    reservation_id: UUID | None
    state: str
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime


def _trip_reservations(trip: Trip) -> list[Reservation]:
    return sorted(
        trip.reservations,
        key=lambda row: (
            row.starts_at is None,
            row.starts_at or datetime.max.replace(tzinfo=UTC),
            row.status,
            row.provider_name.casefold(),
            row.created_at,
        ),
    )


def _snapshot(
    session: Session, owner_id: str, trip_id: UUID
) -> tuple[TravelModeResponse, list[_AttachmentSnapshot]]:
    trip = TripService(session, owner_id).get(trip_id)
    conflicts = calculate_reservation_conflicts(trip)
    place_ids = {
        place_id
        for place_id in (
            [item.place_id for day in trip.days for item in day.items]
            + [reservation.place_id for reservation in trip.reservations]
        )
        if place_id is not None
    }
    if place_ids:
        session.scalars(
            select(Place)
            .where(Place.owner_id == owner_id, Place.id.in_(place_ids))
            .order_by(Place.id)
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        ).all()

    # The trip root is read-locked by TripService.get. These records and both
    # place footprints are copied before that transaction is released.
    trip_response = serialize_detail(trip)
    reservations = [
        serialize_reservation(row, trip, conflicts.get(row.id, []))
        for row in _trip_reservations(trip)
    ]
    settings = get_settings()
    attachments_enabled = (
        settings.private_attachments_enabled and settings.travel_auth_mode == "google_oidc"
    )
    rows: Sequence[SourceAttachment] = []
    if attachments_enabled:
        rows = session.scalars(
            select(SourceAttachment)
            .where(
                SourceAttachment.owner_id == owner_id,
                SourceAttachment.trip_id == trip_id,
                SourceAttachment.purpose == "trip_attachment",
            )
            .order_by(SourceAttachment.created_at, SourceAttachment.id)
            .limit(MAX_TRAVEL_ATTACHMENTS)
        ).all()
    # Keep the private byte store failure separate from the core projection.
    descriptors = [
        _AttachmentSnapshot(
            attachment_id=row.id,
            object_key=row.object_key,
            sha256=row.sha256,
            media_type=row.media_type,
            byte_size=row.byte_size,
            display_filename=row.display_filename or "travel-document",
            reservation_id=row.reservation_id,
            state=row.state,
            expires_at=row.expires_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
        for row in rows
    ]
    return (
        TravelModeResponse(
            trip=trip_response,
            reservations=reservations,
            attachments=[],
            attachments_available=attachments_enabled,
        ),
        descriptors,
    )


@router.get("", response_model=TravelModeResponse)
def get_travel_mode(
    trip_id: UUID,
    session: SessionDependency,
    owner_id: OwnerDependency,
) -> TravelModeResponse:
    with session.begin():
        response, rows = _snapshot(session, owner_id, trip_id)
        revision = response.trip.revision

    if not response.attachments_available:
        return response
    try:
        store = LocalSourceStore(get_settings().private_source_dir)
    except (OSError, ValueError):
        return response.model_copy(update={"attachments_available": False})
    try:
        attachments = []
        for row in rows:
            available = False
            if row.state == "ready":
                try:
                    available = store.matches(row.object_key, row.sha256, row.byte_size)
                except (OSError, ValueError):
                    available = False
            item = {
                "id": str(row.attachment_id),
                "trip_id": str(trip_id),
                "reservation_id": str(row.reservation_id) if row.reservation_id else None,
                "display_filename": row.display_filename,
                "media_type": row.media_type,
                "byte_size": row.byte_size,
                "state": row.state,
                "expires_at": row.expires_at.isoformat() if row.expires_at else None,
                "created_at": row.created_at.isoformat(),
                "updated_at": row.updated_at.isoformat(),
                "trip_revision": revision,
            }
            if row.state == "ready" and not available:
                item["state"] = "missing"
            item["download_available"] = available
            attachments.append(AttachmentResponse.model_validate(item))
        return response.model_copy(update={"attachments": attachments})
    finally:
        store.close()
