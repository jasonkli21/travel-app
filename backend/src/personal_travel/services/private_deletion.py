"""Bounded, owner-scoped cleanup of upstream private extraction results."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from personal_travel.clients.personal_ai import (
    PersonalAIClient,
    PersonalAIExtractionError,
)
from personal_travel.config import get_settings
from personal_travel.models.import_source import BookingDeletionIntent, BookingImport
from personal_travel.models.trip import Trip
from personal_travel.services.provider_admission import (
    ProviderAdmissionUnavailable,
    QuotaExceeded,
    admit_provider_request,
)

MAX_DELETION_RETRIES = 10
MAX_DELETION_RETRY_SECONDS = 20.0


def enqueue_in_session(
    session: Session, *, owner_id: str, extraction_key: UUID, source_sha256: str
) -> BookingDeletionIntent:
    intent = session.scalar(
        select(BookingDeletionIntent)
        .where(
            BookingDeletionIntent.owner_id == owner_id,
            BookingDeletionIntent.extraction_key == extraction_key,
        )
        .with_for_update()
    )
    if intent is None:
        intent = BookingDeletionIntent(
            owner_id=owner_id,
            extraction_key=extraction_key,
            source_sha256=source_sha256,
        )
        session.add(intent)
        session.flush()
    elif intent.source_sha256 != source_sha256:
        raise ValueError("A deletion key cannot change its source digest.")
    return intent


class PrivateDeletionService:
    def __init__(self, session_factory: Callable[[], Session], client: PersonalAIClient) -> None:
        self._factory = session_factory
        self._client = client

    async def retry_pending(
        self, owner_id: str, *, limit: int = MAX_DELETION_RETRIES
    ) -> dict[str, int]:
        if not 1 <= limit <= MAX_DELETION_RETRIES:
            raise ValueError("Deletion retry limit must be between 1 and 10.")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + MAX_DELETION_RETRY_SECONDS
        intents = await asyncio.to_thread(self._pending, owner_id, limit)
        attempted = 0
        deleted = 0
        failed = 0
        for intent_id, extraction_key, source_sha256 in intents:
            if loop.time() >= deadline:
                break
            try:
                await asyncio.to_thread(self._admit, owner_id)
            except (QuotaExceeded, ProviderAdmissionUnavailable):
                failed += 1
                break
            attempted += 1
            try:
                async with asyncio.timeout_at(deadline):
                    result = await self._client.delete_booking_extraction_by_key(
                        extraction_key, source_sha256
                    )
            except TimeoutError:
                failed += 1
                break
            except PersonalAIExtractionError:
                failed += 1
                continue
            try:
                if result.state != "deleted":
                    failed += 1
                    continue
                async with asyncio.timeout_at(deadline):
                    await asyncio.to_thread(self._finish, owner_id, intent_id, extraction_key)
            except TimeoutError:
                failed += 1
                break
            deleted += 1
        pending = await asyncio.to_thread(self._pending_count, owner_id)
        return {"attempted": attempted, "deleted": deleted, "failed": failed, "pending": pending}

    async def retry_one(self, owner_id: str, extraction_key: UUID) -> bool:
        intent = await asyncio.to_thread(self._pending_one, owner_id, extraction_key)
        if intent is None:
            return True
        intent_id, key, source_sha256 = intent
        await asyncio.to_thread(self._admit, owner_id)
        try:
            result = await self._client.delete_booking_extraction_by_key(key, source_sha256)
        except PersonalAIExtractionError:
            return False
        if result.state != "deleted":
            return False
        await asyncio.to_thread(self._finish, owner_id, intent_id, key)
        return True

    def _admit(self, owner_id: str) -> None:
        settings = get_settings()
        admit_provider_request(
            self._factory,
            owner_id=owner_id,
            operation="personal_ai_deletion",
            global_per_minute=settings.personal_ai_global_requests_per_minute,
            global_per_day=settings.personal_ai_global_requests_per_day,
        )

    def _pending(self, owner_id: str, limit: int) -> list[tuple[UUID, UUID, str]]:
        with self._factory() as session:
            rows = session.execute(
                select(
                    BookingDeletionIntent.id,
                    BookingDeletionIntent.extraction_key,
                    BookingDeletionIntent.source_sha256,
                )
                .where(BookingDeletionIntent.owner_id == owner_id)
                .order_by(BookingDeletionIntent.created_at, BookingDeletionIntent.id)
                .limit(limit)
            )
            return [(intent_id, key, digest) for intent_id, key, digest in rows]

    def _finish(self, owner_id: str, intent_id: UUID, key: UUID) -> None:
        with self._factory() as session, session.begin():
            item_stub = session.scalar(
                select(BookingImport).where(
                    BookingImport.owner_id == owner_id,
                    BookingImport.extraction_key == key,
                )
            )
            if item_stub is not None:
                trip = session.scalar(
                    select(Trip)
                    .where(Trip.id == item_stub.trip_id, Trip.owner_id == owner_id)
                    .with_for_update()
                )
                item = None
                if trip is not None:
                    item = session.scalar(
                        select(BookingImport)
                        .where(
                            BookingImport.owner_id == owner_id,
                            BookingImport.trip_id == trip.id,
                            BookingImport.extraction_key == key,
                        )
                        .with_for_update()
                    )
                if item is not None:
                    item.upstream_delete_pending = False
            intent = session.scalar(
                select(BookingDeletionIntent)
                .where(
                    BookingDeletionIntent.id == intent_id,
                    BookingDeletionIntent.owner_id == owner_id,
                    BookingDeletionIntent.extraction_key == key,
                )
                .with_for_update()
            )
            if intent is not None:
                session.delete(intent)

    def _pending_count(self, owner_id: str) -> int:
        with self._factory() as session:
            return int(
                session.scalar(
                    select(func.count()).select_from(
                        select(BookingDeletionIntent.id)
                        .where(BookingDeletionIntent.owner_id == owner_id)
                        .limit(101)
                        .subquery()
                    )
                )
                or 0
            )

    def _pending_one(self, owner_id: str, key: UUID) -> tuple[UUID, UUID, str] | None:
        with self._factory() as session:
            row = session.execute(
                select(
                    BookingDeletionIntent.id,
                    BookingDeletionIntent.extraction_key,
                    BookingDeletionIntent.source_sha256,
                ).where(
                    BookingDeletionIntent.owner_id == owner_id,
                    BookingDeletionIntent.extraction_key == key,
                )
            ).one_or_none()
            return None if row is None else (row[0], row[1], row[2])
