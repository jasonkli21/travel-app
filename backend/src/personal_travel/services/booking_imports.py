"""Durable extraction review and atomic booking confirmation lifecycle."""

from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, timezone
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from personal_travel.api.schemas.booking_imports import (
    ImportConfirmRequest,
    ImportEditsRequest,
)
from personal_travel.auth.contracts import PersonalAIAuthContext
from personal_travel.clients.personal_ai import (
    PersonalAIClient,
    PersonalAIExtractionError,
    PersonalAIExtractionRejected,
    PersonalAIExtractionUnknown,
)
from personal_travel.config import Settings
from personal_travel.domain.upstream_extractions import (
    UPSTREAM_REVISION,
    UpstreamBookingExtractionResult,
)
from personal_travel.models.import_source import BookingImport, SourceAttachment
from personal_travel.models.reservation import Reservation
from personal_travel.models.trip import Trip
from personal_travel.services.errors import DomainError, not_found
from personal_travel.services.private_deletion import PrivateDeletionService, enqueue_in_session
from personal_travel.services.source_lifecycle import SourceLifecycleService
from personal_travel.services.source_parser import MAX_TEXT_CHARS, SourceParseError, parse_pdf
from personal_travel.services.source_store import LocalSourceStore
from personal_travel.services.time_utils import as_aware_utc, resolve_local_datetime

CLAIM_LEASE = timedelta(seconds=60)
UPSTREAM_RESULT_RETENTION = timedelta(days=7)
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

    async def extract(
        self,
        owner_id: str,
        trip_id: UUID,
        import_id: UUID,
        *,
        request_deadline: float | None = None,
    ) -> dict[str, object]:
        if not self._settings.personal_ai_extractions_enabled:
            raise DomainError(
                "booking_extraction_unavailable",
                "Booking extraction is unavailable until enabled for this travel service.",
                status_code=503,
            )
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._settings.personal_ai_extraction_timeout_seconds
        if request_deadline is not None:
            deadline = min(deadline, request_deadline)
        try:
            async with asyncio.timeout_at(deadline):
                return await self._extract_with_deadline(owner_id, trip_id, import_id, deadline)
        except TimeoutError as error:
            raise DomainError(
                "extraction_timeout",
                "Booking extraction exceeded its total time limit.",
                status_code=408,
            ) from error

    async def _extract_with_deadline(
        self, owner_id: str, trip_id: UUID, import_id: UUID, deadline: float
    ) -> dict[str, object]:
        claim = await run_in_threadpool(self._claim, owner_id, trip_id, import_id)
        if isinstance(claim, dict):
            return claim
        try:
            text, media_type, source_hash, text_hash = await run_in_threadpool(
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
            await run_in_threadpool(self._mark_post_attempted, claim, text_hash)
        payload: dict[str, object] = {
            "schema_version": "booking-document-extraction-v1",
            "idempotency_key": str(claim.key),
            "source_sha256": text_hash,
            "media_type": media_type,
            "consent": "submit_for_booking_extraction",
            "synthetic_fixture": False,
            "document_text": text,
        }
        if claim.post_attempted:
            try:
                result = await self._client.get_booking_extraction_by_key(claim.key, text_hash)
            except PersonalAIExtractionError:
                await run_in_threadpool(self._release_claim, claim)
                return await run_in_threadpool(self._detail, owner_id, trip_id, import_id)
            if result is not None and result.state == "running":
                await run_in_threadpool(self._release_claim, claim)
                return await run_in_threadpool(self._detail, owner_id, trip_id, import_id)
            if result is None:
                # A durable pre-dispatch marker can outlive a crash before POST.
                # Re-submit the exact same payload under the existing stable key;
                # the upstream begin fence makes that safe after any lost response.
                try:
                    result = await self._client.create_booking_extraction(
                        payload=payload, idempotency_key=claim.key, source_sha256=text_hash
                    )
                except PersonalAIExtractionRejected as error:
                    await run_in_threadpool(
                        self._fail_claim, claim, f"upstream_rejected_{error.status_code}"
                    )
                    return await run_in_threadpool(self._detail, owner_id, trip_id, import_id)
                except PersonalAIExtractionError:
                    await run_in_threadpool(self._release_claim, claim)
                    return await run_in_threadpool(self._detail, owner_id, trip_id, import_id)
        else:
            try:
                result = await self._client.create_booking_extraction(
                    payload=payload, idempotency_key=claim.key, source_sha256=text_hash
                )
            except PersonalAIExtractionRejected as error:
                await run_in_threadpool(
                    self._fail_claim, claim, f"upstream_rejected_{error.status_code}"
                )
                return await run_in_threadpool(self._detail, owner_id, trip_id, import_id)
            except PersonalAIExtractionUnknown:
                await run_in_threadpool(self._release_claim, claim)
                return await run_in_threadpool(self._detail, owner_id, trip_id, import_id)
            except PersonalAIExtractionError:
                await run_in_threadpool(self._release_claim, claim)
                return await run_in_threadpool(self._detail, owner_id, trip_id, import_id)

        assert result is not None
        try:
            await run_in_threadpool(self._validate_result, result, text, claim.key, text_hash)
        except DomainError:
            await run_in_threadpool(self._fail_claim, claim, "invalid_upstream_result")
            return await run_in_threadpool(self._detail, owner_id, trip_id, import_id)
        return await run_in_threadpool(self._save_result, claim, result)

    def delete_upstream_extraction(self, owner_id: str, trip_id: UUID, import_id: UUID) -> None:
        """Retry durable upstream deletion without requiring the source row to remain."""
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
            extraction_key = item.extraction_key
            digest = item.extraction_text_sha256 or item.source_sha256
            enqueue_in_session(
                session,
                owner_id=owner_id,
                extraction_key=extraction_key,
                source_sha256=digest,
            )
        try:
            deleted = asyncio.run(
                PrivateDeletionService(self._factory, self._client).retry_one(
                    owner_id, extraction_key
                )
            )
        except RuntimeError:
            deleted = False
        if not deleted:
            raise DomainError(
                "upstream_delete_pending",
                "The local source is deleted, but AI result deletion is pending. "
                "Retry deletion to finish cleanup.",
                status_code=503,
            ) from None

    @staticmethod
    def _validate_result(
        result: UpstreamBookingExtractionResult,
        source_text: str,
        idempotency_key: UUID,
        text_hash: str,
    ) -> None:
        if result.idempotency_key != idempotency_key or result.source_sha256 != text_hash:
            raise DomainError(
                "invalid_upstream_result", "The extraction result did not match this request."
            )
        if (
            result.expires_at <= result.created_at
            or result.expires_at > result.created_at + timedelta(days=7)
            or result.created_at > datetime.now(UTC) + timedelta(minutes=1)
        ):
            raise DomainError(
                "invalid_upstream_result", "The extraction result had an invalid retention window."
            )
        if result.state != "completed":
            if result.candidates or (result.state == "failed") != (result.failure_code is not None):
                raise DomainError(
                    "invalid_upstream_result",
                    "The extraction result had an invalid terminal state.",
                )
            return
        if len(result.candidates) > 10:
            raise DomainError(
                "invalid_upstream_result", "The extraction result exceeded its limit."
            )
        identities: set[str] = set()
        spans: set[tuple[int, int]] = set()
        allowed_uncertainty = {
            "reservation_type",
            "provider_name",
            "confirmation_code",
            "starts_at",
            "ends_at",
            "starts_at_timezone",
            "ends_at_timezone",
        }
        for candidate in result.candidates:
            if (
                candidate.candidate_id in identities
                or (candidate.source_start, candidate.source_end) in spans
            ):
                raise DomainError(
                    "invalid_upstream_result", "The extraction result repeated candidate evidence."
                )
            identities.add(candidate.candidate_id)
            spans.add((candidate.source_start, candidate.source_end))
            if not set(candidate.uncertain_fields) <= allowed_uncertainty:
                raise DomainError(
                    "invalid_upstream_result", "The extraction result had unsupported uncertainty."
                )
            if not 0 <= candidate.source_start < candidate.source_end <= len(source_text):
                raise DomainError(
                    "invalid_upstream_result", "The extraction result had an invalid source span."
                )
            excerpt = source_text[candidate.source_start : candidate.source_end]
            if not excerpt.strip() or len(excerpt) > 240 or excerpt != candidate.source_excerpt:
                raise DomainError(
                    "invalid_upstream_result", "The extraction result had invalid source evidence."
                )
            if (
                (
                    candidate.reservation_type is None
                    and "reservation_type" not in candidate.uncertain_fields
                )
                or (
                    candidate.provider_name is None
                    and "provider_name" not in candidate.uncertain_fields
                )
                or (
                    candidate.confirmation_code is None
                    and "confirmation_code" not in candidate.uncertain_fields
                )
                or (
                    candidate.provider_name is not None
                    and candidate.provider_name.casefold() not in excerpt.casefold()
                )
                or (
                    candidate.confirmation_code is not None
                    and candidate.confirmation_code.casefold() not in excerpt.casefold()
                )
            ):
                raise DomainError(
                    "invalid_upstream_result",
                    "The extraction result included unsupported candidate facts.",
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
            for endpoint in ("starts_at", "ends_at"):
                day = getattr(candidate, f"{endpoint}_date")
                local_time = getattr(candidate, f"{endpoint}_time")
                timezone_name = getattr(candidate, f"{endpoint}_timezone")
                text_value = getattr(candidate, f"{endpoint}_text")
                uncertainty = endpoint
                timezone_uncertainty = f"{endpoint}_timezone"
                if day is not None or local_time is not None:
                    if not text_value or text_value.casefold() not in excerpt.casefold():
                        raise DomainError(
                            "invalid_upstream_result",
                            "A schedule value lacked literal source evidence.",
                        )
                    if (
                        timezone_name is not None
                        and timezone_name.casefold() not in excerpt.casefold()
                    ):
                        raise DomainError(
                            "invalid_upstream_result",
                            "A timezone lacked literal evidence in this candidate's source span.",
                        )
                elif uncertainty not in candidate.uncertain_fields:
                    raise DomainError(
                        "invalid_upstream_result", "An omitted schedule was not marked uncertain."
                    )
                if timezone_name is None and timezone_uncertainty not in candidate.uncertain_fields:
                    raise DomainError(
                        "invalid_upstream_result",
                        "An unresolved timezone was not marked uncertain.",
                    )

    def _claim(
        self, owner_id: str, trip_id: UUID, import_id: UUID
    ) -> ExtractionClaim | dict[str, object]:
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
            now = datetime.now(UTC)
            if item.state == "review_ready":
                return self._detail_for(item, trip.timezone, trip.reservations)
            if item.state in {"applied", "rejected", "failed"}:
                return self._detail_for(item, trip.timezone, trip.reservations)
            if (
                item.extraction_post_attempted
                and item.extraction_key is not None
                and item.extraction_key_created_at is not None
                and item.extraction_key_created_at + UPSTREAM_RESULT_RETENTION <= now
            ):
                item.state = "failed"
                item.extraction_claim_token = None
                item.extraction_claimed_at = None
                item.candidate_snapshot = {"failure_code": "upstream_result_expired"}
                if item.extraction_text_sha256 is not None:
                    enqueue_in_session(
                        session,
                        owner_id=owner_id,
                        extraction_key=item.extraction_key,
                        source_sha256=item.extraction_text_sha256,
                    )
                    item.upstream_delete_pending = True
                return self._detail_for(item, trip.timezone, trip.reservations)
            source = session.scalar(
                select(SourceAttachment).where(
                    SourceAttachment.id == item.source_id,
                    SourceAttachment.owner_id == owner_id,
                    SourceAttachment.trip_id == trip_id,
                )
            )
            if (
                source is None
                or source.state != "ready"
                or source.expires_at is None
                or source.expires_at <= now
            ):
                raise DomainError(
                    "source_unavailable", "The source is unavailable.", status_code=410
                )
            if item.extraction_claimed_at and item.extraction_claimed_at + CLAIM_LEASE > now:
                return self._detail_for(item, trip.timezone, trip.reservations)
            if item.extraction_key is None:
                item.extraction_key = uuid4()
                item.extraction_key_created_at = now
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

    def _read_source(
        self, owner_id: str, trip_id: UUID, import_id: UUID
    ) -> tuple[str, str, str, str]:
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
            text_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
            with self._factory() as session:
                persisted_hash = session.scalar(
                    select(BookingImport.extraction_text_sha256).where(
                        BookingImport.id == import_id,
                        BookingImport.trip_id == trip_id,
                        BookingImport.owner_id == owner_id,
                    )
                )
            if persisted_hash is not None and persisted_hash != text_sha256:
                raise DomainError(
                    "source_unavailable", "The recovered source text changed.", status_code=410
                )
            return text, descriptor.media_type, descriptor.sha256, text_sha256
        finally:
            store.close()

    def _mark_post_attempted(self, claim: ExtractionClaim, text_sha256: str) -> None:
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
            now = datetime.now(UTC)
            source = session.scalar(
                select(SourceAttachment).where(
                    SourceAttachment.id == item.source_id,
                    SourceAttachment.owner_id == claim.owner_id,
                    SourceAttachment.trip_id == claim.trip_id,
                )
            )
            if (
                item.state != "extracting"
                or source is None
                or source.state != "ready"
                or source.expires_at is None
                or source.expires_at <= now
            ):
                raise DomainError(
                    "source_unavailable", "The source is unavailable.", status_code=410
                )
            if item.extraction_text_sha256 not in {None, text_sha256}:
                raise DomainError(
                    "source_unavailable",
                    "The source text changed during recovery.",
                    status_code=410,
                )
            item.extraction_text_sha256 = text_sha256
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
            now = datetime.now(UTC)
            was_rejected = item.state == "rejected"
            item.extraction_claim_token = None
            item.extraction_claimed_at = None
            item.upstream_extraction_id = result.extraction_id
            item.upstream_result_expires_at = result.expires_at
            if was_rejected:
                item.candidate_snapshot = None
                if item.extraction_key is not None and item.extraction_text_sha256 is not None:
                    enqueue_in_session(
                        session,
                        owner_id=item.owner_id,
                        extraction_key=item.extraction_key,
                        source_sha256=item.extraction_text_sha256,
                    )
                    item.upstream_delete_pending = True
                return self._detail_for(item, trip.timezone, trip.reservations)
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
            elif result.state == "completed":
                item.state = "expired"
                item.candidate_snapshot = None
                item.review_revision += 1
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
            "candidates": [],
            "duplicate_suggestions": [],
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
                effective = candidate | edits.get(candidate["candidate_id"], {})
                effective["starts_at_trip_local"] = _trip_local_parts(
                    effective.get("starts_at_date"),
                    effective.get("starts_at_time"),
                    effective.get("starts_at_timezone"),
                    trip_timezone,
                )
                effective["ends_at_trip_local"] = _trip_local_parts(
                    effective.get("ends_at_date"),
                    effective.get("ends_at_time"),
                    effective.get("ends_at_timezone"),
                    trip_timezone,
                )
                candidate["current"] = effective
                views.append(candidate)
            data["candidates"] = views
            data["duplicate_suggestions"] = _duplicates(views, reservations or [])
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
            if (
                item.extraction_post_attempted
                and item.extraction_key is not None
                and item.extraction_text_sha256 is not None
            ):
                enqueue_in_session(
                    session,
                    owner_id=owner_id,
                    extraction_key=item.extraction_key,
                    source_sha256=item.extraction_text_sha256,
                )
                item.upstream_delete_pending = True
            return self._detail_for(item, trip.timezone, trip.reservations)

    def confirm(
        self, owner_id: str, trip_id: UUID, import_id: UUID, payload: ImportConfirmRequest
    ) -> dict[str, object]:
        from personal_travel.services.booking_confirmation import BookingConfirmationService

        return BookingConfirmationService(self._factory).confirm(
            owner_id, trip_id, import_id, payload
        )


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
