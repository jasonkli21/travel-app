"""Durable source import identity and conditional lifecycle transitions."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from personal_travel.models.import_source import BookingImport, SourceAttachment
from personal_travel.models.trip import Trip
from personal_travel.services.errors import DomainError, not_found

SOURCE_RETENTION = timedelta(days=7)
SessionFactoryLike = Callable[[], Session]


@dataclass(frozen=True, slots=True)
class Registration:
    metadata: dict[str, object]
    created: bool
    object_key: str | None = None


@dataclass(frozen=True, slots=True)
class SourceDescriptor:
    object_key: str
    sha256: str
    media_type: str
    byte_size: int
    expires_at: datetime
    state: str


def import_metadata(item: BookingImport, source: SourceAttachment | None) -> dict[str, object]:
    now = datetime.now(UTC)
    source_state = "deleted" if source is None else source.state
    display_filename = None
    source_id = None
    if source is not None:
        source_id = str(source.id)
        display_filename = source.display_filename
        if source.state == "ready" and source.expires_at <= now:
            source_state = "expired"
    return {
        "id": str(item.id),
        "trip_id": str(item.trip_id),
        "state": (
            "expired"
            if item.state == "review_ready"
            and item.upstream_result_expires_at is not None
            and item.upstream_result_expires_at <= now
            else item.state
        ),
        "source_id": source_id,
        "source_state": source_state,
        "media_type": item.source_media_type,
        "byte_size": item.source_byte_size,
        "sha256": item.source_sha256,
        "display_filename": display_filename,
        "review_revision": item.review_revision,
        "retention_choice": item.retention_choice,
        "extraction_state": item.state,
        "extraction_key": str(item.extraction_key) if item.extraction_key else None,
        "upstream_extraction_id": (
            str(item.upstream_extraction_id) if item.upstream_extraction_id else None
        ),
        "upstream_revision": item.upstream_revision,
        "upstream_result_expires_at": (
            item.upstream_result_expires_at.isoformat() if item.upstream_result_expires_at else None
        ),
        "candidates": (
            item.candidate_snapshot
            if item.upstream_result_expires_at is None or item.upstream_result_expires_at > now
            else None
        ),
        "confirmation_outcome": item.confirmation_outcome,
    }


class SourceLifecycleService:
    """Own short SQL sessions for source lifecycle operations."""

    def __init__(self, session_factory: SessionFactoryLike) -> None:
        self._session_factory = session_factory

    def ensure_trip(self, owner_id: str, trip_id: UUID) -> None:
        with self._session_factory() as session:
            found = session.scalar(
                select(Trip.id).where(Trip.id == trip_id, Trip.owner_id == owner_id)
            )
        if found is None:
            raise not_found("trip")

    def register_upload(
        self,
        *,
        owner_id: str,
        trip_id: UUID,
        request_key: str,
        media_type: str,
        byte_size: int,
        source_hash: str,
        display_filename: str | None,
        object_key: str,
        retention_choice: str = "delete_after_confirmation",
    ) -> Registration:
        fingerprint = hashlib.sha256(
            f"{media_type}\0{source_hash}\0{retention_choice}".encode()
        ).hexdigest()
        try:
            with self._session_factory() as session:
                existing_key = self._by_request_key(session, owner_id, trip_id, request_key)
                if existing_key is not None:
                    if existing_key.request_fingerprint != fingerprint:
                        raise self._key_conflict()
                    source = self._source(session, existing_key, owner_id, trip_id)
                    return Registration(
                        import_metadata(existing_key, source),
                        created=False,
                        object_key=None if source is None else source.object_key,
                    )

                duplicate = session.scalar(
                    select(BookingImport).where(
                        BookingImport.owner_id == owner_id,
                        BookingImport.trip_id == trip_id,
                        BookingImport.source_sha256 == source_hash,
                    )
                )
                if duplicate is not None:
                    raise DomainError(
                        "source_already_imported",
                        "This source was already submitted. Retry with its original request "
                        "key to recover that import.",
                        status_code=409,
                    )

                now = datetime.now(UTC)
                source = SourceAttachment(
                    owner_id=owner_id,
                    trip_id=trip_id,
                    object_key=object_key,
                    sha256=source_hash,
                    media_type=media_type,
                    byte_size=byte_size,
                    display_filename=display_filename,
                    state="pending",
                    expires_at=now + SOURCE_RETENTION,
                )
                session.add(source)
                session.flush()
                item = BookingImport(
                    owner_id=owner_id,
                    trip_id=trip_id,
                    source_id=source.id,
                    request_key=request_key,
                    request_fingerprint=fingerprint,
                    source_sha256=source_hash,
                    source_media_type=media_type,
                    source_byte_size=byte_size,
                    retention_choice=retention_choice,
                    state="received",
                    parser_version="source-v1",
                    review_revision=0,
                )
                session.add(item)
                session.commit()
                return Registration(
                    import_metadata(item, source), created=True, object_key=object_key
                )
        except IntegrityError:
            # Unique SQL keys arbitrate simultaneous retries. Re-read in a fresh
            # transaction and apply the same key/hash conflict rules.
            with self._session_factory() as session:
                existing_key = self._by_request_key(session, owner_id, trip_id, request_key)
                if existing_key is not None:
                    if existing_key.request_fingerprint != fingerprint:
                        raise self._key_conflict() from None
                    source = self._source(session, existing_key, owner_id, trip_id)
                    return Registration(
                        import_metadata(existing_key, source),
                        created=False,
                        object_key=None if source is None else source.object_key,
                    )
                duplicate = session.scalar(
                    select(BookingImport).where(
                        BookingImport.owner_id == owner_id,
                        BookingImport.trip_id == trip_id,
                        BookingImport.source_sha256 == source_hash,
                    )
                )
                if duplicate is not None:
                    raise DomainError(
                        "source_already_imported",
                        "This source was already submitted. Retry with its original request "
                        "key to recover that import.",
                        status_code=409,
                    ) from None
            raise

    def mark_ready(self, owner_id: str, import_id: UUID, source_id: UUID) -> dict[str, object]:
        now = datetime.now(UTC)
        with self._session_factory() as session, session.begin():
            result = session.execute(
                update(SourceAttachment)
                .where(
                    SourceAttachment.id == source_id,
                    SourceAttachment.owner_id == owner_id,
                    SourceAttachment.state == "pending",
                    SourceAttachment.expires_at > now,
                )
                .values(state="ready", updated_at=now)
                .returning(SourceAttachment.id)
            )
            if result.scalar_one_or_none() is None:
                raise DomainError(
                    "source_transition_conflict",
                    "The source changed while it was being saved. Retry with the same request key.",
                    status_code=409,
                )
            item = session.scalar(
                select(BookingImport).where(
                    BookingImport.id == import_id,
                    BookingImport.owner_id == owner_id,
                )
            )
            source = session.scalar(
                select(SourceAttachment).where(SourceAttachment.id == source_id)
            )
            if item is None or source is None:
                raise DomainError(
                    "source_transition_conflict",
                    "The source changed while it was being saved. Retry with the same request key.",
                    status_code=409,
                )
            return import_metadata(item, source)

    def get_import(
        self, owner_id: str, trip_id: UUID, import_id: UUID
    ) -> tuple[dict[str, object], SourceDescriptor | None]:
        with self._session_factory() as session:
            item = session.scalar(
                select(BookingImport).where(
                    BookingImport.id == import_id,
                    BookingImport.trip_id == trip_id,
                    BookingImport.owner_id == owner_id,
                )
            )
            if item is None:
                raise not_found("import")
            source = self._source(session, item, owner_id, trip_id)
            descriptor = (
                None
                if source is None
                else SourceDescriptor(
                    object_key=source.object_key,
                    sha256=source.sha256,
                    media_type=source.media_type,
                    byte_size=source.byte_size,
                    expires_at=source.expires_at,
                    state=source.state,
                )
            )
            return import_metadata(item, source), descriptor

    def list_imports(self, owner_id: str, trip_id: UUID) -> list[dict[str, object]]:
        with self._session_factory() as session:
            found = session.scalar(
                select(Trip.id).where(Trip.id == trip_id, Trip.owner_id == owner_id)
            )
            if found is None:
                raise not_found("trip")
            items = list(
                session.scalars(
                    select(BookingImport)
                    .where(BookingImport.owner_id == owner_id, BookingImport.trip_id == trip_id)
                    .order_by(BookingImport.created_at.desc(), BookingImport.id)
                    .limit(100)
                )
            )
            return [
                import_metadata(item, self._source(session, item, owner_id, trip_id))
                for item in items
            ]

    def download_descriptor(
        self, owner_id: str, trip_id: UUID, import_id: UUID
    ) -> SourceDescriptor:
        metadata, descriptor = self.get_import(owner_id, trip_id, import_id)
        if descriptor is None or metadata["source_state"] == "deleted":
            raise not_found("source")
        if descriptor.state != "ready" or descriptor.expires_at <= datetime.now(UTC):
            raise DomainError("source_unavailable", "The source is unavailable.", status_code=410)
        return descriptor

    def begin_delete(self, owner_id: str, trip_id: UUID, import_id: UUID) -> str | None:
        with self._session_factory() as session, session.begin():
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
            source_id = item.source_id
            if source_id is None:
                return None
            result = session.execute(
                update(SourceAttachment)
                .where(
                    SourceAttachment.id == source_id,
                    SourceAttachment.owner_id == owner_id,
                    SourceAttachment.trip_id == trip_id,
                    SourceAttachment.state.in_(("pending", "ready", "deleting")),
                )
                .values(state="deleting", updated_at=datetime.now(UTC))
                .returning(SourceAttachment.object_key)
            )
            object_key = result.scalar_one_or_none()
            if object_key is not None:
                return object_key
            # A competing delete may have finalized the source after our read.
            current = session.get(SourceAttachment, source_id)
            return None if current is None else current.object_key

    def finish_delete(self, object_key: str) -> None:
        with self._session_factory() as session, session.begin():
            source = session.scalar(
                select(SourceAttachment).where(
                    SourceAttachment.object_key == object_key,
                    SourceAttachment.state == "deleting",
                )
            )
            if source is not None:
                if source.trip_id is not None:
                    session.scalar(select(Trip).where(Trip.id == source.trip_id).with_for_update())
                item = session.scalar(
                    select(BookingImport)
                    .where(BookingImport.source_id == source.id)
                    .with_for_update()
                )
                if item is not None:
                    item.source_id = None
                    item.candidate_snapshot = None
                    if item.extraction_key is not None and item.extraction_post_attempted:
                        item.upstream_delete_pending = True
                    item.extraction_claim_token = None
                    item.extraction_claimed_at = None
                    if item.state not in {"applied", "rejected", "failed", "expired"}:
                        item.state = "expired"
                        item.candidate_snapshot = {"failure_code": "source_deleted"}
                        item.review_revision += 1
                    item.updated_at = datetime.now(UTC)
                session.execute(
                    delete(SourceAttachment).where(
                        SourceAttachment.id == source.id,
                        SourceAttachment.state == "deleting",
                    )
                )

    @staticmethod
    def _by_request_key(
        session: Session, owner_id: str, trip_id: UUID, request_key: str
    ) -> BookingImport | None:
        return session.scalar(
            select(BookingImport).where(
                BookingImport.owner_id == owner_id,
                BookingImport.trip_id == trip_id,
                BookingImport.request_key == request_key,
            )
        )

    @staticmethod
    def _source(
        session: Session, item: BookingImport, owner_id: str, trip_id: UUID
    ) -> SourceAttachment | None:
        if item.source_id is None:
            return None
        return session.scalar(
            select(SourceAttachment).where(
                SourceAttachment.id == item.source_id,
                SourceAttachment.owner_id == owner_id,
                SourceAttachment.trip_id == trip_id,
            )
        )

    @staticmethod
    def _key_conflict() -> DomainError:
        return DomainError(
            "request_key_conflict",
            "The request key already identifies a different source.",
            status_code=409,
        )
