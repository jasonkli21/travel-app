"""Atomic confirmation of traveler-reviewed booking candidates."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from personal_travel.api.schemas.booking_imports import ImportConfirmRequest
from personal_travel.api.schemas.reservations import ReservationCreate
from personal_travel.domain.types import ReservationStatus, ReservationType
from personal_travel.models.import_source import BookingImport
from personal_travel.models.place import Place
from personal_travel.models.trip import Trip
from personal_travel.repositories.trips import SqlAlchemyTripRepository
from personal_travel.services.booking_imports import _mapped_type, _source_instant
from personal_travel.services.errors import DomainError, not_found
from personal_travel.services.reservations import ReservationService


class BookingConfirmationService:
    def __init__(self, factory: Callable[[], Session]) -> None:
        self._factory = factory

    def confirm(
        self, owner_id: str, trip_id: UUID, import_id: UUID, payload: ImportConfirmRequest
    ) -> dict[str, object]:
        fingerprint = hashlib.sha256(
            json.dumps(
                payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
        with self._factory() as session, session.begin():
            # Fixed lock order shared with extraction and source lifecycle: trip, then import.
            trip = SqlAlchemyTripRepository(session).get(
                owner_id=owner_id, trip_id=trip_id, for_update=True
            )
            if trip is None:
                raise not_found("trip")
            item = session.scalar(
                select(BookingImport)
                .where(
                    BookingImport.id == import_id,
                    BookingImport.trip_id == trip_id,
                    BookingImport.owner_id == owner_id,
                )
                .with_for_update()
            )
            if item is None:
                raise not_found("import")
            if item.confirmation_key is not None:
                if (
                    item.confirmation_key == payload.confirmation_key
                    and item.confirmation_fingerprint == fingerprint
                ):
                    assert item.confirmation_outcome is not None
                    return item.confirmation_outcome
                raise DomainError(
                    "confirmation_already_final",
                    "This import already has a confirmation outcome. Reopen it to see the result.",
                    status_code=409,
                    details={"confirmation_key": str(item.confirmation_key)},
                )
            if item.state != "review_ready" or not item.candidate_snapshot:
                raise DomainError(
                    "review_unavailable",
                    "This extraction is not ready to confirm.",
                    status_code=409,
                )
            if (
                item.upstream_result_expires_at is not None
                and item.upstream_result_expires_at <= datetime.now(UTC)
            ):
                raise DomainError(
                    "extraction_expired",
                    "This extraction expired. Upload the source again to review it.",
                    status_code=410,
                )
            if item.review_revision != payload.expected_import_revision:
                raise DomainError(
                    "stale_revision",
                    "The import changed; reload before confirming.",
                    status_code=409,
                )
            if trip.revision != payload.expected_trip_revision:
                raise DomainError(
                    "stale_revision",
                    "The trip changed after it was loaded. Reload before confirming.",
                    status_code=409,
                    details={"aggregate": "trip", "current_revision": trip.revision},
                )
            upstream = {
                candidate["candidate_id"]: candidate
                for candidate in item.candidate_snapshot.get("upstream", [])
            }
            edits = item.candidate_snapshot.get("edits", {})
            entries = {entry.candidate_id: entry for entry in payload.entries}
            if len(entries) != len(payload.entries) or set(entries) != set(upstream) or not entries:
                raise DomainError(
                    "invalid_confirmation",
                    "Choose create, link, or skip for every candidate before confirming.",
                )
            planned_links: dict[UUID, str] = {}
            for candidate_id, entry in entries.items():
                if entry.decision == "skip" or entry.itinerary_item_id is None:
                    continue
                reservation_target = (
                    f"existing:{entry.existing_reservation_id}"
                    if entry.decision == "link_existing"
                    else f"new:{candidate_id}"
                )
                previous_target = planned_links.get(entry.itinerary_item_id)
                if previous_target is not None and previous_target != reservation_target:
                    raise DomainError(
                        "item_link_conflict",
                        "An itinerary item cannot link to multiple reservations "
                        "in one confirmation.",
                        status_code=409,
                    )
                planned_links[entry.itinerary_item_id] = reservation_target
            reservation_service = ReservationService(session, owner_id)
            outcomes: list[dict[str, object]] = []
            for candidate_id, entry in entries.items():
                if entry.decision == "skip":
                    outcomes.append({"candidate_id": candidate_id, "outcome": "skipped"})
                    continue
                if entry.decision == "link_existing":
                    reservation = next(
                        (
                            value
                            for value in trip.reservations
                            if value.id == entry.existing_reservation_id
                        ),
                        None,
                    )
                    if reservation is None:
                        raise not_found("reservation")
                    if entry.place_id is not None and entry.place_id != reservation.place_id:
                        raise DomainError(
                            "invalid_link_choice", "A linked reservation keeps its existing place."
                        )
                else:
                    merged = dict(upstream[candidate_id]) | edits.get(candidate_id, {})
                    starts_date = (
                        entry.starts_at_date
                        if "starts_at_date" in entry.model_fields_set
                        else merged.get("starts_at_date")
                    )
                    starts_time = (
                        entry.starts_at_time
                        if "starts_at_time" in entry.model_fields_set
                        else merged.get("starts_at_time")
                    )
                    starts_zone = (
                        entry.starts_at_timezone
                        if "starts_at_timezone" in entry.model_fields_set
                        else merged.get("starts_at_timezone")
                    )
                    ends_date = (
                        entry.ends_at_date
                        if "ends_at_date" in entry.model_fields_set
                        else merged.get("ends_at_date")
                    )
                    ends_time = (
                        entry.ends_at_time
                        if "ends_at_time" in entry.model_fields_set
                        else merged.get("ends_at_time")
                    )
                    ends_zone = (
                        entry.ends_at_timezone
                        if "ends_at_timezone" in entry.model_fields_set
                        else merged.get("ends_at_timezone")
                    )
                    starts_at = _source_instant(
                        starts_date, starts_time, starts_zone, field="start time"
                    )
                    ends_at = _source_instant(ends_date, ends_time, ends_zone, field="end time")
                    if ends_at is not None and starts_at is None:
                        raise DomainError(
                            "invalid_reservation_schedule", "An end time requires a start time."
                        )
                    if ends_at is not None and starts_at is not None and ends_at < starts_at:
                        raise DomainError(
                            "invalid_reservation_time_range",
                            "End must occur after start across the converted timezones.",
                        )
                    provider = (
                        entry.provider_name
                        if "provider_name" in entry.model_fields_set
                        else merged.get("provider_name")
                    )
                    reference = (
                        entry.confirmation_code
                        if "confirmation_code" in entry.model_fields_set
                        else merged.get("confirmation_code")
                    )
                    reservation_type = (
                        entry.reservation_type
                        if "reservation_type" in entry.model_fields_set
                        else _mapped_type(merged.get("reservation_type"))
                    )
                    if not provider or not reservation_type:
                        raise DomainError(
                            "required_candidate_field",
                            "Select a reservation type and enter a provider before confirming.",
                        )
                    if (
                        reservation_type in {"flight", "train", "car_rental", "lodging"}
                        and starts_at is None
                    ):
                        raise DomainError(
                            "required_candidate_schedule",
                            "Flight, train, car rental, and lodging reservations need a complete "
                            "start date, time, and timezone.",
                        )
                    place = self._trip_place(session, trip, entry.place_id)
                    data = ReservationCreate(
                        reservation_type=cast(ReservationType, reservation_type),
                        status=cast(ReservationStatus, entry.reservation_status),
                        provider_name=provider,
                        confirmation_code=reference,
                        place_id=entry.place_id,
                        source_reference=f"booking-import:{item.id}:candidate:{candidate_id}",
                    )
                    reservation = reservation_service.create_in_transaction(
                        trip, data, starts_at=starts_at, ends_at=ends_at, place=place
                    )
                linked_item = None
                if entry.itinerary_item_id is not None:
                    linked_item = next(
                        (
                            candidate
                            for day in trip.days
                            for candidate in day.items
                            if candidate.id == entry.itinerary_item_id
                        ),
                        None,
                    )
                    if linked_item is None:
                        raise not_found("itinerary item")
                    if linked_item.reservation_id not in {None, reservation.id}:
                        raise DomainError(
                            "item_already_linked",
                            "This itinerary item already links to another reservation.",
                            status_code=409,
                        )
                    linked_item.reservation_id = reservation.id
                    linked_item.reservation = reservation
                outcomes.append(
                    {
                        "candidate_id": candidate_id,
                        "outcome": "linked" if entry.decision == "link_existing" else "created",
                        "reservation_id": str(reservation.id),
                        "itinerary_item_id": str(linked_item.id) if linked_item else None,
                    }
                )
            if any(entry.decision != "skip" for entry in entries.values()):
                trip.revision += 1
            trip_revision = trip.revision
            item.state = "applied"
            item.confirmation_key = payload.confirmation_key
            item.confirmation_fingerprint = fingerprint
            item.confirmation_outcome = {
                "confirmation_key": str(payload.confirmation_key),
                "import_id": str(item.id),
                "trip_id": str(trip.id),
                "trip_revision": trip_revision,
                "upstream_revision": item.upstream_revision,
                "outcomes": outcomes,
            }
            item.review_revision += 1
            if item.retention_choice == "delete_after_confirmation":
                item.candidate_snapshot = None
            session.flush()
            return item.confirmation_outcome

    @staticmethod
    def _trip_place(session: Session, trip: Trip, place_id: UUID | None) -> Place | None:
        if place_id is None:
            return None
        place = session.scalar(
            select(Place).where(Place.id == place_id, Place.owner_id == trip.owner_id)
        )
        if place is None:
            raise not_found("place")
        belongs = any(saved.place_id == place_id for saved in trip.saved_places)
        belongs = belongs or any(
            candidate.place_id == place_id for day in trip.days for candidate in day.items
        )
        belongs = belongs or any(candidate.place_id == place_id for candidate in trip.reservations)
        if not belongs:
            raise not_found("trip place")
        return place
