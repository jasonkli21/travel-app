"""Authenticated raw private-source ingress and scoped reads."""

from __future__ import annotations

import hashlib
import os
import re
from functools import partial
from uuid import UUID

from anyio.to_thread import run_sync
from fastapi import APIRouter, Header, Request, Response

from personal_travel.api.dependencies import OwnerDependency
from personal_travel.config import get_settings
from personal_travel.db.session import SessionFactory
from personal_travel.services.errors import DomainError, not_found
from personal_travel.services.source_lifecycle import (
    Registration,
    SourceLifecycleService,
)
from personal_travel.services.source_parser import MAX_TEXT_CHARS, SourceParseError, parse_pdf
from personal_travel.services.source_store import LocalSourceStore

router = APIRouter(prefix="/trips/{trip_id}/imports", tags=["imports"])
REQUEST_KEY = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
MAX_TEXT_BYTES = 1024 * 1024
MAX_PDF_BYTES = 10 * 1024 * 1024


def _gate() -> LocalSourceStore:
    settings = get_settings()
    if not settings.private_imports_enabled or settings.travel_auth_mode != "google_oidc":
        raise not_found("import")
    return LocalSourceStore(settings.private_source_dir)


def _service(request: Request) -> SourceLifecycleService:
    # Each service method opens and closes its own Session in the worker that
    # executes the synchronous operation. No ORM Session crosses threads.
    factory = getattr(request.app.state, "auth_session_factory", None) or SessionFactory
    return SourceLifecycleService(factory)


def _display_filename(filename: str | None) -> str | None:
    if not filename:
        return None
    basename = filename[:512].replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = re.sub(r"[^A-Za-z0-9 ._-]", "_", basename).strip(" ._")[:120]
    return cleaned or None


def _write_all(fd: int, chunk: bytes) -> None:
    view = memoryview(chunk)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("Source write did not make progress.")
        view = view[written:]


def _sync_close(fd: int) -> None:
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _close(fd: int) -> None:
    try:
        os.close(fd)
    except OSError:
        pass


def _verify_ready_source(
    store: LocalSourceStore, metadata: dict[str, object], object_key: str
) -> None:
    if metadata["source_state"] != "ready":
        return
    try:
        data = store.read(object_key)
    except (OSError, ValueError, KeyError) as exc:
        raise DomainError(
            "source_unavailable", "The source is unavailable.", status_code=410
        ) from exc
    if len(data) != metadata["byte_size"] or hashlib.sha256(data).hexdigest() != metadata["sha256"]:
        raise DomainError("source_unavailable", "The source is unavailable.", status_code=410)


@router.post("")
async def upload_source(
    trip_id: UUID,
    request: Request,
    owner_id: OwnerDependency,
    request_key: str = Header(alias="X-Import-Request-Key"),
    filename: str | None = Header(default=None, alias="X-Source-Filename"),
) -> dict[str, object]:
    if not REQUEST_KEY.fullmatch(request_key):
        raise DomainError("invalid_request_key", "Invalid import request key.")
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type not in {"text/plain", "application/pdf"}:
        raise DomainError("unsupported_media_type", "Use plain text or PDF.", status_code=415)

    store = await run_sync(_gate)
    source_service = _service(request)
    key = store.new_key()
    fd: int | None = None
    temp_created = False
    try:
        await run_sync(source_service.ensure_trip, owner_id, trip_id)
        fd = await run_sync(store.open_temp, key)
        temp_created = True
        limit = MAX_TEXT_BYTES if media_type == "text/plain" else MAX_PDF_BYTES
        digest = hashlib.sha256()
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > limit:
                raise DomainError(
                    "source_too_large", "The source exceeds its size limit.", status_code=413
                )
            digest.update(chunk)
            await run_sync(_write_all, fd, chunk)
        await run_sync(_sync_close, fd)
        fd = None

        if size == 0:
            raise DomainError("empty_source", "The source is empty.", status_code=400)
        source_hash = digest.hexdigest()
        if media_type == "text/plain":
            raw = await run_sync(partial(store.read, key, temp=True))
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
            if (await run_sync(partial(store.read, key, temp=True)))[:5] != b"%PDF-":
                raise DomainError("invalid_pdf", "The PDF source is invalid.", status_code=400)
            try:
                await run_sync(parse_pdf, store.temp_path(key))
            except SourceParseError as exc:
                raise DomainError(
                    exc.args[0], "The PDF cannot be imported.", status_code=400
                ) from exc

        registration: Registration = await run_sync(
            partial(
                source_service.register_upload,
                owner_id=owner_id,
                trip_id=trip_id,
                request_key=request_key,
                media_type=media_type,
                byte_size=size,
                source_hash=source_hash,
                display_filename=_display_filename(filename),
                object_key=key,
            )
        )
        if not registration.created:
            if registration.object_key is not None:
                await run_sync(
                    _verify_ready_source, store, registration.metadata, registration.object_key
                )
            return registration.metadata

        # Blob promotion stays outside SQL. A failed state update leaves the
        # pending row and promoted bytes for deterministic cleanup recovery.
        await run_sync(store.promote, key)
        source_id = UUID(str(registration.metadata["source_id"]))
        return await run_sync(
            source_service.mark_ready, owner_id, UUID(str(registration.metadata["id"])), source_id
        )
    finally:
        if fd is not None:
            await run_sync(_close, fd)
        if temp_created:
            await run_sync(partial(store.delete, key, temp=True))
        await run_sync(store.close)


@router.get("/{import_id}")
def get_import(
    trip_id: UUID, import_id: UUID, request: Request, owner_id: OwnerDependency
) -> dict[str, object]:
    store = _gate()
    try:
        metadata, descriptor = _service(request).get_import(owner_id, trip_id, import_id)
        if descriptor is not None and metadata["source_state"] == "ready":
            _verify_ready_source(store, metadata, descriptor.object_key)
        return metadata
    finally:
        store.close()


@router.get("/{import_id}/source")
def download_source(
    trip_id: UUID, import_id: UUID, request: Request, owner_id: OwnerDependency
) -> Response:
    store = _gate()
    try:
        descriptor = _service(request).download_descriptor(owner_id, trip_id, import_id)
        try:
            data = store.read(descriptor.object_key)
        except (OSError, ValueError) as exc:
            raise DomainError(
                "source_unavailable", "The source is unavailable.", status_code=410
            ) from exc
        if (
            len(data) != descriptor.byte_size
            or hashlib.sha256(data).hexdigest() != descriptor.sha256
        ):
            raise DomainError("source_unavailable", "The source is unavailable.", status_code=410)
        return Response(
            data,
            media_type=descriptor.media_type,
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
    trip_id: UUID, import_id: UUID, request: Request, owner_id: OwnerDependency
) -> Response:
    store = _gate()
    source_service = _service(request)
    try:
        object_key = source_service.begin_delete(owner_id, trip_id, import_id)
        if object_key is not None:
            store.delete(object_key)
            store.delete(object_key, temp=True)
            source_service.finish_delete(object_key)
        return Response(status_code=204)
    finally:
        store.close()
