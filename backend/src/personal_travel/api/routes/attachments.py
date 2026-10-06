"""Private, owner-scoped trip-document upload, metadata, and download routes."""

from __future__ import annotations

import hashlib
import os
import re
from functools import partial
from typing import Annotated, Literal
from uuid import UUID

from anyio.to_thread import run_sync
from fastapi import APIRouter, Header, Request, Response, status

from personal_travel.api.dependencies import OwnerDependency
from personal_travel.api.schemas.attachments import AttachmentResponse, AttachmentUpdateRequest
from personal_travel.config import get_settings
from personal_travel.db.session import SessionFactory
from personal_travel.services.attachment_validation import (
    ALLOWED_ATTACHMENT_TYPES,
    MAX_ATTACHMENT_BYTES,
    MAX_ATTACHMENT_TEXT_BYTES,
    AttachmentValidationError,
    validate_attachment,
)
from personal_travel.services.attachments import AttachmentService, upload_fingerprint
from personal_travel.services.errors import DomainError, not_found
from personal_travel.services.source_store import LocalSourceStore

router = APIRouter(prefix="/trips/{trip_id}/attachments", tags=["attachments"])
REQUEST_KEY = re.compile(r"^[A-Za-z0-9_-]{8,128}$")


def _gate() -> LocalSourceStore:
    settings = get_settings()
    if not settings.private_attachments_enabled or settings.travel_auth_mode != "google_oidc":
        raise not_found("attachment")
    return LocalSourceStore(settings.private_source_dir)


def _service(request: Request) -> AttachmentService:
    factory = getattr(request.app.state, "auth_session_factory", None) or SessionFactory
    return AttachmentService(factory)


def _display_filename(filename: str | None) -> str:
    if not filename:
        return "travel-document"
    basename = filename[:512].replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = re.sub(r"[^A-Za-z0-9 ._-]", "_", basename).strip(" ._")[:120]
    return cleaned or "travel-document"


def _write_all(fd: int, chunk: bytes) -> None:
    view = memoryview(chunk)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("Document write did not make progress.")
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


def _matches(
    store: LocalSourceStore, key: str, digest: str, byte_size: int, *, temp: bool = False
) -> bool:
    return store.matches(key, digest, byte_size, temp=temp)


def _promote_or_recover(
    store: LocalSourceStore,
    key: str,
    data: bytes,
    digest: str,
    byte_size: int,
    service: AttachmentService,
    owner_id: str,
    trip_id: UUID,
    attachment_id: UUID,
    *,
    lock_fd: int | None = None,
) -> tuple[Literal["ready", "busy", "unavailable"], dict[str, object] | None]:
    """Publish/recover bytes while holding the per-object lifecycle lock."""
    owns_lock = lock_fd is None
    if lock_fd is None:
        lock_fd = store.try_promotion_lock(key)
        if lock_fd is None:
            return "busy", None
    try:
        if not _matches(store, key, digest, byte_size):
            if store.entry_stat(key) is not None:
                # A final object is immutable. Never replace a corrupt or unrelated
                # object with retry bytes under an existing metadata row.
                return "unavailable", None
            if not _matches(store, key, digest, byte_size, temp=True):
                if store.entry_stat(key + ".tmp") is not None:
                    store.delete(key, temp=True)
                store.write_temp(key, data)
            try:
                store.promote(key)
            except OSError:
                # Another lifecycle transition may have completed the hard link
                # before the unlink/fsync failed. Accept only the verified final.
                if not _matches(store, key, digest, byte_size):
                    return "unavailable", None
        if not _matches(store, key, digest, byte_size):
            # A final object is immutable. Never replace a corrupt or unrelated
            # object with retry bytes under an existing metadata row.
            return "unavailable", None
        metadata = service.mark_ready(owner_id, trip_id, attachment_id)
        return "ready", metadata
    finally:
        if owns_lock:
            store.release_promotion_lock(lock_fd)


def _headers_for_download(filename: str) -> dict[str, str]:
    # Filenames are reduced to a conservative ASCII allowlist before reaching
    # this formatter, so they cannot add headers or alter Content-Disposition.
    return {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "no-store",
    }


@router.get("", response_model=list[AttachmentResponse])
def list_attachments(
    trip_id: UUID, request: Request, owner_id: OwnerDependency
) -> list[AttachmentResponse]:
    store = _gate()
    try:
        result: list[AttachmentResponse] = []
        for raw in _service(request).list(owner_id, trip_id):
            item = dict(raw)
            key = item.pop("_object_key")
            digest = item.pop("_sha256")
            state = item["state"]
            available = False
            if (
                state == "ready"
                and isinstance(key, str)
                and isinstance(digest, str)
                and isinstance(item["byte_size"], int)
            ):
                available = _matches(store, key, digest, item["byte_size"])
                if not available:
                    item["state"] = "missing"
            item["download_available"] = available
            result.append(AttachmentResponse.model_validate(item))
        return result
    finally:
        store.close()


@router.post("", response_model=AttachmentResponse, status_code=status.HTTP_201_CREATED)
async def upload_attachment(
    trip_id: UUID,
    request: Request,
    owner_id: OwnerDependency,
    expected_revision: Annotated[int, Header(alias="X-Expected-Revision", ge=0)],
    request_key: Annotated[str, Header(alias="X-Attachment-Request-Key")],
    filename: Annotated[str | None, Header(alias="X-Attachment-Filename")] = None,
    reservation_id: Annotated[UUID | None, Header(alias="X-Attachment-Reservation-ID")] = None,
) -> AttachmentResponse:
    if not REQUEST_KEY.fullmatch(request_key):
        raise DomainError("invalid_request_key", "Invalid attachment request key.")
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type not in ALLOWED_ATTACHMENT_TYPES:
        raise DomainError(
            "unsupported_media_type",
            "Use UTF-8 text, PDF, JPEG, or PNG.",
            status_code=415,
        )

    store = await run_sync(_gate)
    service = _service(request)
    safe_filename = _display_filename(filename)
    key = store.new_key()
    fd: int | None = None
    promotion_lock_fd: int | None = None
    registered_key: str | None = None
    try:
        # Scope checks precede the first read from the streamed request body.
        await run_sync(service.ensure_target, owner_id, trip_id, reservation_id)
        promotion_lock_fd = await run_sync(store.try_promotion_lock, key)
        if promotion_lock_fd is None:
            raise DomainError(
                "attachment_upload_in_progress",
                "This upload is still being saved. Retry with the same request key.",
                status_code=409,
            )
        fd = await run_sync(store.open_temp, key)
        digest = hashlib.sha256()
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > (
                MAX_ATTACHMENT_TEXT_BYTES if media_type == "text/plain" else MAX_ATTACHMENT_BYTES
            ):
                raise DomainError(
                    "attachment_too_large", "The document exceeds its size limit.", status_code=413
                )
            digest.update(chunk)
            await run_sync(_write_all, fd, chunk)
        await run_sync(_sync_close, fd)
        fd = None
        if size == 0:
            raise DomainError("empty_attachment", "The document is empty.")
        raw = await run_sync(partial(store.read, key, temp=True))
        try:
            await run_sync(partial(validate_attachment, store.temp_path(key), media_type, raw))
        except AttachmentValidationError as exc:
            code = exc.args[0] if exc.args else "invalid_attachment"
            if code == "image_dimensions_exceeded":
                raise DomainError(
                    "image_dimensions_exceeded",
                    "Images must be 40 megapixels or smaller.",
                ) from exc
            if code in {"unsupported_jpeg_encoding", "unsupported_jpeg_restart"}:
                raise DomainError(
                    "unsupported_image_encoding",
                    "Use an 8-bit baseline JPEG without restart markers, or a static PNG.",
                ) from exc
            if code == "animated_image_unsupported":
                raise DomainError(
                    "animated_image_unsupported", "Animated PNG images are not supported."
                ) from exc
            raise DomainError(
                "invalid_attachment", "The document does not match its declared file type."
            ) from exc

        source_hash = digest.hexdigest()
        fingerprint = upload_fingerprint(
            trip_id=trip_id,
            media_type=media_type,
            sha256=source_hash,
            byte_size=size,
            display_filename=safe_filename,
            reservation_id=reservation_id,
        )
        registration = await run_sync(
            partial(
                service.register_upload,
                owner_id=owner_id,
                trip_id=trip_id,
                expected_revision=expected_revision,
                request_key=request_key,
                fingerprint=fingerprint,
                object_key=key,
                sha256=source_hash,
                media_type=media_type,
                byte_size=size,
                display_filename=safe_filename,
                reservation_id=reservation_id,
            )
        )
        if not registration.created:
            # This request wrote to its own fresh opaque key before resolving
            # the stable request key. It must never touch the registered key's
            # temp file unless it owns that object's lifecycle lock.
            await run_sync(partial(store.delete, key, temp=True))
            if promotion_lock_fd is not None:
                await run_sync(store.release_promotion_lock, promotion_lock_fd)
                promotion_lock_fd = None
            current_state = registration.metadata["state"]
            if current_state == "ready":
                ready_lock = await run_sync(store.try_promotion_lock, registration.object_key)
                if ready_lock is None:
                    raise DomainError(
                        "attachment_busy",
                        "The document is being removed. Retry shortly.",
                        status_code=409,
                    )
                try:
                    descriptor = await run_sync(
                        partial(
                            service.download_descriptor,
                            owner_id,
                            trip_id,
                            registration.attachment_id,
                        )
                    )
                    available = await run_sync(
                        partial(
                            _matches,
                            store,
                            registration.object_key,
                            descriptor.sha256,
                            descriptor.byte_size,
                        )
                    )
                finally:
                    await run_sync(store.release_promotion_lock, ready_lock)
                if not available:
                    raise DomainError(
                        "attachment_unavailable", "The document is unavailable.", status_code=410
                    )
            elif current_state == "pending":
                try:
                    state, metadata = await run_sync(
                        partial(
                            _promote_or_recover,
                            store,
                            registration.object_key,
                            raw,
                            source_hash,
                            size,
                            service,
                            owner_id,
                            trip_id,
                            registration.attachment_id,
                        )
                    )
                except (OSError, ValueError) as exc:
                    raise DomainError(
                        "attachment_promotion_failed",
                        "The upload is saved as pending. Retry with the same request key "
                        "to recover it.",
                        status_code=503,
                    ) from exc
                if state == "busy":
                    raise DomainError(
                        "attachment_upload_in_progress",
                        "This upload is still being saved. Retry with the same request key.",
                        status_code=409,
                    )
                if state != "ready" or metadata is None:
                    raise DomainError(
                        "attachment_unavailable", "The document is unavailable.", status_code=410
                    )
                return AttachmentResponse.model_validate(metadata | {"download_available": True})
            else:
                raise DomainError(
                    "attachment_unavailable", "The document is unavailable.", status_code=410
                )
            return AttachmentResponse.model_validate(
                registration.metadata | {"download_available": True}
            )

        registered_key = registration.object_key
        try:
            state, metadata = await run_sync(
                partial(
                    _promote_or_recover,
                    store,
                    registration.object_key,
                    raw,
                    source_hash,
                    size,
                    service,
                    owner_id,
                    trip_id,
                    registration.attachment_id,
                    lock_fd=promotion_lock_fd,
                )
            )
        except (OSError, ValueError) as exc:
            raise DomainError(
                "attachment_promotion_failed",
                "The upload is saved as pending. Retry with the same request key to recover it.",
                status_code=503,
            ) from exc
        if state == "busy":
            raise DomainError(
                "attachment_upload_in_progress",
                "This upload is still being saved. Retry with the same request key.",
                status_code=409,
            )
        if state != "ready" or metadata is None:
            raise DomainError(
                "attachment_unavailable", "The document is unavailable.", status_code=410
            )
        return AttachmentResponse.model_validate(metadata | {"download_available": True})
    finally:
        if fd is not None:
            await run_sync(_close, fd)
        if promotion_lock_fd is not None:
            await run_sync(store.release_promotion_lock, promotion_lock_fd)
        # A registered pending object's temp/final byte is left for the cleanup
        # worker to recover after an interrupted promotion or state transition.
        if registered_key is None:
            await run_sync(partial(store.delete, key, temp=True))
        store.close()


@router.patch("/{attachment_id}", response_model=AttachmentResponse)
def update_attachment(
    trip_id: UUID,
    attachment_id: UUID,
    payload: AttachmentUpdateRequest,
    request: Request,
    owner_id: OwnerDependency,
    expected_revision: Annotated[int, Header(alias="X-Expected-Revision", ge=0)],
) -> AttachmentResponse:
    store = _gate()
    lock_fd: int | None = None
    try:
        service = _service(request)
        object_key = service.deletion_key(owner_id, trip_id, attachment_id)
        lock_fd = store.try_promotion_lock(object_key)
        if lock_fd is None:
            raise DomainError(
                "attachment_busy",
                "The document is being saved or removed. Retry shortly.",
                status_code=409,
            )
        label = payload.display_filename
        if label is not None:
            label = _display_filename(label)
        metadata = service.patch(
            owner_id=owner_id,
            trip_id=trip_id,
            attachment_id=attachment_id,
            expected_revision=expected_revision,
            display_filename=label,
            reservation_id=payload.reservation_id,
            update_label="display_filename" in payload.model_fields_set,
            update_reservation="reservation_id" in payload.model_fields_set,
        )
        descriptor = service.download_descriptor(owner_id, trip_id, attachment_id)
        response = AttachmentResponse.model_validate(metadata)
        available = _matches(store, descriptor.object_key, descriptor.sha256, descriptor.byte_size)
        return AttachmentResponse.model_validate(
            response.model_dump()
            | {
                "state": response.state if available else "missing",
                "download_available": available,
            }
        )
    finally:
        if lock_fd is not None:
            store.release_promotion_lock(lock_fd)
        store.close()


@router.get("/{attachment_id}/download")
def download_attachment(
    trip_id: UUID, attachment_id: UUID, request: Request, owner_id: OwnerDependency
) -> Response:
    store = _gate()
    try:
        descriptor = _service(request).download_descriptor(owner_id, trip_id, attachment_id)
        try:
            data = store.read(descriptor.object_key)
        except (OSError, ValueError) as exc:
            raise DomainError(
                "attachment_unavailable", "The document is unavailable.", status_code=410
            ) from exc
        if (
            len(data) != descriptor.byte_size
            or hashlib.sha256(data).hexdigest() != descriptor.sha256
        ):
            raise DomainError(
                "attachment_unavailable", "The document is unavailable.", status_code=410
            )
        return Response(
            data,
            media_type=descriptor.media_type,
            headers=_headers_for_download(descriptor.display_filename),
        )
    finally:
        store.close()


@router.delete("/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_attachment(
    trip_id: UUID,
    attachment_id: UUID,
    request: Request,
    owner_id: OwnerDependency,
    expected_revision: Annotated[int, Header(alias="X-Expected-Revision", ge=0)],
) -> Response:
    store = _gate()
    lock_fd: int | None = None
    try:
        service = _service(request)
        object_key = service.deletion_key(owner_id, trip_id, attachment_id)
        lock_fd = store.try_promotion_lock(object_key)
        if lock_fd is None:
            raise DomainError(
                "attachment_busy",
                "The document is being saved or removed. Retry shortly.",
                status_code=409,
            )
        object_key = service.begin_delete(
            owner_id=owner_id,
            trip_id=trip_id,
            attachment_id=attachment_id,
            expected_revision=expected_revision,
        )
        store.delete(object_key)
        store.delete(object_key, temp=True)
        service.finish_delete(owner_id, trip_id, attachment_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    finally:
        if lock_fd is not None:
            store.release_promotion_lock(lock_fd)
        store.close()
