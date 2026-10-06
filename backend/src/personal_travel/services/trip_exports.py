"""Bounded, revision-stamped trip snapshots and deterministic serializers."""

from __future__ import annotations

import hashlib
import html
import json
import re
import time
import uuid
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from io import BytesIO
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from personal_travel.api.schemas.attachments import TripExportRequest
from personal_travel.models.import_source import SourceAttachment
from personal_travel.models.place import Place
from personal_travel.repositories.trips import SqlAlchemyTripRepository
from personal_travel.services.conflicts import calculate_reservation_conflicts
from personal_travel.services.errors import DomainError, not_found
from personal_travel.services.time_utils import as_aware_utc, local_date_time_parts

SessionFactoryLike = Callable[[], Session]
MAX_EXPORT_RECORDS = 5_000
MAX_EXPORT_BYTES = 10 * 1024 * 1024
MAX_BUNDLE_DOCUMENT_BYTES = 25 * 1024 * 1024
MAX_BUNDLE_BYTES = MAX_EXPORT_BYTES + MAX_BUNDLE_DOCUMENT_BYTES + 1024 * 1024
EXPORT_SECONDS = 10.0


@dataclass(frozen=True, slots=True)
class ExportArtifact:
    data: bytes
    media_type: str
    filename: str


@dataclass(frozen=True, slots=True)
class _Projection:
    data: dict[str, Any]
    objects: tuple[tuple[str, str, str, int], ...]


def build_export(
    session_factory: SessionFactoryLike,
    *,
    owner_id: str,
    trip_id: UUID,
    options: TripExportRequest,
    generated_at: datetime | None = None,
) -> _Projection:
    generated = generated_at or datetime.now(UTC)
    with session_factory() as session, session.begin():
        trip = SqlAlchemyTripRepository(session).get(
            owner_id=owner_id, trip_id=trip_id, for_update=True
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

        place_ids = {
            place_id
            for place_id in (
                [item.place_id for day in trip.days for item in day.items]
                + [reservation.place_id for reservation in trip.reservations]
                + [saved.place_id for saved in trip.saved_places]
            )
            if place_id is not None
        }
        if place_ids:
            # Shared place edits have their own lock/revision. Lock all export
            # dependencies after the trip root in a deterministic UUID order.
            session.scalars(
                select(Place)
                .where(Place.owner_id == owner_id, Place.id.in_(place_ids))
                .order_by(Place.id)
                .with_for_update(read=True)
                .execution_options(populate_existing=True)
            ).all()

        day_rows = [
            day
            for day in sorted(trip.days, key=lambda item: item.day_index)
            if start_date <= day.date <= end_date
        ]
        conflict_map = calculate_reservation_conflicts(trip)
        selected_reservations = []
        zone = ZoneInfo(trip.timezone)
        for reservation in sorted(
            trip.reservations,
            key=lambda row: (
                row.starts_at is None,
                row.starts_at or datetime.max.replace(tzinfo=UTC),
                row.id,
            ),
        ):
            start_local = (
                as_aware_utc(reservation.starts_at).astimezone(zone).date()
                if reservation.starts_at is not None
                else None
            )
            end_local = (
                as_aware_utc(reservation.ends_at).astimezone(zone).date()
                if reservation.ends_at is not None
                else None
            )
            if start_local is not None and start_local > end_date:
                continue
            if end_local is not None and end_local < start_date:
                continue
            if start_local is None and start_date != trip.start_date:
                continue
            selected_reservations.append(reservation)

        selected_item_count = sum(len(day.items) for day in day_rows)
        if (
            selected_item_count > MAX_EXPORT_RECORDS
            or len(selected_reservations) > MAX_EXPORT_RECORDS
        ):
            raise DomainError(
                "export_too_large",
                "This date range has too many records for one snapshot.",
                status_code=413,
            )

        all_selected_items = [item for day in day_rows for item in day.items]
        all_selected_ids = (
            {item.place_id for item in all_selected_items if item.place_id is not None}
            | {
                reservation.place_id
                for reservation in selected_reservations
                if reservation.place_id is not None
            }
            | {saved.place_id for saved in trip.saved_places}
        )
        place_revisions = []
        if all_selected_ids:
            places = session.scalars(
                select(Place)
                .where(Place.owner_id == owner_id, Place.id.in_(all_selected_ids))
                .order_by(Place.id)
            ).all()
            place_revisions = [
                {"place_id": str(place.id), "revision": place.revision} for place in places
            ]

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
            ).all()
            total_document_bytes = sum(row.byte_size for row in rows)
            if total_document_bytes > MAX_BUNDLE_DOCUMENT_BYTES:
                raise DomainError(
                    "export_too_large",
                    "Trip documents exceed the 25 MiB bundle limit. Remove some documents first.",
                    status_code=413,
                )
            for index, row in enumerate(rows, start=1):
                filename = _safe_document_name(row.display_filename or "travel-document")
                archive_path = f"attachments/{index:02d}-{filename}"
                attachments.append(
                    {
                        "display_filename": filename,
                        "media_type": row.media_type,
                        "byte_size": row.byte_size,
                        "archive_path": archive_path,
                    }
                )
                object_rows.append((row.object_key, row.sha256, archive_path, row.byte_size))

        include_private = options.include_private_fields
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
            "days": [
                {
                    "id": str(day.id),
                    "day_index": day.day_index,
                    "date": day.date.isoformat(),
                    "title": day.title,
                    "items": [
                        _item_projection(item, trip.timezone, include_private)
                        for item in sorted(day.items, key=lambda row: row.sort_order)
                    ],
                }
                for day in day_rows
            ],
            "reservations": [
                _reservation_projection(
                    reservation,
                    trip.timezone,
                    include_private,
                    [
                        conflict
                        for conflict in conflict_map.get(reservation.id, [])
                        if start_date <= conflict.day.date <= end_date
                    ],
                )
                for reservation in selected_reservations
            ],
            "saved_places": [
                {
                    "place": _place_projection(saved.place),
                    **({"note": saved.note} if include_private and saved.note else {}),
                }
                for saved in sorted(trip.saved_places, key=lambda row: row.place.name.casefold())
            ],
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
        return _Projection(data, tuple(object_rows))


def render_export(
    projection: _Projection,
    *,
    export_format: str,
    include_documents: bool,
    include_linked_reservations: bool,
    store: Any | None = None,
) -> ExportArtifact:
    started = time.monotonic()
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
        body = json.dumps(
            snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
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
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        bundle.writestr(filename, body)
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
            bundle.writestr(archive_path, data)
            if archive.tell() > MAX_BUNDLE_BYTES:
                raise DomainError(
                    "export_too_large",
                    "The download bundle exceeds its size limit.",
                    status_code=413,
                )
    return ExportArtifact(
        archive.getvalue(),
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
    selected_linked_ids = {
        item["reservation_id"]
        for day in snapshot["days"]
        for item in day["items"]
        if item["reservation_id"] and item["status"] != "cancelled"
    }
    for day in snapshot["days"]:
        for item in day["items"]:
            _check_deadline(started)
            if item["status"] == "cancelled":
                continue
            lines.extend(_ics_item(item, day["date"], dtstamp, revision, metadata))
    for reservation in snapshot["reservations"]:
        _check_deadline(started)
        if reservation["status"] == "cancelled":
            continue
        if not include_linked_reservations and reservation["id"] in selected_linked_ids:
            continue
        if reservation["starts_at_utc"] is None and reservation["ends_at_utc"] is None:
            continue
        lines.extend(_ics_reservation(reservation, dtstamp, revision, metadata))
    lines.append("END:VCALENDAR")
    return ("\r\n".join(_fold_ical_line(line) for line in lines) + "\r\n").encode("utf-8")


def _ics_item(
    item: dict[str, Any], day_date: str, dtstamp: str, revision: int, metadata: dict[str, Any]
) -> list[str]:
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
        location=_place_location(item["place"]),
        description=item.get("notes") if metadata["private_fields_included"] else None,
        item_type=item["item_type"],
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
    if confirmation_code:
        description_parts.append(f"Confirmation: {confirmation_code}")
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
    document_links = "".join(
        f'<li><a href="{html.escape(item["archive_path"], quote=True)}">'
        f"{html.escape(item['display_filename'])}</a></li>"
        for item in snapshot.get("attachments", [])
    )
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
            when = item["start_time"] or "Flexible day plan"
            if item["end_time"]:
                when += f"–{item['end_time']}"
            details = [place_text, item["status"], item["item_type"]]
            if place and place.get("provider_source_attribution"):
                details.append(place["provider_source_attribution"])
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
        warnings = "".join(
            f'<li class="warning">{html.escape(conflict["reason"])} '
            f"({html.escape(conflict['date'])} · {html.escape(conflict['item_title'])})</li>"
            for conflict in reservation["conflicts"]
        )
        reservations.append(
            "<li><strong>"
            + html.escape(reservation["provider_name"])
            + "</strong><p>"
            + html.escape(" · ".join(detail_parts))
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
{attachment_section}
<footer class="meta">
<p>Route estimates and live availability are not included. Regenerate this
file after trip changes.</p>
</footer>
</body>
</html>"""
    return output.encode("utf-8")


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
        raise DomainError(
            "export_too_slow",
            "The snapshot took too long to render. Choose a smaller date range.",
            status_code=413,
        )
