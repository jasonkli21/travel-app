from __future__ import annotations

import hashlib
import json
import os
import struct
import zlib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from pathlib import Path
from threading import Event
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Column, DateTime, MetaData, String, Table, create_engine, insert, select
from sqlalchemy.orm import Session

import personal_travel.services.trip_exports as trip_exports
from personal_travel.api.routes.attachments import _promote_or_recover
from personal_travel.services.attachment_validation import (
    AttachmentValidationError,
    _validate_jpeg,
    _validate_png,
    validate_attachment,
)
from personal_travel.services.errors import DomainError
from personal_travel.services.source_store import LocalSourceStore


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    )


def _png(compressed: bytes) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + _png_chunk(b"IDAT", compressed)
        + _png_chunk(b"IEND", b"")
    )


def test_png_requires_valid_compressed_scanlines_and_one_header() -> None:
    _validate_png(_png(zlib.compress(b"\x00\xff\x00\x00")))

    with pytest.raises(AttachmentValidationError, match="invalid_png"):
        _validate_png(_png(b"not a zlib stream"))

    header = _png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    duplicate_header = (
        b"\x89PNG\r\n\x1a\n"
        + header
        + header
        + _png_chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00"))
        + _png_chunk(b"IEND", b"")
    )
    with pytest.raises(AttachmentValidationError, match="invalid_png"):
        _validate_png(duplicate_header)


def test_jpeg_accepts_real_image_and_rejects_empty_component_scan() -> None:
    # 1x1 RGB JPEG generated from a synthetic pixel; no user data is involved.
    jpeg = (Path(__file__).parent / "fixtures" / "synthetic-image.jpg").read_bytes()
    _validate_jpeg(jpeg)

    scan_marker = jpeg.rfind(b"\xff\xda")
    scan_start = scan_marker + 2 + struct.unpack_from(">H", jpeg, scan_marker + 2)[0]
    end_marker = jpeg.find(b"\xff\xd9", scan_start)
    damaged_entropy = jpeg[:scan_start] + b"\x00" + jpeg[end_marker:]
    with pytest.raises(AttachmentValidationError, match="invalid_jpeg"):
        _validate_jpeg(damaged_entropy)

    progressive = jpeg.replace(b"\xff\xc0", b"\xff\xc2", 1)
    with pytest.raises(AttachmentValidationError, match="unsupported_jpeg_encoding"):
        _validate_jpeg(progressive)

    # SOF0 declares zero components; its SOS cannot select an image component.
    def segment(marker: int, body: bytes) -> bytes:
        return b"\xff" + bytes([marker]) + struct.pack(">H", len(body) + 2) + body

    malformed = (
        b"\xff\xd8"
        + segment(0xC0, b"\x08\x00\x01\x00\x01\x00")
        + segment(0xDA, b"\x01\x01\x00\x00\x3f\x00")
        + b"\x01\xff\xd9"
    )
    with pytest.raises(AttachmentValidationError, match="invalid_jpeg"):
        _validate_jpeg(malformed)


def test_image_upload_validation_runs_in_isolated_worker(tmp_path: Path) -> None:
    image = tmp_path / "synthetic.png"
    png = _png(zlib.compress(b"\x00\xff\x00\x00"))
    image.write_bytes(png)
    validate_attachment(image, "image/png", png)

    jpeg = (Path(__file__).parent / "fixtures" / "synthetic-image.jpg").read_bytes()
    image.write_bytes(jpeg)
    validate_attachment(image, "image/jpeg", jpeg)

    progressive = jpeg.replace(b"\xff\xc0", b"\xff\xc2", 1)
    image.write_bytes(progressive)
    with pytest.raises(AttachmentValidationError, match="unsupported_jpeg_encoding"):
        validate_attachment(image, "image/jpeg", progressive)

    invalid = _png(b"not a zlib stream")
    image.write_bytes(invalid)
    with pytest.raises(AttachmentValidationError, match="invalid_image"):
        validate_attachment(image, "image/png", invalid)


def test_upload_promotion_lock_keeps_partial_retry_bytes_private(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = LocalSourceStore(tmp_path / "private")
    payload = b"synthetic complete bytes"
    digest = hashlib.sha256(payload).hexdigest()
    writing = Event()
    resume = Event()

    def pause_after_partial_write(key: str, data: bytes) -> None:
        fd = store.open_temp(key)
        try:
            os.write(fd, data[:4])
            writing.set()
            assert resume.wait(3)
            os.write(fd, data[4:])
            os.fsync(fd)
        finally:
            os.close(fd)

    monkeypatch.setattr(store, "write_temp", pause_after_partial_write)

    class ReadyService:
        ready_calls = 0

        def mark_ready(self, *_args):
            self.ready_calls += 1
            return {"id": "synthetic"}

    service = ReadyService()
    owner = "owner"
    trip_id = uuid4()
    attachment_id = uuid4()
    key = store.new_key()

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(
                _promote_or_recover,
                store,
                key,
                payload,
                digest,
                len(payload),
                service,
                owner,
                trip_id,
                attachment_id,
            )
            assert writing.wait(3)
            assert store.temp_path(key).stat().st_size == 4
            retry = _promote_or_recover(
                store,
                key,
                payload,
                digest,
                len(payload),
                service,
                owner,
                trip_id,
                attachment_id,
            )
            assert retry == ("busy", None)
            assert store.temp_path(key).stat().st_size == 4
            resume.set()
            state, metadata = first.result(timeout=3)

        assert state == "ready"
        assert metadata == {"id": "synthetic"}
        assert service.ready_calls == 1
        assert store.matches(key, digest, len(payload))
        assert not store.entry_stat(key + ".tmp")
    finally:
        resume.set()
        store.close()

    # Retained lock files keep a stable inode for future cross-process users.
    assert len(list((tmp_path / "private").glob("*.lock"))) == 1


def test_reservation_scope_uses_trip_local_half_open_days_and_point_events() -> None:
    engine = create_engine("sqlite://")
    metadata = MetaData()
    rows = Table(
        "reservations",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("owner_id", String(128), nullable=False),
        Column("trip_id", String(36), nullable=False),
        Column("starts_at", DateTime(timezone=True)),
        Column("ends_at", DateTime(timezone=True)),
    )
    metadata.create_all(engine)
    trip_id = uuid4()
    start = datetime(2026, 11, 1, 7, tzinfo=UTC)
    end = datetime(2026, 11, 2, 8, tzinfo=UTC)
    fixtures = {
        "start_before": (start - trip_exports.timedelta(minutes=1), None),
        "start_at": (start, None),
        "end_before": (None, end - trip_exports.timedelta(minutes=1)),
        "end_after": (None, end),
        "crossing": (
            start - trip_exports.timedelta(minutes=1),
            start + trip_exports.timedelta(minutes=1),
        ),
        "ending_at_start": (start - trip_exports.timedelta(minutes=1), start),
        "unscheduled": (None, None),
    }
    row_ids = {key: uuid4() for key in fixtures}
    with engine.begin() as connection:
        connection.execute(
            insert(rows),
            [
                {
                    "id": str(row_ids[key]),
                    "owner_id": "owner",
                    "trip_id": trip_id.hex,
                    "starts_at": begin,
                    "ends_at": finish,
                }
                for key, (begin, finish) in fixtures.items()
            ],
        )

    scope = trip_exports._reservation_date_scope(
        owner_id="owner",
        trip_id=trip_id,
        trip_start_date=date(2026, 10, 31),
        trip_end_date=date(2026, 11, 2),
        start_date=date(2026, 11, 1),
        end_date=date(2026, 11, 1),
        timezone_name="America/Los_Angeles",
    )
    with Session(engine) as session:
        selected = set(session.scalars(select(trip_exports.Reservation.id).where(scope)))
    assert selected == {row_ids[key] for key in ("start_at", "end_before", "crossing")}

    full_trip_scope = trip_exports._reservation_date_scope(
        owner_id="owner",
        trip_id=trip_id,
        trip_start_date=date(2026, 10, 31),
        trip_end_date=date(2026, 11, 2),
        start_date=date(2026, 10, 31),
        end_date=date(2026, 11, 2),
        timezone_name="America/Los_Angeles",
    )
    with Session(engine) as session:
        full_trip = set(session.scalars(select(trip_exports.Reservation.id).where(full_trip_scope)))
    assert row_ids["unscheduled"] in full_trip
    engine.dispose()


def _export_snapshot(*, matching_schedule: bool = True, include_private: bool = True):
    reservation_id = str(UUID("22222222-2222-4222-8222-222222222222"))
    place = {
        "id": "provider-place",
        "name": "Synthetic lodging",
        "address": "1 Example St",
        "provider_source_name": "Geoapify",
        "provider_source_attribution": "Provider credit <script>alert(1)</script>",
        "provider_source_license": "Synthetic license",
        "provider_source_url": "https://example.test/source",
    }
    item_start = "2026-12-01T18:00:00Z" if matching_schedule else None
    item_end = "2026-12-03T10:00:00Z" if matching_schedule else None
    item = {
        "id": "11111111-1111-4111-8111-111111111111",
        "title": "Hotel anchor",
        "item_type": "lodging",
        "status": "booked",
        "start_time": "10:00" if matching_schedule else None,
        "end_time": "18:00" if matching_schedule else None,
        "starts_at_utc": item_start,
        "ends_at_utc": item_end,
        "date": "2026-12-01",
        "place": place,
        "reservation_id": reservation_id,
        "notes": "<script>item note</script>" if include_private else None,
    }
    reservation = {
        "id": reservation_id,
        "reservation_type": "lodging",
        "status": "confirmed",
        "provider_name": "Example Hotel",
        "start_date": "2026-12-01",
        "start_time": "10:00:00",
        "end_date": "2026-12-03",
        "end_time": "02:00:00",
        "starts_at_utc": "2026-12-01T18:00:00Z",
        "ends_at_utc": "2026-12-03T10:00:00Z",
        "place": place,
        "conflicts": [],
        "confirmation_code": "CONFIRM-SENTINEL" if include_private else None,
        "source_reference": "SOURCE-SENTINEL" if include_private else None,
        "notes": "<script>reservation note</script>" if include_private else None,
    }
    return {
        "metadata": {
            "generated_at": "2026-10-05T12:00:00+00:00",
            "trip_revision": 7,
            "date_scope": {"start_date": "2026-12-01", "end_date": "2026-12-03"},
            "private_fields_included": include_private,
            "documents_included": True,
            "freshness_notice": "Static synthetic snapshot",
        },
        "trip": {
            "title": "Synthetic Trip",
            "start_date": "2026-12-01",
            "end_date": "2026-12-03",
            "timezone": "America/Los_Angeles",
        },
        "days": [
            {
                "id": "day-id",
                "day_index": 1,
                "date": "2026-12-01",
                "title": None,
                "items": [item],
            }
        ],
        "reservations": [reservation],
        "saved_places": [
            {"place": place, "note": "<script>saved note</script>" if include_private else None}
        ],
        "attachments": [
            {
                "id": "33333333-3333-4333-8333-333333333333",
                "reservation_id": reservation_id,
                "reservation_label": "Example Hotel",
                "display_filename": "receipt.pdf",
                "archive_path": "attachments/01-receipt.pdf",
            }
        ],
        "included_sections": ["saved_places", "attachments"],
    }


def test_ics_keeps_authoritative_booking_data_when_deduplicating() -> None:
    snapshot = _export_snapshot()
    calendar = trip_exports._render_ics(snapshot, False, trip_exports.time.monotonic())
    text = calendar.decode("utf-8").replace("\r\n ", "")
    assert text.count("BEGIN:VEVENT") == 1
    assert "DTSTART:20261201T180000Z" in text
    assert "DTEND:20261203T100000Z" in text
    assert "CONFIRM-SENTINEL" in text
    assert "SOURCE-SENTINEL" in text
    assert "Reservation notes" in text
    assert "Provider credit" in text
    assert "X-TRAVEL-OMITTED-SECTIONS:SAVED-PLACES" in text

    include_both = trip_exports._render_ics(snapshot, True, trip_exports.time.monotonic())
    assert include_both.decode("utf-8").count("BEGIN:VEVENT") == 2

    snapshot["days"][0]["items"][0]["starts_at_utc"] = None
    snapshot["days"][0]["items"][0]["ends_at_utc"] = None
    mismatch = trip_exports._render_ics(snapshot, False, trip_exports.time.monotonic()).decode(
        "utf-8"
    )
    assert mismatch.count("BEGIN:VEVENT") == 2
    assert "DTSTART:20261201T180000Z" in mismatch


def test_html_includes_opted_in_fields_saved_places_and_safe_attribution() -> None:
    snapshot = _export_snapshot()
    html = trip_exports._render_html(snapshot, trip_exports.time.monotonic()).decode("utf-8")
    assert "SOURCE-SENTINEL" in html
    assert "CONFIRM-SENTINEL" in html
    assert "saved note" in html
    assert 'data-attachment-id="33333333-3333-4333-8333-333333333333"' in html
    assert "Linked to Example Hotel" in html
    assert 'href="https://example.test/source"' in html
    assert "Provider credit &lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<script>" not in html

    private_default = _export_snapshot(include_private=False)
    output = trip_exports._render_html(private_default, trip_exports.time.monotonic()).decode(
        "utf-8"
    )
    assert "SOURCE-SENTINEL" not in output
    assert "CONFIRM-SENTINEL" not in output
    assert "saved note" not in output


def test_export_schedule_and_render_deadlines_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    assert trip_exports._format_schedule(None, None, "Flexible") == "Flexible"
    assert trip_exports._format_schedule("10:00", None, "Flexible") == "10:00"
    assert trip_exports._format_schedule(None, "10:00", "Flexible") == "10:00"
    assert trip_exports._format_schedule("10:00", "10:00", "Flexible") == "10:00"
    assert trip_exports._format_schedule("10:00", "11:00", "Flexible") == "10:00–11:00"

    monkeypatch.setattr(trip_exports, "MAX_EXPORT_BYTES", 8)
    with pytest.raises(DomainError, match="10 MiB"):
        trip_exports._bounded_json_bytes({"body": "x" * 20}, trip_exports.time.monotonic())

    monkeypatch.setattr(trip_exports.time, "monotonic", lambda: 100.0)
    with pytest.raises(DomainError, match="too long"):
        trip_exports._bounded_json_bytes({"small": True}, started=0)


def test_export_rendering_runs_in_a_bounded_worker_process() -> None:
    snapshot = {
        "metadata": {"generated_at": "2026-10-05T12:00:00+00:00", "trip_revision": 3},
        "trip": {"title": "Worker snapshot"},
        "days": [],
        "reservations": [],
        "saved_places": [],
    }
    projection = trip_exports._Projection(snapshot, (), trip_exports.time.monotonic())

    artifact = trip_exports.render_export(
        projection,
        export_format="json",
        include_documents=False,
        include_linked_reservations=False,
    )

    assert artifact.filename == "worker-snapshot-trip.json"
    assert json.loads(artifact.data) == snapshot
