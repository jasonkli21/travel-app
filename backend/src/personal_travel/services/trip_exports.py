"""Bounded, revision-stamped trip snapshots and deterministic serializers."""

from __future__ import annotations

import hashlib
import html
import json
import multiprocessing as mp
import re
import resource
import sys
import time
import uuid
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from datetime import time as local_time
from io import BytesIO
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, selectinload

from personal_travel.api.schemas.attachments import TripExportRequest
from personal_travel.domain.urls import validate_http_url
from personal_travel.models.import_source import SourceAttachment
from personal_travel.models.itinerary import ItineraryItem
from personal_travel.models.place import Place
from personal_travel.models.reservation import Reservation, SavedPlace
from personal_travel.models.trip import Trip, TripDay
from personal_travel.services.conflicts import (
    ReservationConflict,
    intervals_overlap,
    schedule_bounds,
)
from personal_travel.services.errors import DomainError, not_found
from personal_travel.services.source_store import LocalSourceStore
from personal_travel.services.time_utils import as_aware_utc, local_date_time_parts

SessionFactoryLike = Callable[[], Session]
MAX_EXPORT_RECORDS = 5_000
MAX_EXPORT_BYTES = 10 * 1024 * 1024
MAX_BUNDLE_DOCUMENT_BYTES = 25 * 1024 * 1024
MAX_BUNDLE_BYTES = MAX_EXPORT_BYTES + MAX_BUNDLE_DOCUMENT_BYTES + 1024 * 1024
MAX_EXPORT_CONFLICT_PAIRS = 100_000
MAX_EXPORT_DOCUMENTS = 50
EXPORT_SECONDS = 10.0
MAX_EXPORT_SOURCE_CHARS = 750_000


@dataclass(frozen=True, slots=True)
class ExportArtifact:
    data: bytes
    media_type: str
    filename: str


@dataclass(frozen=True, slots=True)
class _Projection:
    data: dict[str, Any]
    objects: tuple[tuple[str, str, str, int], ...]
    started: float


def build_export(
    session_factory: SessionFactoryLike,
    *,
    owner_id: str,
    trip_id: UUID,
    options: TripExportRequest,
    generated_at: datetime | None = None,
) -> _Projection:
    generated = generated_at or datetime.now(UTC)
    started = time.monotonic()
    with session_factory() as session, session.begin():
        # Lock only the aggregate root first. Selected child rows are counted
        # before loading, and shared-place locks are limited to this snapshot.
        trip = session.scalar(
            select(Trip)
            .where(Trip.owner_id == owner_id, Trip.id == trip_id)
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )
        if trip is None:
            raise not_found("trip")
        start_date = options.start_date or trip.start_date
        end_date = options.end_date or trip.end_date
        if start_date < trip.start_date or end_date > trip.end_date:
            raise DomainError(
                "invalid_export_scope",
                "The export date range must be within the trip dates.",
            )
        reservation_scope = _reservation_date_scope(
            owner_id=owner_id,
            trip_id=trip_id,
            trip_start_date=trip.start_date,
            trip_end_date=trip.end_date,
            start_date=start_date,
            end_date=end_date,
            timezone_name=trip.timezone,
        )
        item_scope = and_(
            TripDay.trip_id == trip_id,
            TripDay.date >= start_date,
            TripDay.date <= end_date,
        )
        selected_item_count = (
            session.scalar(
                select(func.count()).select_from(ItineraryItem).join(TripDay).where(item_scope)
            )
            or 0
        )
        selected_reservation_count = (
            session.scalar(
                select(func.count())
                .select_from(Reservation)
                .where(
                    Reservation.owner_id == owner_id,
                    Reservation.trip_id == trip_id,
                    reservation_scope,
                )
            )
            or 0
        )
        saved_place_count = (
            session.scalar(
                select(func.count())
                .select_from(SavedPlace)
                .where(SavedPlace.owner_id == owner_id, SavedPlace.trip_id == trip_id)
            )
            or 0
        )
        if (
            selected_item_count > MAX_EXPORT_RECORDS
            or selected_reservation_count > MAX_EXPORT_RECORDS
            or saved_place_count > MAX_EXPORT_RECORDS
        ):
            raise DomainError(
                "export_too_large",
                "This date range has too many records for one snapshot.",
                status_code=413,
            )
        if selected_item_count * selected_reservation_count > MAX_EXPORT_CONFLICT_PAIRS:
            raise DomainError(
                "export_too_large",
                "This date range has too many reservation/item comparisons for one snapshot.",
                status_code=413,
            )
        selected_place_ids = (
            select(ItineraryItem.place_id)
            .join(TripDay)
            .where(item_scope, ItineraryItem.place_id.is_not(None))
            .union(
                select(Reservation.place_id).where(
                    Reservation.owner_id == owner_id,
                    Reservation.trip_id == trip_id,
                    reservation_scope,
                    Reservation.place_id.is_not(None),
                ),
                select(SavedPlace.place_id).where(
                    SavedPlace.owner_id == owner_id,
                    SavedPlace.trip_id == trip_id,
                ),
            )
        )
        text_chars = len(trip.title) + len(trip.timezone)
        text_chars += _text_char_count(session, TripDay, (TripDay.title,), item_scope)
        text_chars += _text_char_count(
            session,
            ItineraryItem,
            (
                ItineraryItem.title,
                ItineraryItem.item_type,
                ItineraryItem.status,
                ItineraryItem.notes,
            ),
            item_scope,
            join=TripDay,
        )
        text_chars += _text_char_count(
            session,
            Reservation,
            (
                Reservation.provider_name,
                Reservation.reservation_type,
                Reservation.status,
                Reservation.confirmation_code,
                Reservation.source_reference,
                Reservation.notes,
            ),
            Reservation.owner_id == owner_id,
            Reservation.trip_id == trip_id,
            reservation_scope,
        )
        text_chars += _text_char_count(
            session,
            SavedPlace,
            (SavedPlace.note,),
            SavedPlace.owner_id == owner_id,
            SavedPlace.trip_id == trip_id,
        )
        text_chars += _text_char_count(
            session,
            Place,
            (
                Place.name,
                Place.address,
                Place.category,
                Place.phone,
                Place.website_url,
                Place.provider_source_name,
                Place.provider_source_attribution,
                Place.provider_source_license,
                Place.provider_source_url,
            ),
            Place.owner_id == owner_id,
            Place.id.in_(selected_place_ids),
        )
        if text_chars > MAX_EXPORT_SOURCE_CHARS:
            raise _export_too_large()
        _check_deadline(started)
        day_rows = session.scalars(
            select(TripDay)
            .where(item_scope)
            .order_by(TripDay.day_index)
            .options(selectinload(TripDay.items).selectinload(ItineraryItem.place))
        ).all()
        selected_reservations = session.scalars(
            select(Reservation)
            .where(
                Reservation.owner_id == owner_id,
                Reservation.trip_id == trip_id,
                reservation_scope,
            )
            .order_by(Reservation.starts_at.is_(None), Reservation.starts_at, Reservation.id)
            .options(selectinload(Reservation.place))
        ).all()
        selected_saved_places = session.scalars(
            select(SavedPlace)
            .where(SavedPlace.owner_id == owner_id, SavedPlace.trip_id == trip_id)
            .order_by(SavedPlace.place_id)
            .options(selectinload(SavedPlace.place))
        ).all()
        _check_deadline(started)
        all_selected_items = [item for day in day_rows for item in day.items]
        all_selected_ids = (
            {item.place_id for item in all_selected_items if item.place_id is not None}
            | {
                reservation.place_id
                for reservation in selected_reservations
                if reservation.place_id is not None
            }
            | {saved.place_id for saved in selected_saved_places}
        )
        place_revisions = []
        if all_selected_ids:
            places = session.scalars(
                select(Place)
                .where(Place.owner_id == owner_id, Place.id.in_(all_selected_ids))
                .order_by(Place.id)
                .with_for_update(read=True)
                .execution_options(populate_existing=True)
            ).all()
            place_revisions = [
                {"place_id": str(place.id), "revision": place.revision} for place in places
            ]
        _check_deadline(started)

        conflicts_by_reservation: dict[UUID, list[ReservationConflict]] = {}
        selected_pairs = 0
        conflict_count = 0
        for reservation in selected_reservations:
            if reservation.status == "cancelled":
                continue
            reservation_bounds = schedule_bounds(reservation.starts_at, reservation.ends_at)
            if reservation_bounds is None:
                continue
            for day in day_rows:
                for item in day.items:
                    selected_pairs += 1
                    if selected_pairs > MAX_EXPORT_CONFLICT_PAIRS:
                        raise DomainError(
                            "export_too_large",
                            "This date range has too many reservation/item comparisons "
                            "for one snapshot.",
                            status_code=413,
                        )
                    if item.status == "cancelled" or item.reservation_id == reservation.id:
                        continue
                    item_bounds = schedule_bounds(item.starts_at, item.ends_at)
                    if item_bounds is not None and intervals_overlap(
                        reservation_bounds, item_bounds
                    ):
                        conflict_count += 1
                        if conflict_count > MAX_EXPORT_RECORDS:
                            raise DomainError(
                                "export_too_large",
                                "This date range has too many conflicts for one snapshot.",
                                status_code=413,
                            )
                        conflicts_by_reservation.setdefault(reservation.id, []).append(
                            ReservationConflict(
                                item=item,
                                day=day,
                                reason="Reservation overlaps this itinerary item.",
                            )
                        )
                _check_deadline(started)
        attachments: list[dict[str, Any]] = []
        object_rows: list[tuple[str, str, str, int]] = []
        if options.include_documents:
            rows = session.scalars(
                select(SourceAttachment)
                .where(
                    SourceAttachment.owner_id == owner_id,
                    SourceAttachment.trip_id == trip_id,
                    SourceAttachment.purpose == "trip_attachment",
                    SourceAttachment.state == "ready",
                )
                .order_by(SourceAttachment.created_at, SourceAttachment.id)
                .limit(MAX_EXPORT_DOCUMENTS + 1)
            ).all()
            if len(rows) > MAX_EXPORT_DOCUMENTS:
                raise DomainError(
                    "export_too_large",
                    "Too many documents were selected for one snapshot.",
                    status_code=413,
                )
            total_document_bytes = sum(row.byte_size for row in rows)
            if total_document_bytes > MAX_BUNDLE_DOCUMENT_BYTES:
                raise DomainError(
                    "export_too_large",
                    "Trip documents exceed the 25 MiB bundle limit. Remove some documents first.",
                    status_code=413,
                )
            reservation_ids = {row.reservation_id for row in rows if row.reservation_id is not None}
            reservation_labels = {
                reservation_id: provider_name
                for reservation_id, provider_name in session.execute(
                    select(Reservation.id, Reservation.provider_name).where(
                        Reservation.owner_id == owner_id,
                        Reservation.trip_id == trip_id,
                        Reservation.id.in_(reservation_ids),
                    )
                )
            }
            for index, row in enumerate(rows, start=1):
                _check_deadline(started)
                filename = _safe_document_name(row.display_filename or "travel-document")
                archive_path = f"attachments/{index:02d}-{filename}"
                attachments.append(
                    {
                        "id": str(row.id),
                        "reservation_id": str(row.reservation_id) if row.reservation_id else None,
                        "reservation_label": (
                            reservation_labels.get(row.reservation_id)
                            if row.reservation_id is not None
                            else None
                        ),
                        "display_filename": filename,
                        "media_type": row.media_type,
                        "byte_size": row.byte_size,
                        "archive_path": archive_path,
                    }
                )
                object_rows.append((row.object_key, row.sha256, archive_path, row.byte_size))

        include_private = options.include_private_fields
        projected_days: list[dict[str, Any]] = []
        for day in day_rows:
            _check_deadline(started)
            projected_items = []
            for item in sorted(day.items, key=lambda row: row.sort_order):
                _check_deadline(started)
                projected_items.append(_item_projection(item, trip.timezone, include_private))
            projected_days.append(
                {
                    "id": str(day.id),
                    "day_index": day.day_index,
                    "date": day.date.isoformat(),
                    "title": day.title,
                    "items": projected_items,
                }
            )
        projected_reservations = []
        for reservation in selected_reservations:
            _check_deadline(started)
            projected_reservations.append(
                _reservation_projection(
                    reservation,
                    trip.timezone,
                    include_private,
                    conflicts_by_reservation.get(reservation.id, []),
                )
            )
        projected_saved_places = []
        for saved in sorted(selected_saved_places, key=lambda row: row.place.name.casefold()):
            _check_deadline(started)
            projected_saved_places.append(
                {
                    "place": _place_projection(saved.place),
                    **({"note": saved.note} if include_private and saved.note else {}),
                }
            )
        data: dict[str, Any] = {
            "schema_version": "travel-trip-export-v1",
            "metadata": {
                "generated_at": generated.isoformat(),
                "timezone": trip.timezone,
                "trip_revision": trip.revision,
                "date_scope": {
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat(),
                },
                "private_fields_included": include_private,
                "documents_included": options.include_documents,
                "static_snapshot": True,
                "freshness_notice": (
                    "This is a static snapshot. Changes made after generation are not included; "
                    "regenerate it before relying on updated trip details."
                ),
            },
            "trip": {
                "id": str(trip.id),
                "title": trip.title,
                "start_date": trip.start_date.isoformat(),
                "end_date": trip.end_date.isoformat(),
                "timezone": trip.timezone,
            },
            "days": projected_days,
            "reservations": projected_reservations,
            "saved_places": projected_saved_places,
            "revision_footprint": {
                "trip_revision": trip.revision,
                "places": place_revisions,
            },
            "included_sections": ["itinerary", "reservations", "saved_places"],
        }
        if options.include_documents:
            data["attachments"] = attachments
            data["included_sections"].append("attachments")
        if include_private:
            data["included_sections"].append("private_fields")
        _check_projection_text(data, started)
        _check_deadline(started)
        return _Projection(data, tuple(object_rows), started)


def render_export(
    projection: _Projection,
    *,
    export_format: str,
    include_documents: bool,
    include_linked_reservations: bool,
    store: Any | None = None,
) -> ExportArtifact:
    """Render a bounded artifact in a disposable process with a hard wall deadline."""
    _check_deadline(projection.started)
    store_root: str | None = None
    if include_documents:
        if store is None:
            raise DomainError(
                "attachments_disabled",
                "Trip documents are unavailable for this export.",
                status_code=404,
            )
        root = getattr(store, "root", None)
        if not isinstance(root, (str, bytes)) and not hasattr(root, "__fspath__"):
            raise DomainError(
                "attachments_disabled",
                "Trip documents are unavailable for this export.",
                status_code=404,
            )
        store_root = str(root)

    context = mp.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    process = context.Process(
        target=_render_worker,
        args=(
            projection.data,
            projection.objects,
            projection.started,
            export_format,
            include_documents,
            include_linked_reservations,
            store_root,
            child,
        ),
    )
    deadline = projection.started + EXPORT_SECONDS
    try:
        process.start()
        child.close()
        header = _receive_worker_message(parent, process, deadline)
        if not isinstance(header, tuple) or not header:
            raise _render_worker_failed()
        if header[0] == "error":
            if (
                len(header) != 4
                or not isinstance(header[1], str)
                or not isinstance(header[2], str)
                or not isinstance(header[3], int)
            ):
                raise _render_worker_failed()
            raise DomainError(header[1], header[2], status_code=header[3])
        if (
            len(header) != 4
            or header[0] != "ok"
            or not isinstance(header[1], str)
            or not isinstance(header[2], str)
            or not isinstance(header[3], int)
            or header[3] < 0
            or header[3] > (MAX_BUNDLE_BYTES if include_documents else MAX_EXPORT_BYTES)
        ):
            raise _render_worker_failed()
        _, media_type, filename, expected_size = header
        output = bytearray()
        while len(output) < expected_size:
            _check_deadline(projection.started)
            _wait_for_worker_data(parent, process, deadline)
            chunk = parent.recv_bytes(min(64 * 1024, expected_size - len(output)))
            if not chunk or len(output) + len(chunk) > expected_size:
                raise _render_worker_failed()
            output.extend(chunk)
        artifact_bytes = bytes(output)
        _check_deadline(projection.started)
        return ExportArtifact(artifact_bytes, media_type, filename)
    except DomainError:
        raise
    except (EOFError, OSError, ValueError) as exc:
        raise _render_worker_failed() from exc
    finally:
        parent.close()
        if child.closed is False:
            child.close()
        if process.pid is not None:
            if process.is_alive():
                process.terminate()
                process.join(timeout=0.2)
                if process.is_alive():
                    process.kill()
            process.join(timeout=0.2)
            process.close()


def _render_worker(
    snapshot: dict[str, Any],
    objects: tuple[tuple[str, str, str, int], ...],
    started: float,
    export_format: str,
    include_documents: bool,
    include_linked_reservations: bool,
    store_root: str | None,
    connection: Connection,
) -> None:
    store = None
    try:
        _set_export_worker_limits()
        if store_root is not None:
            store = LocalSourceStore(store_root)
        artifact = _render_export_contents(
            _Projection(snapshot, objects, started),
            export_format=export_format,
            include_documents=include_documents,
            include_linked_reservations=include_linked_reservations,
            store=store,
        )
        connection.send(("ok", artifact.media_type, artifact.filename, len(artifact.data)))
        for offset in range(0, len(artifact.data), 64 * 1024):
            _check_deadline(started)
            connection.send_bytes(artifact.data[offset : offset + 64 * 1024])
    except DomainError as exc:
        try:
            connection.send(("error", exc.code, exc.message, exc.status_code))
        except (BrokenPipeError, OSError):
            pass
    except BaseException:
        try:
            connection.send(
                (
                    "error",
                    "export_worker_failed",
                    "The snapshot could not be rendered. Please retry with a smaller scope.",
                    503,
                )
            )
        except (BrokenPipeError, OSError):
            pass
    finally:
        if store is not None:
            store.close()
        connection.close()


def _set_export_worker_limits() -> None:
    try:
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        cpu_limit = max(1, int(EXPORT_SECONDS))
        soft, hard = resource.getrlimit(resource.RLIMIT_CPU)
        ceiling = cpu_limit if hard == resource.RLIM_INFINITY else min(cpu_limit, hard)
        resource.setrlimit(resource.RLIMIT_CPU, (ceiling, ceiling))
        if sys.platform != "darwin":
            _set_resource_limit(resource.RLIMIT_AS, 1024 * 1024 * 1024)
            _set_resource_limit(resource.RLIMIT_DATA, 512 * 1024 * 1024)
    except (OSError, ValueError) as exc:
        raise DomainError(
            "export_worker_limit_failed",
            "The snapshot worker could not establish its resource limits.",
            status_code=503,
        ) from exc


def _set_resource_limit(limit: int, maximum: int) -> None:
    _, hard = resource.getrlimit(limit)
    ceiling = maximum if hard == resource.RLIM_INFINITY else min(maximum, hard)
    resource.setrlimit(limit, (ceiling, ceiling))


def _receive_worker_message(connection: Connection, process: BaseProcess, deadline: float) -> Any:
    _wait_for_worker_data(connection, process, deadline)
    return connection.recv()


def _wait_for_worker_data(connection: Connection, process: BaseProcess, deadline: float) -> None:
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise _export_too_slow()
        if connection.poll(min(remaining, 0.05)):
            return
        if not process.is_alive():
            raise _render_worker_failed()


def _render_worker_failed() -> DomainError:
    return DomainError(
        "export_worker_failed",
        "The snapshot could not be rendered. Please retry with a smaller scope.",
        status_code=503,
    )


def _render_export_contents(
    projection: _Projection,
    *,
    export_format: str,
    include_documents: bool,
    include_linked_reservations: bool,
    store: Any | None,
) -> ExportArtifact:
    started = projection.started
    snapshot = projection.data
    if export_format == "ics":
        filename = "trip.ics"
        media_type = "text/calendar; charset=utf-8"
        body = _render_ics(snapshot, include_linked_reservations, started)
    elif export_format == "html":
        filename = "trip.html"
        media_type = "text/html; charset=utf-8"
        body = _render_html(snapshot, started)
    elif export_format == "json":
        filename = "trip.json"
        media_type = "application/json"
        body = _bounded_json_bytes(snapshot, started)
    else:
        raise ValueError("Unsupported export format.")
    if len(body) > MAX_EXPORT_BYTES:
        raise DomainError(
            "export_too_large",
            "This snapshot exceeds the 10 MiB export limit. Choose a smaller date range.",
            status_code=413,
        )
    if not include_documents:
        return ExportArtifact(body, media_type, _download_name(snapshot, filename))
    if store is None:
        raise DomainError(
            "attachments_disabled",
            "Trip documents are unavailable for this export.",
            status_code=404,
        )
    document_bytes = sum(row[3] for row in projection.objects)
    if document_bytes + len(body) > MAX_BUNDLE_BYTES:
        raise DomainError(
            "export_too_large",
            "The snapshot and documents exceed the bundle size limit.",
            status_code=413,
        )
    archive = BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as bundle:
        bundle.writestr(filename, body)
        manifest = {
            "schema_version": "travel-trip-bundle-manifest-v1",
            "documents": snapshot.get("attachments", []),
        }
        bundle.writestr("manifest.json", _bounded_json_bytes(manifest, started))
        for object_key, expected_hash, archive_path, expected_size in projection.objects:
            _check_deadline(started)
            try:
                data = store.read(object_key)
            except (OSError, ValueError) as exc:
                raise DomainError(
                    "attachment_unavailable",
                    "A selected trip document is unavailable. Refresh the document list and retry.",
                    status_code=410,
                ) from exc
            if len(data) != expected_size or _sha256(data) != expected_hash:
                raise DomainError(
                    "attachment_unavailable",
                    "A selected trip document is unavailable. Refresh the document list and retry.",
                    status_code=410,
                )
            with bundle.open(archive_path, "w") as target:
                for offset in range(0, len(data), 64 * 1024):
                    _check_deadline(started)
                    target.write(data[offset : offset + 64 * 1024])
                    if archive.tell() > MAX_BUNDLE_BYTES:
                        raise DomainError(
                            "export_too_large",
                            "The download bundle exceeds its size limit.",
                            status_code=413,
                        )
    if archive.tell() > MAX_BUNDLE_BYTES:
        raise DomainError(
            "export_too_large",
            "The download bundle exceeds its size limit.",
            status_code=413,
        )
    _check_deadline(started)
    bundle_bytes = archive.getvalue()
    _check_deadline(started)
    return ExportArtifact(
        bundle_bytes,
        "application/zip",
        _download_name(snapshot, "trip.zip"),
    )


def _item_projection(item: Any, timezone_name: str, include_private: bool) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": str(item.id),
        "item_type": item.item_type,
        "title": item.title,
        "start_time": _time_part(item.starts_at, timezone_name),
        "end_time": _time_part(item.ends_at, timezone_name),
        "starts_at_utc": _utc_part(item.starts_at),
        "ends_at_utc": _utc_part(item.ends_at),
        "sort_order": item.sort_order,
        "status": item.status,
        "place": _place_projection(item.place),
        "reservation_id": str(item.reservation_id) if item.reservation_id else None,
    }
    if include_private and item.notes:
        result["notes"] = item.notes
    return result


def _reservation_projection(
    reservation: Any,
    timezone_name: str,
    include_private: bool,
    conflicts: list[Any],
) -> dict[str, Any]:
    start_date, start_time = local_date_time_parts(reservation.starts_at, timezone_name)
    end_date, end_time = local_date_time_parts(reservation.ends_at, timezone_name)
    result: dict[str, Any] = {
        "id": str(reservation.id),
        "reservation_type": reservation.reservation_type,
        "status": reservation.status,
        "provider_name": reservation.provider_name,
        "start_date": start_date.isoformat() if start_date else None,
        "start_time": start_time,
        "end_date": end_date.isoformat() if end_date else None,
        "end_time": end_time,
        "starts_at_utc": _utc_part(reservation.starts_at),
        "ends_at_utc": _utc_part(reservation.ends_at),
        "place": _place_projection(reservation.place),
        "conflicts": [
            {
                "item_title": conflict.item.title,
                "date": conflict.day.date.isoformat(),
                "reason": conflict.reason,
            }
            for conflict in conflicts
        ],
    }
    if include_private:
        if reservation.confirmation_code:
            result["confirmation_code"] = reservation.confirmation_code
        if reservation.notes:
            result["notes"] = reservation.notes
        if reservation.source_reference:
            result["source_reference"] = reservation.source_reference
    return result


def _place_projection(place: Place | None) -> dict[str, Any] | None:
    if place is None:
        return None
    return {
        "id": str(place.id),
        "revision": place.revision,
        "name": place.name,
        "address": place.address,
        "category": place.category,
        "latitude": float(place.latitude) if place.latitude is not None else None,
        "longitude": float(place.longitude) if place.longitude is not None else None,
        "website_url": place.website_url,
        "provider_source_name": place.provider_source_name,
        "provider_source_attribution": place.provider_source_attribution,
        "provider_source_license": place.provider_source_license,
        "provider_source_url": place.provider_source_url,
    }


def _time_part(value: datetime | None, timezone_name: str) -> str | None:
    if value is None:
        return None
    return as_aware_utc(value).astimezone(ZoneInfo(timezone_name)).strftime("%H:%M:%S")


def _utc_part(value: datetime | None) -> str | None:
    return as_aware_utc(value).strftime("%Y-%m-%dT%H:%M:%SZ") if value else None


def _render_ics(
    snapshot: dict[str, Any], include_linked_reservations: bool, started: float
) -> bytes:
    metadata = snapshot["metadata"]
    trip = snapshot["trip"]
    generated = datetime.fromisoformat(metadata["generated_at"]).astimezone(UTC)
    dtstamp = generated.strftime("%Y%m%dT%H%M%SZ")
    revision = int(metadata["trip_revision"])
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Personal Travel App//Travel Snapshot 1.0//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_ics_escape(trip['title'])}",
        f"X-WR-TIMEZONE:{_ics_escape(trip['timezone'])}",
        f"X-TRAVEL-TRIP-REVISION:{metadata['trip_revision']}",
        f"X-TRAVEL-GENERATED-AT:{_ics_datetime(metadata['generated_at'])}",
        f"X-TRAVEL-DATE-START;VALUE=DATE:{metadata['date_scope']['start_date'].replace('-', '')}",
        f"X-TRAVEL-DATE-END;VALUE=DATE:{metadata['date_scope']['end_date'].replace('-', '')}",
        f"X-TRAVEL-PRIVATE-FIELDS:{'TRUE' if metadata['private_fields_included'] else 'FALSE'}",
        f"X-TRAVEL-DOCUMENTS:{'TRUE' if metadata['documents_included'] else 'FALSE'}",
        "X-TRAVEL-STATIC-SNAPSHOT:TRUE",
    ]
    if "saved_places" in snapshot.get("included_sections", []):
        lines.append("X-TRAVEL-OMITTED-SECTIONS:SAVED-PLACES")
    reservation_by_id = {row["id"]: row for row in snapshot["reservations"]}
    represented_item_by_reservation: dict[str, dict[str, Any]] = {}
    for day in snapshot["days"]:
        for item in day["items"]:
            _check_deadline(started)
            reservation_id = item.get("reservation_id")
            reservation = reservation_by_id.get(reservation_id)
            item_place = item.get("place")
            reservation_place = reservation.get("place") if reservation else None
            if (
                item["status"] != "cancelled"
                and reservation is not None
                and reservation["status"] != "cancelled"
                and item.get("starts_at_utc") == reservation.get("starts_at_utc")
                and item.get("ends_at_utc") == reservation.get("ends_at_utc")
                and (item_place or {}).get("id") == (reservation_place or {}).get("id")
            ):
                represented_item_by_reservation.setdefault(reservation_id, item)
    represented_ids = set(represented_item_by_reservation)
    rendered_size = sum(len(_fold_ical_line(line).encode("utf-8")) + 2 for line in lines)
    for day in snapshot["days"]:
        for item in day["items"]:
            _check_deadline(started)
            if item["status"] == "cancelled":
                continue
            linked_reservation = None
            candidate = reservation_by_id.get(item.get("reservation_id"))
            if (
                not include_linked_reservations
                and candidate is not None
                and represented_item_by_reservation.get(candidate["id"]) is item
            ):
                linked_reservation = candidate
            rendered_size += _append_ics_lines(
                lines,
                _ics_item(item, day["date"], dtstamp, revision, metadata, linked_reservation),
                started,
            )
            if rendered_size > MAX_EXPORT_BYTES:
                raise _export_too_large()
    for reservation in snapshot["reservations"]:
        _check_deadline(started)
        if reservation["status"] == "cancelled":
            continue
        if not include_linked_reservations and reservation["id"] in represented_ids:
            continue
        if reservation["starts_at_utc"] is None and reservation["ends_at_utc"] is None:
            continue
        rendered_size += _append_ics_lines(
            lines, _ics_reservation(reservation, dtstamp, revision, metadata), started
        )
        if rendered_size > MAX_EXPORT_BYTES:
            raise _export_too_large()
    lines.append("END:VCALENDAR")
    _check_deadline(started)
    output = ("\r\n".join(_fold_ical_line(line) for line in lines) + "\r\n").encode("utf-8")
    _check_deadline(started)
    return output


def _ics_item(
    item: dict[str, Any],
    day_date: str,
    dtstamp: str,
    revision: int,
    metadata: dict[str, Any],
    linked_reservation: dict[str, Any] | None = None,
) -> list[str]:
    place = linked_reservation["place"] if linked_reservation else item["place"]
    description_parts = []
    if linked_reservation:
        description_parts.append(
            "Reservation: "
            + linked_reservation["provider_name"]
            + " ("
            + linked_reservation["reservation_type"]
            + ", "
            + linked_reservation["status"]
            + ")"
        )
        if metadata["private_fields_included"]:
            for field, label in (
                ("confirmation_code", "Confirmation"),
                ("source_reference", "Source reference"),
                ("notes", "Reservation notes"),
            ):
                if linked_reservation.get(field):
                    description_parts.append(f"{label}: {linked_reservation[field]}")
    return _ics_event(
        kind="item",
        record_id=item["id"],
        summary=item["title"],
        status=item["status"],
        start_utc=item["starts_at_utc"],
        end_utc=item["ends_at_utc"],
        date_value=day_date,
        dtstamp=dtstamp,
        revision=revision,
        location=_place_location(place),
        description="\n".join(
            value
            for value in [
                item.get("notes") if metadata["private_fields_included"] else None,
                *description_parts,
            ]
            if value
        )
        or None,
        item_type=item["item_type"],
        place_credit=_place_credit(place),
    )


def _ics_reservation(
    reservation: dict[str, Any], dtstamp: str, revision: int, metadata: dict[str, Any]
) -> list[str]:
    return _ics_event(
        kind="reservation",
        record_id=reservation["id"],
        summary=f"{reservation['provider_name']} · {reservation['reservation_type']}",
        status=reservation["status"],
        start_utc=reservation["starts_at_utc"],
        end_utc=reservation["ends_at_utc"],
        date_value=None,
        dtstamp=dtstamp,
        revision=revision,
        location=_place_location(reservation["place"]),
        description=reservation.get("notes") if metadata["private_fields_included"] else None,
        item_type=None,
        confirmation_code=(
            reservation.get("confirmation_code") if metadata["private_fields_included"] else None
        ),
        source_reference=(
            reservation.get("source_reference") if metadata["private_fields_included"] else None
        ),
        place_credit=_place_credit(reservation["place"]),
    )


def _ics_event(
    *,
    kind: str,
    record_id: str,
    summary: str,
    status: str,
    start_utc: str | None,
    end_utc: str | None,
    date_value: str | None,
    dtstamp: str,
    revision: int,
    location: str | None,
    description: str | None,
    item_type: str | None,
    confirmation_code: str | None = None,
    source_reference: str | None = None,
    place_credit: str | None = None,
) -> list[str]:
    uid = uuid.uuid5(uuid.NAMESPACE_URL, f"personal-travel:{kind}:{record_id}")
    lines = [
        "BEGIN:VEVENT",
        f"UID:{uid}@personal-travel.app",
        f"DTSTAMP:{dtstamp}",
        f"SEQUENCE:{revision}",
        f"SUMMARY:{_ics_escape(summary)}",
        f"STATUS:{_ical_status(status)}",
    ]
    if start_utc is not None:
        lines.append(f"DTSTART:{_ics_datetime(start_utc)}")
        if end_utc is not None and end_utc != start_utc:
            lines.append(f"DTEND:{_ics_datetime(end_utc)}")
        else:
            lines.append("X-TRAVEL-POINT:TRUE")
    elif end_utc is not None:
        # Existing domain rules treat a single endpoint as a point event. Use
        # that instant as DTSTART without inventing an end or duration.
        lines.append(f"DTSTART:{_ics_datetime(end_utc)}")
        lines.append("X-TRAVEL-POINT:TRUE")
    elif date_value is not None:
        day = date.fromisoformat(date_value)
        lines.append(f"DTSTART;VALUE=DATE:{day.strftime('%Y%m%d')}")
        lines.append(f"DTEND;VALUE=DATE:{(day + timedelta(days=1)).strftime('%Y%m%d')}")
        lines.append("X-TRAVEL-FLEXIBLE-DAY:TRUE")
    else:
        return []
    if location:
        lines.append(f"LOCATION:{_ics_escape(location)}")
    description_parts = []
    if item_type:
        description_parts.append(f"Type: {item_type}")
    if place_credit:
        description_parts.append(place_credit)
    if confirmation_code:
        description_parts.append(f"Confirmation: {confirmation_code}")
    if source_reference:
        description_parts.append(f"Source reference: {source_reference}")
    if description:
        description_parts.append(description)
    if description_parts:
        description_text = _ics_escape("\n".join(description_parts))
        lines.append(f"DESCRIPTION:{description_text}")
    return [*lines, "END:VEVENT"]


def _render_html(snapshot: dict[str, Any], started: float) -> bytes:
    metadata = snapshot["metadata"]
    trip = snapshot["trip"]
    title = html.escape(trip["title"])
    reservation_by_id = {row["id"]: row for row in snapshot["reservations"]}
    document_entries: list[str] = []
    for item in snapshot.get("attachments", []):
        _check_deadline(started)
        reservation = reservation_by_id.get(item.get("reservation_id"))
        linked_label = item.get("reservation_label") or (
            reservation["provider_name"] if reservation else None
        )
        association = (
            f" · Linked to {html.escape(linked_label)}"
            if linked_label
            else " · No reservation link"
        )
        document_entries.append(
            f'<li data-attachment-id="{html.escape(item["id"], quote=True)}">'
            f'<a href="{html.escape(item["archive_path"], quote=True)}">'
            f"{html.escape(item['display_filename'])}</a>{association}</li>"
        )
    document_links = "".join(document_entries)
    sections: list[str] = []
    for day in snapshot["days"]:
        _check_deadline(started)
        items: list[str] = []
        for item in day["items"]:
            place = item["place"]
            place_text = (
                " · ".join(part for part in (place["name"], place["address"]) if part)
                if place
                else ""
            )
            when = _format_schedule(item["start_time"], item["end_time"], "Flexible day plan")
            details = [place_text, item["status"], item["item_type"]]
            if item.get("notes"):
                details.append(item["notes"])
            linked = next(
                (
                    reservation["provider_name"]
                    for reservation in snapshot["reservations"]
                    if reservation["id"] == item["reservation_id"]
                ),
                None,
            )
            if linked:
                details.append(f"Reservation anchor: {linked}")
            items.append(
                "<li><time>"
                + html.escape(when)
                + "</time><div><strong>"
                + html.escape(item["title"])
                + "</strong><p>"
                + html.escape(" · ".join(value for value in details if value))
                + ("<br>" + _place_credit_markup(place) if _place_credit_markup(place) else "")
                + "</p></div></li>"
            )
        heading = html.escape(day["title"] or f"Day {day['day_index']}")
        sections.append(
            f"<section><h2>{heading} <time>{html.escape(day['date'])}</time></h2>"
            + ("<ol>" + "".join(items) + "</ol>" if items else "<p>No itinerary items.</p>")
            + "</section>"
        )

    reservations = []
    for reservation in snapshot["reservations"]:
        place = reservation["place"]
        schedule = (
            " ".join(
                value
                for value in (
                    reservation["start_date"],
                    reservation["start_time"],
                    reservation["end_date"],
                    reservation["end_time"],
                )
                if value
            )
            or "Schedule not set"
        )
        detail_parts = [reservation["reservation_type"], reservation["status"], schedule]
        if place:
            detail_parts.extend(value for value in (place["name"], place["address"]) if value)
        if reservation.get("confirmation_code"):
            detail_parts.append(f"Confirmation: {reservation['confirmation_code']}")
        if reservation.get("notes"):
            detail_parts.append(reservation["notes"])
        if reservation.get("source_reference"):
            detail_parts.append(f"Source reference: {reservation['source_reference']}")
        warning_rows = []
        for conflict in reservation["conflicts"]:
            _check_deadline(started)
            warning_rows.append(
                f'<li class="warning">{html.escape(conflict["reason"])} '
                f"({html.escape(conflict['date'])} · {html.escape(conflict['item_title'])})</li>"
            )
        warnings = "".join(warning_rows)
        reservations.append(
            "<li><strong>"
            + html.escape(reservation["provider_name"])
            + "</strong><p>"
            + html.escape(" · ".join(detail_parts))
            + ("<br>" + _place_credit_markup(place) if _place_credit_markup(place) else "")
            + "</p>"
            + (f"<ul>{warnings}</ul>" if warnings else "")
            + "</li>"
        )
    privacy = (
        "Private fields included"
        if metadata["private_fields_included"]
        else "Private fields omitted"
    )
    freshness = html.escape(metadata["freshness_notice"])
    generated = html.escape(metadata["generated_at"])
    scope = metadata["date_scope"]
    attachment_section = (
        "<section><h2>Trip documents</h2><ul>" + document_links + "</ul></section>"
        if document_links
        else ""
    )
    saved_places: list[str] = []
    for saved in snapshot.get("saved_places", []):
        _check_deadline(started)
        place = saved["place"]
        fields = [place.get("name"), place.get("address"), place.get("category")]
        if saved.get("note"):
            fields.append(saved["note"])
        saved_places.append(
            "<li>"
            + html.escape(" · ".join(value for value in fields if value))
            + ("<br>" + _place_credit_markup(place) if _place_credit_markup(place) else "")
            + "</li>"
        )
    saved_places_markup = (
        "<section><h2>Saved place candidates</h2><ul>" + "".join(saved_places) + "</ul></section>"
        if saved_places
        else ""
    )
    reservations_markup = (
        "<ul>" + "".join(reservations) + "</ul>"
        if reservations
        else "<p>No reservations in this date scope.</p>"
    )
    output = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · Travel snapshot</title>
<style>
:root{{color-scheme:light dark;font:16px/1.55 system-ui,sans-serif}}
body{{margin:0 auto;padding:1.25rem;max-width:54rem}}
header,section{{margin:0 0 1.25rem;padding:1rem;border:1px solid #8885;border-radius:.8rem}}
h1,h2,p{{margin:.2rem 0 .65rem}}
.notice{{border-left:.35rem solid #c98218;padding:.5rem .8rem}}
ol,ul{{padding-left:1.3rem}}
li{{margin:.65rem 0}}
li>time{{font-variant-numeric:tabular-nums;font-weight:700;display:block}}
.warning{{color:#cf7a18}}
.meta{{font-size:.9rem;opacity:.8}}
@media print{{
body{{max-width:none;padding:0}}
header,section{{break-inside:avoid}}
a{{color:inherit;text-decoration:none}}
}}
</style>
</head>
<body>
<header>
<p class="meta">STATIC TRIP SNAPSHOT · revision {metadata["trip_revision"]}</p>
<h1>{title}</h1>
<p>{html.escape(trip["start_date"])} – {html.escape(trip["end_date"])} ·
{html.escape(trip["timezone"])}</p>
<p class="meta">Date scope: {html.escape(scope["start_date"])} –
{html.escape(scope["end_date"])} · {privacy} · documents
{"included" if metadata["documents_included"] else "omitted"}</p>
<p class="notice">{freshness}</p>
<p class="meta">Generated {generated}</p>
</header>
{"".join(sections)}
<section><h2>Reservations</h2>{reservations_markup}</section>
{saved_places_markup}
{attachment_section}
<footer class="meta">
<p>Route estimates and live availability are not included. Regenerate this
file after trip changes.</p>
</footer>
</body>
</html>"""
    result = output.encode("utf-8")
    _check_deadline(started)
    return result


def _bounded_json_bytes(value: Any, started: float) -> bytes:
    encoder = json.JSONEncoder(ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    output = bytearray()
    for part in encoder.iterencode(value):
        _check_deadline(started)
        encoded = part.encode("utf-8")
        if len(output) + len(encoded) > MAX_EXPORT_BYTES:
            raise _export_too_large()
        output.extend(encoded)
    return bytes(output)


def _check_projection_text(value: Any, started: float) -> None:
    stack = [value]
    char_count = 0
    while stack:
        _check_deadline(started)
        current = stack.pop()
        if isinstance(current, str):
            char_count += len(current)
            if char_count > MAX_EXPORT_SOURCE_CHARS:
                raise _export_too_large()
        elif isinstance(current, dict):
            stack.extend(current.keys())
            stack.extend(current.values())
        elif isinstance(current, (list, tuple)):
            stack.extend(current)


def _reservation_date_scope(
    *,
    owner_id: str,
    trip_id: UUID,
    trip_start_date: date,
    trip_end_date: date,
    start_date: date,
    end_date: date,
    timezone_name: str,
) -> Any:
    """Build the SQL date filter using point-event and half-open interval rules."""
    zone = ZoneInfo(timezone_name)
    scope_start = datetime.combine(start_date, local_time.min, zone).astimezone(UTC)
    scope_end = datetime.combine(end_date + timedelta(days=1), local_time.min, zone).astimezone(UTC)
    point_start = or_(
        and_(Reservation.starts_at.is_not(None), Reservation.ends_at.is_(None)),
        and_(Reservation.starts_at.is_not(None), Reservation.ends_at == Reservation.starts_at),
    )
    point_end = and_(Reservation.starts_at.is_(None), Reservation.ends_at.is_not(None))
    interval = and_(
        Reservation.starts_at.is_not(None),
        Reservation.ends_at.is_not(None),
        Reservation.starts_at < Reservation.ends_at,
    )
    date_conditions = [
        and_(
            point_start,
            Reservation.starts_at >= scope_start,
            Reservation.starts_at < scope_end,
        ),
        and_(point_end, Reservation.ends_at >= scope_start, Reservation.ends_at < scope_end),
        and_(interval, Reservation.starts_at < scope_end, Reservation.ends_at > scope_start),
    ]
    if start_date == trip_start_date and end_date == trip_end_date:
        date_conditions.append(and_(Reservation.starts_at.is_(None), Reservation.ends_at.is_(None)))
    return and_(
        Reservation.owner_id == owner_id,
        Reservation.trip_id == trip_id,
        or_(*date_conditions),
    )


def _text_char_count(
    session: Session,
    model: Any,
    fields: tuple[Any, ...],
    *criteria: Any,
    join: Any | None = None,
) -> int:
    expression = sum(
        (func.coalesce(func.length(field), 0) for field in fields),
        start=0,
    )
    statement = select(func.coalesce(func.sum(expression), 0)).select_from(model)
    if join is not None:
        statement = statement.join(join)
    return int(session.scalar(statement.where(*criteria)) or 0)


def _export_too_large() -> DomainError:
    return DomainError(
        "export_too_large",
        "This snapshot exceeds the 10 MiB export limit. Choose a smaller date range.",
        status_code=413,
    )


def _append_ics_lines(lines: list[str], additions: list[str], started: float) -> int:
    added_bytes = 0
    for line in additions:
        _check_deadline(started)
        folded = _fold_ical_line(line)
        added_bytes += len(folded.encode("utf-8")) + 2
        if added_bytes > MAX_EXPORT_BYTES:
            raise _export_too_large()
        lines.append(line)
    return added_bytes


def _format_schedule(start: str | None, end: str | None, flexible: str) -> str:
    if start is None and end is None:
        return flexible
    if start is None:
        return end or flexible
    if end is None or end == start:
        return start
    return f"{start}–{end}"


def _place_credit(place: dict[str, Any] | None) -> str | None:
    if place is None:
        return None
    fields = [
        place.get("provider_source_attribution"),
        place.get("provider_source_license"),
        place.get("provider_source_name"),
    ]
    source_url = place.get("provider_source_url")
    if source_url:
        try:
            valid_url = validate_http_url(source_url)
        except ValueError:
            valid_url = None
        if valid_url:
            fields.append(f"Source: {valid_url}")
    return " · ".join(value for value in fields if value) or None


def _place_credit_markup(place: dict[str, Any] | None) -> str:
    if place is None or not any(
        place.get(field)
        for field in (
            "provider_source_attribution",
            "provider_source_license",
            "provider_source_name",
            "provider_source_url",
        )
    ):
        return ""
    fields = [
        html.escape(value)
        for value in (
            place.get("provider_source_attribution"),
            place.get("provider_source_license"),
            place.get("provider_source_name"),
        )
        if value
    ]
    source_url = place.get("provider_source_url")
    if source_url:
        try:
            safe_url = validate_http_url(source_url)
        except ValueError:
            safe_url = None
        if safe_url:
            fields.append(
                f'<a href="{html.escape(safe_url, quote=True)}" rel="noreferrer">Source</a>'
            )
    return '<span class="source-credit">' + " · ".join(fields) + "</span>"


def _ics_escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\n", "\\n")
        .replace(",", "\\,")
        .replace(";", "\\;")
    )


def _fold_ical_line(line: str) -> str:
    chunks: list[str] = []
    current: list[str] = []
    octets = 0
    for char in line:
        encoded_size = len(char.encode("utf-8"))
        if current and octets + encoded_size > 75:
            chunks.append("".join(current))
            current = [" "]
            octets = 1
        current.append(char)
        octets += encoded_size
    chunks.append("".join(current))
    return "\r\n".join(chunks)


def _ics_datetime(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    return parsed.strftime("%Y%m%dT%H%M%SZ")


def _ical_status(value: str) -> str:
    if value in {"confirmed", "booked", "planned", "completed"}:
        return "CONFIRMED"
    if value == "cancelled":
        return "CANCELLED"
    return "TENTATIVE"


def _place_location(place: dict[str, Any] | None) -> str | None:
    if place is None:
        return None
    return ", ".join(value for value in (place.get("name"), place.get("address")) if value) or None


def _safe_document_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9 ._-]", "_", value.replace("\\", "/").rsplit("/", 1)[-1])
    return cleaned.strip(" ._")[:120] or "travel-document"


def _download_name(snapshot: dict[str, Any], suffix: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", snapshot["trip"]["title"].lower()).strip("-")[:48]
    return f"{slug or 'trip'}-{suffix}"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _check_deadline(started: float) -> None:
    if time.monotonic() - started > EXPORT_SECONDS:
        raise _export_too_slow()


def _export_too_slow() -> DomainError:
    return DomainError(
        "export_too_slow",
        "The snapshot took too long to render. Choose a smaller date range.",
        status_code=413,
    )
