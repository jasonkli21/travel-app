"""Authenticated raw private-source ingress and scoped reads."""

from __future__ import annotations

import hashlib
import os
import re
from datetime import UTC, datetime, timedelta
from uuid import UUID

from anyio.to_thread import run_sync
from fastapi import APIRouter, Header, Request, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from personal_travel.api.dependencies import OwnerDependency, SessionDependency
from personal_travel.config import get_settings
from personal_travel.models.import_source import BookingImport, SourceAttachment
from personal_travel.models.trip import Trip
from personal_travel.services.errors import DomainError, not_found
from personal_travel.services.source_parser import MAX_TEXT_CHARS, SourceParseError, parse_pdf
from personal_travel.services.source_store import LocalSourceStore

router = APIRouter(prefix="/trips/{trip_id}/imports", tags=["imports"])
REQUEST_KEY = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
MAX_TEXT_BYTES = 1024 * 1024
MAX_PDF_BYTES = 10 * 1024 * 1024
SOURCE_RETENTION = timedelta(days=7)


def _gate() -> LocalSourceStore:
    settings = get_settings()
    if not settings.private_imports_enabled or settings.travel_auth_mode != "google_oidc":
        raise not_found("import")
    return LocalSourceStore(settings.private_source_dir)


def _metadata(item: BookingImport, source: SourceAttachment) -> dict[str, object]:
    return {
        "id": str(item.id),
        "trip_id": str(item.trip_id),
        "state": item.state,
        "source_id": str(source.id),
        "source_state": source.state,
        "media_type": source.media_type,
        "byte_size": source.byte_size,
        "sha256": source.sha256,
        "display_filename": source.display_filename,
        "review_revision": item.review_revision,
    }


def _display_filename(filename: str | None) -> str | None:
    if not filename:
        return None
    basename = filename[:512].replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = re.sub(r"[^A-Za-z0-9 ._-]", "_", basename).strip(" ._")[:120]
    return cleaned or None


def _source_exists(store: LocalSourceStore, source: SourceAttachment) -> bool:
    try:
        return store.exists(source.object_key)
    except (OSError, ValueError):
        return False


def _load(
    session: SessionDependency, owner_id: str, trip_id: UUID, import_id: UUID
) -> tuple[BookingImport, SourceAttachment]:
    row = session.scalar(
        select(BookingImport).where(
            BookingImport.id == import_id,
            BookingImport.trip_id == trip_id,
            BookingImport.owner_id == owner_id,
        )
    )
    if row is None:
        raise not_found("import")
    source = session.scalar(
        select(SourceAttachment).where(
            SourceAttachment.id == row.source_id,
            SourceAttachment.owner_id == owner_id,
            SourceAttachment.trip_id == trip_id,
        )
    )
    if source is None:
        raise not_found("source")
    return row, source


@router.post("")
async def upload_source(
    trip_id: UUID,
    request: Request,
    session: SessionDependency,
    owner_id: OwnerDependency,
    request_key: str = Header(alias="X-Import-Request-Key"),
    filename: str | None = Header(default=None, alias="X-Source-Filename"),
) -> dict[str, object]:
    store = _gate()
    key = store.new_key()
    temp_created = False
    try:
        if not REQUEST_KEY.fullmatch(request_key):
            raise DomainError("invalid_request_key", "Invalid import request key.")
        trip = session.scalar(select(Trip.id).where(Trip.id == trip_id, Trip.owner_id == owner_id))
        if trip is None:
            raise not_found("trip")
        session.rollback()  # Release the owner/trip read before streaming or parsing.
        media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if media_type not in {"text/plain", "application/pdf"}:
            raise DomainError("unsupported_media_type", "Use plain text or PDF.", status_code=415)
        limit = MAX_TEXT_BYTES if media_type == "text/plain" else MAX_PDF_BYTES
        digest = hashlib.sha256()
        size = 0
        fd = store.open_temp(key)
        temp_created = True
        try:
            with os.fdopen(fd, "wb") as stream:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > limit:
                        raise DomainError(
                            "source_too_large",
                            "The source exceeds its size limit.",
                            status_code=413,
                        )
                    digest.update(chunk)
                    stream.write(chunk)
                stream.flush()
                os.fsync(stream.fileno())
            if size == 0:
                raise DomainError("empty_source", "The source is empty.", status_code=400)
            if media_type == "text/plain":
                raw = store.read(key, temp=True)
                try:
                    source_text = raw.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise DomainError(
                        "invalid_text", "Plain text must use UTF-8.", status_code=400
                    ) from exc
                if (
                    len(source_text) > MAX_TEXT_CHARS
                    or not source_text.strip()
                    or raw.startswith(b"%PDF-")
                ):
                    raise DomainError(
                        "invalid_text",
                        "The source text is empty, mislabeled or too long.",
                        status_code=400,
                    )
            else:
                if store.read(key, temp=True)[:5] != b"%PDF-":
                    raise DomainError("invalid_pdf", "The PDF source is invalid.", status_code=400)
                try:
                    await run_sync(parse_pdf, store.temp_path(key))
                except SourceParseError as exc:
                    raise DomainError(
                        exc.args[0], "The PDF cannot be imported.", status_code=400
                    ) from exc

            source_hash = digest.hexdigest()
            fingerprint = hashlib.sha256(f"{media_type}\0{source_hash}".encode()).hexdigest()
            existing = session.scalar(
                select(BookingImport).where(
                    BookingImport.owner_id == owner_id,
                    BookingImport.trip_id == trip_id,
                    BookingImport.request_key == request_key,
                )
            )
            if existing is None:
                existing = session.scalar(
                    select(BookingImport).where(
                        BookingImport.owner_id == owner_id,
                        BookingImport.trip_id == trip_id,
                        BookingImport.source_sha256 == source_hash,
                    )
                )
            if existing is not None:
                if (
                    existing.request_key == request_key
                    and existing.request_fingerprint != fingerprint
                ):
                    raise DomainError(
                        "request_key_conflict",
                        "The request key already identifies a different source.",
                        status_code=409,
                    )
                old_source = session.scalar(
                    select(SourceAttachment).where(
                        SourceAttachment.id == existing.source_id,
                        SourceAttachment.owner_id == owner_id,
                        SourceAttachment.trip_id == trip_id,
                    )
                )
                if old_source is None or old_source.state not in {"ready", "pending"}:
                    raise DomainError(
                        "source_unavailable", "The previous source is unavailable.", status_code=409
                    )
                if old_source.state == "ready" and not _source_exists(store, old_source):
                    raise DomainError(
                        "source_unavailable", "The previous source is unavailable.", status_code=409
                    )
                return _metadata(existing, old_source)

            source = SourceAttachment(
                owner_id=owner_id,
                trip_id=trip_id,
                object_key=key,
                sha256=source_hash,
                media_type=media_type,
                byte_size=size,
                display_filename=_display_filename(filename),
                state="pending",
                expires_at=datetime.now(UTC) + SOURCE_RETENTION,
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
                state="received",
                parser_version="source-v1",
                review_revision=0,
            )
            session.add(item)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                winner = session.scalar(
                    select(BookingImport).where(
                        BookingImport.owner_id == owner_id,
                        BookingImport.trip_id == trip_id,
                        BookingImport.request_key == request_key,
                    )
                )
                if winner is None:
                    winner = session.scalar(
                        select(BookingImport).where(
                            BookingImport.owner_id == owner_id,
                            BookingImport.trip_id == trip_id,
                            BookingImport.source_sha256 == source_hash,
                        )
                    )
                if winner is None or (
                    winner.request_key == request_key and winner.request_fingerprint != fingerprint
                ):
                    raise DomainError(
                        "request_key_conflict",
                        "The source conflicts with another import.",
                        status_code=409,
                    ) from None
                old_source = session.scalar(
                    select(SourceAttachment).where(
                        SourceAttachment.id == winner.source_id,
                        SourceAttachment.owner_id == owner_id,
                        SourceAttachment.trip_id == trip_id,
                    )
                )
                if old_source is None or old_source.state not in {"ready", "pending"}:
                    raise DomainError(
                        "source_unavailable", "The previous source is unavailable.", status_code=409
                    ) from None
                if old_source.state == "ready" and not _source_exists(store, old_source):
                    raise DomainError(
                        "source_unavailable", "The previous source is unavailable.", status_code=409
                    ) from None
                return _metadata(winner, old_source)

            # The short pending row is durable before promotion. A crash between
            # these operations is reconciled by the bounded cleanup command.
            try:
                store.promote(key)
                source.state = "ready"
                session.commit()
            except Exception:
                session.rollback()
                # Best-effort compensation leaves either a pending row for
                # cleanup or an unreferenced opaque object that cleanup removes.
                try:
                    session.query(BookingImport).filter(BookingImport.id == item.id).delete()
                    session.query(SourceAttachment).filter(
                        SourceAttachment.id == source.id
                    ).delete()
                    session.commit()
                    store.delete(key)
                except Exception:
                    session.rollback()
                raise
            return _metadata(item, source)
        finally:
            if temp_created:
                store.delete(key, temp=True)
    finally:
        store.close()


@router.get("/{import_id}")
def get_import(
    trip_id: UUID, import_id: UUID, session: SessionDependency, owner_id: OwnerDependency
) -> dict[str, object]:
    store = _gate()
    try:
        item, source = _load(session, owner_id, trip_id, import_id)
        if source.state == "ready" and not _source_exists(store, source):
            raise DomainError("source_unavailable", "The source is unavailable.", status_code=410)
        return _metadata(item, source)
    finally:
        store.close()


@router.get("/{import_id}/source")
def download_source(
    trip_id: UUID, import_id: UUID, session: SessionDependency, owner_id: OwnerDependency
) -> Response:
    store = _gate()
    try:
        _, source = _load(session, owner_id, trip_id, import_id)
        if source.state != "ready":
            raise not_found("source")
        try:
            data = store.read(source.object_key)
        except (OSError, ValueError) as exc:
            raise DomainError(
                "source_unavailable", "The source is unavailable.", status_code=410
            ) from exc
        if hashlib.sha256(data).hexdigest() != source.sha256:
            raise DomainError("source_unavailable", "The source is unavailable.", status_code=410)
        return Response(
            data,
            media_type=source.media_type,
            headers={
                "Content-Disposition": "attachment; filename=source",
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "no-store",
            },
        )
    finally:
        store.close()


@router.delete("/{import_id}/source", status_code=204)
def delete_source(
    trip_id: UUID, import_id: UUID, session: SessionDependency, owner_id: OwnerDependency
) -> Response:
    store = _gate()
    try:
        _, source = _load(session, owner_id, trip_id, import_id)
        source.state = "deleting"
        session.commit()
        store.delete(source.object_key)
        return Response(status_code=204)
    finally:
        store.close()
