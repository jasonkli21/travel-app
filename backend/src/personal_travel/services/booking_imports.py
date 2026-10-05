"""Durable extraction review and atomic booking confirmation lifecycle."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, timezone
from typing import cast
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from personal_travel.api.schemas.booking_imports import (
    ImportConfirmRequest,
    ImportEditsRequest,
)
from personal_travel.api.schemas.reservations import ReservationCreate
from personal_travel.auth.contracts import PersonalAIAuthContext
from personal_travel.clients.personal_ai import (
    PersonalAIClient,
    PersonalAIExtractionError,
    PersonalAIExtractionUnknown,
)
from personal_travel.config import Settings
from personal_travel.domain.types import ReservationType
from personal_travel.domain.upstream_extractions import (
    UPSTREAM_REVISION,
    UpstreamBookingExtractionResult,
)
from personal_travel.models.import_source import BookingImport, SourceAttachment
from personal_travel.models.place import Place
from personal_travel.models.reservation import Reservation
from personal_travel.models.trip import Trip
from personal_travel.repositories.trips import SqlAlchemyTripRepository
from personal_travel.services.errors import DomainError, not_found
from personal_travel.services.reservations import ReservationService
from personal_travel.services.source_lifecycle import SourceLifecycleService
from personal_travel.services.source_parser import MAX_TEXT_CHARS, SourceParseError, parse_pdf
from personal_travel.services.source_store import LocalSourceStore
from personal_travel.services.time_utils import as_aware_utc, resolve_local_datetime

CLAIM_LEASE = timedelta(seconds=60)
MAX_SOURCE_BYTES = 10 * 1024 * 1024
OFFSET_ZONE = re.compile(r"^([+-])(0\d|1[0-4]):([0-5]\d)$")


@dataclass(frozen=True, slots=True)
class ExtractionClaim:
    owner_id: str
    import_id: UUID
    trip_id: UUID
    key: UUID
    token: UUID
    post_attempted: bool
    state: str


def _normalized(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").casefold())


def _zone(value: str | None, *, field: str) -> ZoneInfo | timezone:
    if not value:
        raise DomainError(
            "timezone_resolution_required",
            f"Choose a timezone for {field} before confirming this schedule.",
        )
    match = OFFSET_ZONE.fullmatch(value)
    if match:
        hours, minutes = int(match.group(2)), int(match.group(3))
        if hours == 14 and minutes != 0:
            raise DomainError("invalid_timezone", f"{field} has an invalid UTC offset.")
        delta = timedelta(hours=hours, minutes=minutes)
        if match.group(1) == "-":
            delta = -delta
        return timezone(delta)
    try:
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise DomainError(
            "invalid_timezone", f"{field} must be an IANA zone or ±HH:MM offset."
        ) from error


def _source_instant(
    day: date | str | None, clock: str | None, zone: str | None, *, field: str
) -> datetime | None:
    if (day is None) != (clock is None):
        raise DomainError(
            "invalid_reservation_schedule", f"{field} date and time must be supplied together."
        )
    if day is None:
        if zone is not None:
            raise DomainError("invalid_timezone", f"{field} timezone requires a schedule.")
        return None
    if isinstance(day, str):
        try:
            day = date.fromisoformat(day)
        except ValueError as error:
            raise DomainError(
                "invalid_reservation_schedule", f"{field} date is invalid."
            ) from error
    try:
        local_time = time.fromisoformat(clock or "")
    except ValueError as error:
        raise DomainError("invalid_local_time", f"{field} must use a valid HH:MM time.") from error
    resolved = _zone(zone, field=field)
    if isinstance(resolved, ZoneInfo):
        return resolve_local_datetime(day, local_time, resolved, field_name=field).astimezone(UTC)
    return datetime.combine(day, local_time, tzinfo=resolved).astimezone(UTC)


def _mapped_type(value: str | None) -> str | None:
    if value is None:
        return None
    return {"rail": "train", "car": "car_rental"}.get(value, value)


class BookingImportService:
    """Own short SQL transactions; source parsing and upstream HTTP run without DB locks."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        settings: Settings,
        *,
        auth_context: PersonalAIAuthContext | None = None,
        client: PersonalAIClient | None = None,
        source_store_factory: Callable[[], LocalSourceStore] | None = None,
    ) -> None:
        self._factory = session_factory
        self._settings = settings
        self._auth_context = auth_context
        self._client = client or PersonalAIClient(
            timeout_seconds=settings.personal_ai_extraction_timeout_seconds,
            auth_context=auth_context,
        )
        self._source_store_factory = source_store_factory or (
            lambda: LocalSourceStore(settings.private_source_dir)
        )

    async def extract(self, owner_id: str, trip_id: UUID, import_id: UUID) -> dict[str, object]:
        if not self._settings.personal_ai_extractions_enabled:
            raise DomainError(
                "booking_extraction_unavailable",
                "Booking extraction is unavailable until enabled for this travel service.",
                status_code=503,
            )
        claim = await run_in_threadpool(self._claim, owner_id, trip_id, import_id)
        if isinstance(claim, dict):
            return claim
        try:
            text, media_type, source_hash = await run_in_threadpool(
                self._read_source, owner_id, trip_id, import_id
            )
        except DomainError:
            await run_in_threadpool(self._fail_claim, claim, "source_unavailable")
            raise
        except SourceParseError as error:
            await run_in_threadpool(self._fail_claim, claim, error.args[0])
            raise DomainError(
                "source_parse_failed", "This document could not be read safely.", status_code=400
            ) from error

        result: UpstreamBookingExtractionResult | None
        if not claim.post_attempted:
            await run_in_threadpool(self._mark_post_attempted, claim)
            payload: dict[str, object] = {
                "schema_version": "booking-document-extraction-v1",
                "idempotency_key": str(claim.key),
                "source_sha256": source_hash,
                "media_type": media_type,
                "consent": "submit_for_booking_extraction",
                "synthetic_fixture": False,
                "document_text": text,
            }
            try:
                result = await self._client.create_booking_extraction(
                    payload=payload, idempotency_key=claim.key, source_sha256=source_hash
                )
            except PersonalAIExtractionUnknown:
                await run_in_threadpool(self._release_claim, claim)
                return await run_in_threadpool(self._detail, owner_id, trip_id, import_id)
            except PersonalAIExtractionError:
                await run_in_threadpool(self._release_claim, claim)
                return await run_in_threadpool(self._detail, owner_id, trip_id, import_id)
        else:
            try:
                result = await self._client.get_booking_extraction_by_key(claim.key, source_hash)
            except PersonalAIExtractionError:
                await run_in_threadpool(self._release_claim, claim)
                return await run_in_threadpool(self._detail, owner_id, trip_id, import_id)
            if result is None or result.state == "running":
                await run_in_threadpool(self._release_claim, claim)
                return await run_in_threadpool(self._detail, owner_id, trip_id, import_id)

        assert result is not None
        try:
            await run_in_threadpool(self._validate_result, result, text, source_hash)
        except DomainError:
            await run_in_threadpool(self._fail_claim, claim, "invalid_upstream_result")
            return await run_in_threadpool(self._detail, owner_id, trip_id, import_id)
        return await run_in_threadpool(self._save_result, claim, result)

    def delete_upstream_extraction(self, owner_id: str, trip_id: UUID, import_id: UUID) -> None:
        """Delete the upstream keyed result, including a POST racing source deletion."""
        with self._factory() as session, session.begin():
            trip = session.scalar(
                select(Trip).where(Trip.id == trip_id, Trip.owner_id == owner_id).with_for_update()
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
            if not item.upstream_delete_pending:
                return
            if item.extraction_key is None:
                item.upstream_delete_pending = False
                return
            extraction_key, source_hash = item.extraction_key, item.source_sha256

        try:
            deleted = asyncio.run(
                self._client.delete_booking_extraction_by_key(extraction_key, source_hash)
            )
            if deleted.state != "deleted":
                raise PersonalAIExtractionError("upstream deletion was not confirmed")
        except (PersonalAIExtractionError, RuntimeError):
            raise DomainError(
                "upstream_delete_pending",
                "The local source is deleted, but AI result deletion is pending. "
                "Retry deletion to finish cleanup.",
                status_code=503,
            ) from None

        with self._factory() as session, session.begin():
            trip = session.scalar(
                select(Trip).where(Trip.id == trip_id, Trip.owner_id == owner_id).with_for_update()
            )
            if trip is None:
                raise not_found("trip")
            item = session.scalar(
                select(BookingImport)
                .where(
                    BookingImport.id == import_id,
                    BookingImport.trip_id == trip_id,
                    BookingImport.owner_id == owner_id,
                    BookingImport.extraction_key == extraction_key,
                )
                .with_for_update()
            )
            if item is not None:
                item.upstream_delete_pending = False

    @staticmethod
    def _validate_result(
        result: UpstreamBookingExtractionResult, source_text: str, source_hash: str
    ) -> None:
        if result.source_sha256 != source_hash:
            raise DomainError(
                "invalid_upstream_result", "The extraction result did not match this source."
            )
        if result.state != "completed":
            return
        if len(result.candidates) > 10:
            raise DomainError(
                "invalid_upstream_result", "The extraction result exceeded its limit."
            )
        for candidate in result.candidates:
            if not 0 <= candidate.source_start < candidate.source_end <= len(source_text):
                raise DomainError(
                    "invalid_upstream_result", "The extraction result had an invalid source span."
                )
            excerpt = source_text[candidate.source_start : candidate.source_end]
            if not excerpt.strip() or len(excerpt) > 240 or excerpt != candidate.source_excerpt:
                raise DomainError(
                    "invalid_upstream_result", "The extraction result had invalid source evidence."
                )
            for day, local_time, zone, name in (
                (
                    candidate.starts_at_date,
                    candidate.starts_at_time,
                    candidate.starts_at_timezone,
                    "start time",
                ),
                (
                    candidate.ends_at_date,
                    candidate.ends_at_time,
                    candidate.ends_at_timezone,
                    "end time",
                ),
            ):
                if day is None and local_time is None and zone is None:
                    continue
                if (day is None) != (local_time is None):
                    raise DomainError(
                        "invalid_upstream_result",
                        f"The extraction result had an incomplete {name}.",
                    )
                if zone is not None:
                    _zone(zone, field=name)

    def _claim(
        self, owner_id: str, trip_id: UUID, import_id: UUID
    ) -> ExtractionClaim | dict[str, object]:
        now = datetime.now(UTC)
        with self._factory() as session, session.begin():
            trip = session.scalar(
                select(Trip).where(Trip.id == trip_id, Trip.owner_id == owner_id).with_for_update()
            )
            if trip is None:
                raise not_found("trip")
            item = session.scalar(
                select(BookingImport)
                .where(
                    BookingImport.id == import_id,
                    BookingImport.owner_id == owner_id,
                    BookingImport.trip_id == trip_id,
                )
                .with_for_update()
            )
            if item is None:
                raise not_found("import")
            if item.state == "review_ready":
                return self._detail_for(item, trip.timezone, trip.reservations)
            if item.state in {"applied", "rejected", "failed"}:
                return self._detail_for(item, trip.timezone, trip.reservations)
            source = session.scalar(
                select(SourceAttachment).where(
                    SourceAttachment.id == item.source_id,
                    SourceAttachment.owner_id == owner_id,
                    SourceAttachment.trip_id == trip_id,
                )
            )
            if source is None or source.state != "ready" or source.expires_at <= now:
                raise DomainError(
                    "source_unavailable", "The source is unavailable.", status_code=410
                )
            if item.extraction_claimed_at and item.extraction_claimed_at + CLAIM_LEASE > now:
                return self._detail_for(item, trip.timezone, trip.reservations)
            if item.extraction_key is None:
                item.extraction_key = uuid4()
            if item.upstream_revision is None:
                item.upstream_revision = UPSTREAM_REVISION
            token = uuid4()
            item.state = "extracting"
            item.extraction_claim_token = token
            item.extraction_claimed_at = now
            return ExtractionClaim(
                owner_id=owner_id,
                import_id=item.id,
                trip_id=trip.id,
                key=item.extraction_key,
                token=token,
                post_attempted=item.extraction_post_attempted,
                state=item.state,
            )

    def _read_source(self, owner_id: str, trip_id: UUID, import_id: UUID) -> tuple[str, str, str]:
        lifecycle = SourceLifecycleService(self._factory)
        descriptor = lifecycle.download_descriptor(owner_id, trip_id, import_id)
        store = self._source_store_factory()
        try:
            data = store.read(descriptor.object_key)
            if (
                len(data) != descriptor.byte_size
                or len(data) > MAX_SOURCE_BYTES
                or hashlib.sha256(data).hexdigest() != descriptor.sha256
            ):
                raise DomainError(
                    "source_unavailable", "The source is unavailable.", status_code=410
                )
            if descriptor.media_type == "text/plain":
                try:
                    text = data.decode("utf-8")
                except UnicodeDecodeError as error:
                    raise DomainError(
                        "source_unavailable", "The source is unavailable.", status_code=410
                    ) from error
            else:
                try:
                    text = parse_pdf(store.root / descriptor.object_key)
                except SourceParseError:
                    raise
            if not text.strip() or len(text) > MAX_TEXT_CHARS:
                raise DomainError(
                    "source_unavailable", "The source is unavailable.", status_code=410
                )
            return text, descriptor.media_type, descriptor.sha256
        finally:
            store.close()

    def _mark_post_attempted(self, claim: ExtractionClaim) -> None:
        with self._factory() as session, session.begin():
            trip = session.scalar(
                select(Trip)
                .where(Trip.id == claim.trip_id, Trip.owner_id == claim.owner_id)
                .with_for_update()
            )
            if trip is None:
                raise not_found("trip")
            item = session.scalar(
                select(BookingImport)
                .where(
                    BookingImport.id == claim.import_id,
                    BookingImport.trip_id == claim.trip_id,
                    BookingImport.extraction_claim_token == claim.token,
                    BookingImport.state == "extracting",
                )
                .with_for_update()
            )
            if item is None:
                raise DomainError(
                    "extraction_claim_lost", "The import changed; reopen it.", status_code=409
                )
            item.extraction_post_attempted = True

    def _release_claim(self, claim: ExtractionClaim) -> None:
        with self._factory() as session, session.begin():
            trip = session.scalar(
                select(Trip)
                .where(Trip.id == claim.trip_id, Trip.owner_id == claim.owner_id)
                .with_for_update()
            )
            if trip is None:
                return
            item = session.scalar(
                select(BookingImport)
                .where(
                    BookingImport.id == claim.import_id,
                    BookingImport.trip_id == claim.trip_id,
                    BookingImport.owner_id == claim.owner_id,
                    BookingImport.extraction_claim_token == claim.token,
                )
                .with_for_update()
            )
            if item is not None:
                item.extraction_claim_token = None
                item.extraction_claimed_at = None

    def _save_result(
        self, claim: ExtractionClaim, result: UpstreamBookingExtractionResult
    ) -> dict[str, object]:
        now = datetime.now(UTC)
        with self._factory() as session, session.begin():
            trip = session.scalar(select(Trip).where(Trip.id == claim.trip_id).with_for_update())
            if trip is None:
                raise not_found("trip")
            item = session.scalar(
                select(BookingImport)
                .where(
                    BookingImport.id == claim.import_id,
                    BookingImport.trip_id == claim.trip_id,
                    BookingImport.extraction_claim_token == claim.token,
                )
                .with_for_update()
            )
            if item is None:
                raise DomainError(
                    "extraction_claim_lost", "The import changed; reopen it.", status_code=409
                )
            item.extraction_claim_token = None
            item.extraction_claimed_at = None
            item.upstream_extraction_id = result.extraction_id
            item.upstream_result_expires_at = result.expires_at
            if result.state == "completed" and result.expires_at > now:
                item.candidate_snapshot = {
                    "schema_version": result.schema_version,
                    "source_sha256": result.source_sha256,
                    "upstream": [
                        candidate.model_dump(mode="json") for candidate in result.candidates
                    ],
                    "edits": {},
                }
                item.state = "review_ready"
                item.review_revision += 1
            elif result.state in {"failed", "deleted", "expired"}:
                item.state = "failed"
                item.candidate_snapshot = {
                    "failure_code": result.failure_code or "provider_unavailable"
                }
            else:
                item.state = "extracting"
            return self._detail_for(item, trip.timezone, trip.reservations)

    def _fail_claim(self, claim: ExtractionClaim, code: str) -> None:
        with self._factory() as session, session.begin():
            trip = session.scalar(
                select(Trip)
                .where(Trip.id == claim.trip_id, Trip.owner_id == claim.owner_id)
                .with_for_update()
            )
            if trip is None:
                return
            item = session.scalar(
                select(BookingImport)
                .where(
                    BookingImport.id == claim.import_id,
                    BookingImport.trip_id == claim.trip_id,
                    BookingImport.owner_id == claim.owner_id,
                    BookingImport.extraction_claim_token == claim.token,
                )
                .with_for_update()
            )
            if item is not None:
                item.state = "failed"
                item.extraction_claim_token = None
                item.extraction_claimed_at = None
                item.candidate_snapshot = {"failure_code": code}

    def _detail(self, owner_id: str, trip_id: UUID, import_id: UUID) -> dict[str, object]:
        return self.detail(owner_id, trip_id, import_id)

    @staticmethod
    def _detail_for(
        item: BookingImport,
        trip_timezone: str,
        reservations: list[Reservation] | None = None,
    ) -> dict[str, object]:
        data: dict[str, object] = {
            "id": str(item.id),
            "trip_id": str(item.trip_id),
            "state": item.state,
            "review_revision": item.review_revision,
            "trip_timezone": trip_timezone,
            "retention_choice": item.retention_choice,
            "upstream_result_expires_at": (
                item.upstream_result_expires_at.isoformat()
                if item.upstream_result_expires_at
                else None
            ),
            "upstream_revision": item.upstream_revision,
            "confirmation_outcome": item.confirmation_outcome,
            "upstream_delete_pending": item.upstream_delete_pending,
            "failure_code": (
                item.candidate_snapshot.get("failure_code") if item.candidate_snapshot else None
            ),
            "outcome_unknown": item.state == "extracting" and item.extraction_post_attempted,
        }
        snapshot = item.candidate_snapshot
        if (
            snapshot
            and snapshot.get("upstream")
            and (
                item.upstream_result_expires_at is None
                or item.upstream_result_expires_at > datetime.now(UTC)
            )
        ):
            upstream = snapshot["upstream"]
            edits = snapshot.get("edits", {})
            views: list[dict[str, object]] = []
            for raw in upstream:
                candidate = dict(raw)
                candidate["starts_at_trip_local"] = _trip_local_parts(
                    candidate.get("starts_at_date"),
                    candidate.get("starts_at_time"),
                    candidate.get("starts_at_timezone"),
                    trip_timezone,
                )
                candidate["ends_at_trip_local"] = _trip_local_parts(
                    candidate.get("ends_at_date"),
                    candidate.get("ends_at_time"),
                    candidate.get("ends_at_timezone"),
                    trip_timezone,
                )
                candidate["current"] = candidate | edits.get(candidate["candidate_id"], {})
                views.append(candidate)
            data["candidates"] = views
            data["duplicate_suggestions"] = _duplicates(views, reservations or [])
        else:
            data["candidates"] = []
        return data

    def update_edits(
        self, owner_id: str, trip_id: UUID, import_id: UUID, payload: ImportEditsRequest
    ) -> dict[str, object]:
        with self._factory() as session, session.begin():
            trip = session.scalar(
                select(Trip).where(Trip.id == trip_id, Trip.owner_id == owner_id).with_for_update()
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
            if item.state != "review_ready" or not item.candidate_snapshot:
                raise DomainError(
                    "review_unavailable", "This extraction is not ready to review.", status_code=409
                )
            if item.review_revision != payload.expected_import_revision:
                raise DomainError(
                    "stale_revision", "The import changed; reload before editing.", status_code=409
                )
            upstream = item.candidate_snapshot.get("upstream", [])
            known = {candidate["candidate_id"] for candidate in upstream}
            edits = dict(item.candidate_snapshot.get("edits", {}))
            for edit in payload.edits:
                if edit.candidate_id not in known:
                    raise DomainError(
                        "unknown_candidate",
                        "A candidate no longer belongs to this import.",
                        status_code=409,
                    )
                values = edit.model_dump(exclude_unset=True, mode="json")
                values.pop("candidate_id", None)
                edits[edit.candidate_id] = edits.get(edit.candidate_id, {}) | values
            item.candidate_snapshot = item.candidate_snapshot | {"edits": edits}
            item.review_revision += 1
            return self._detail_for(item, trip.timezone, trip.reservations)

    def detail(self, owner_id: str, trip_id: UUID, import_id: UUID) -> dict[str, object]:
        now = datetime.now(UTC)
        with self._factory() as session, session.begin():
            trip = session.scalar(
                select(Trip).where(Trip.id == trip_id, Trip.owner_id == owner_id).with_for_update()
            )
            item = session.scalar(
                select(BookingImport)
                .where(
                    BookingImport.id == import_id,
                    BookingImport.trip_id == trip_id,
                    BookingImport.owner_id == owner_id,
                )
                .with_for_update()
            )
            if trip is None or item is None:
                raise not_found("import")
            if (
                item.state == "review_ready"
                and item.upstream_result_expires_at is not None
                and item.upstream_result_expires_at <= now
            ):
                item.state = "expired"
                item.candidate_snapshot = None
                item.review_revision += 1
            return self._detail_for(item, trip.timezone, trip.reservations)

    def reject(self, owner_id: str, trip_id: UUID, import_id: UUID) -> dict[str, object]:
        with self._factory() as session, session.begin():
            trip = session.scalar(
                select(Trip).where(Trip.id == trip_id, Trip.owner_id == owner_id).with_for_update()
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
            if item.state not in {"review_ready", "failed", "received", "extracting"}:
                raise DomainError(
                    "import_already_final",
                    "This import already has a final outcome.",
                    status_code=409,
                )
            item.state = "rejected"
            item.candidate_snapshot = None
            item.review_revision += 1
            return self._detail_for(item, trip.timezone, trip.reservations)

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
                    place = self._trip_place(session, trip, entry.place_id)
                    data = ReservationCreate(
                        reservation_type=cast(ReservationType, reservation_type),
                        status="tentative",
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


def _trip_local_parts(
    day: date | str | None,
    local_time: str | None,
    zone: str | None,
    trip_timezone: str,
) -> dict[str, str] | None:
    if day is None or local_time is None or zone is None:
        return None
    try:
        instant = _source_instant(day, local_time, zone, field="source schedule")
        if instant is None:
            return None
        local = instant.astimezone(ZoneInfo(trip_timezone))
        return {"date": local.date().isoformat(), "time": local.strftime("%H:%M")}
    except (ValueError, DomainError, ZoneInfoNotFoundError):
        return None


def _duplicates(
    candidates: list[dict[str, object]], reservations: list[Reservation]
) -> list[dict[str, object]]:
    suggestions: list[dict[str, object]] = []
    for candidate in candidates:
        current = candidate["current"]
        assert isinstance(current, dict)
        provider = _normalized(current.get("provider_name"))
        code = _normalized(current.get("confirmation_code"))
        starts = None
        try:
            starts = _source_instant(
                current.get("starts_at_date"),
                current.get("starts_at_time"),
                current.get("starts_at_timezone"),
                field="start time",
            )
        except DomainError:
            pass
        for reservation in reservations:
            if provider != _normalized(reservation.provider_name):
                continue
            same_reference = code and code == _normalized(reservation.confirmation_code)
            same_schedule = (
                starts is not None
                and reservation.starts_at is not None
                and starts == as_aware_utc(reservation.starts_at)
            )
            if same_reference or same_schedule:
                suggestions.append(
                    {
                        "candidate_id": candidate["candidate_id"],
                        "reservation_id": str(reservation.id),
                        "reason": "provider_reference" if same_reference else "provider_schedule",
                        "choice_required": True,
                    }
                )
    return suggestions
