"""Exercise an encrypted PostgreSQL/private-store backup and isolated restore.

The source database must be migrated and empty of business data. The destination
must be a separate empty database. This is intended for CI and disposable local
databases only; it does not inspect or modify any cloud resources.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from personal_travel.api.schemas.attachments import TripExportRequest
from personal_travel.models import (
    BookingImport,
    ItineraryItem,
    ItineraryProposal,
    Place,
    Reservation,
    SourceAttachment,
    Trip,
    TripDay,
)
from personal_travel.services.trip_exports import build_export, render_export
from scripts.secure_backup import (
    BackupError,
    _current_revision,
    _private_attachment_references,
    _validate_private_reference_inventory,
    create_backup,
    restore_backup,
    verify_backup,
)

OWNER_ID = "phase9-recovery-smoke-owner"
TRIP_TITLE = "Synthetic recovery smoke trip"
ITEM_TITLE = "Synthetic recovery itinerary item"
ATTACHMENT_BYTES = b"synthetic trip attachment for recovery"
BOOKING_BYTES = b"synthetic booking source for recovery"


def _database_has_business_data(database_url: str) -> bool:
    tables = (
        "trips",
        "places",
        "reservations",
        "itinerary_proposals",
        "booking_imports",
        "source_attachments",
    )
    engine = create_engine(database_url, pool_size=1, max_overflow=0)
    try:
        with engine.connect() as connection:
            return any(
                int(connection.scalar(text(f"SELECT count(*) FROM {table}")) or 0) > 0
                for table in tables
            )
    finally:
        engine.dispose()


def _seed_source(database_url: str, store_dir: Path) -> tuple[str, dict[str, str]]:
    attachment_key = uuid4().hex
    booking_key = uuid4().hex
    attachment_hash = hashlib.sha256(ATTACHMENT_BYTES).hexdigest()
    booking_hash = hashlib.sha256(BOOKING_BYTES).hexdigest()
    (store_dir / attachment_key).write_bytes(ATTACHMENT_BYTES)
    (store_dir / booking_key).write_bytes(BOOKING_BYTES)

    engine = create_engine(database_url, pool_size=1, max_overflow=0)
    try:
        with Session(engine, expire_on_commit=False) as session, session.begin():
            trip = Trip(
                owner_id=OWNER_ID,
                title=TRIP_TITLE,
                start_date=date(2027, 3, 1),
                end_date=date(2027, 3, 1),
                timezone="UTC",
                revision=3,
            )
            place = Place(owner_id=OWNER_ID, name="Synthetic recovery place")
            session.add_all([trip, place])
            session.flush()
            day = TripDay(trip_id=trip.id, day_index=1, date=trip.start_date)
            reservation = Reservation(
                owner_id=OWNER_ID,
                trip_id=trip.id,
                reservation_type="lodging",
                status="confirmed",
                provider_name="Synthetic recovery provider",
                place_id=place.id,
            )
            session.add_all([day, reservation])
            session.flush()
            session.add(
                ItineraryItem(
                    trip_day_id=day.id,
                    place_id=place.id,
                    reservation_id=reservation.id,
                    item_type="activity",
                    title=ITEM_TITLE,
                    sort_order=0,
                    status="planned",
                )
            )
            attachment = SourceAttachment(
                owner_id=OWNER_ID,
                trip_id=trip.id,
                reservation_id=reservation.id,
                purpose="trip_attachment",
                object_key=attachment_key,
                sha256=attachment_hash,
                media_type="text/plain",
                byte_size=len(ATTACHMENT_BYTES),
                display_filename="synthetic-receipt.txt",
                state="ready",
            )
            booking_source = SourceAttachment(
                owner_id=OWNER_ID,
                trip_id=trip.id,
                reservation_id=reservation.id,
                purpose="booking_source",
                object_key=booking_key,
                sha256=booking_hash,
                media_type="text/plain",
                byte_size=len(BOOKING_BYTES),
                display_filename="synthetic-booking.txt",
                state="ready",
            )
            session.add_all([attachment, booking_source])
            session.flush()
            session.add(
                BookingImport(
                    owner_id=OWNER_ID,
                    trip_id=trip.id,
                    source_id=booking_source.id,
                    request_key="recovery-smoke-request",
                    request_fingerprint="1" * 64,
                    source_sha256=booking_hash,
                    source_media_type="text/plain",
                    source_byte_size=len(BOOKING_BYTES),
                    state="review_ready",
                    parser_version="recovery-smoke-v1",
                    review_revision=2,
                    retention_choice="keep_until_expiry",
                    candidate_snapshot={"synthetic": True},
                )
            )
            session.add(
                ItineraryProposal(
                    owner_id=OWNER_ID,
                    trip_id=trip.id,
                    idempotency_key=uuid4(),
                    downstream_key=uuid4(),
                    request_fingerprint="2" * 64,
                    state="failed",
                    schema_version="itinerary-proposal-v1",
                    policy_version="itinerary-proposal-policy-v1",
                    upstream_revision="3" * 40,
                    support_mode="context_only",
                    trip_handle="h_recovery_smoke_trip",
                    generation_deadline=datetime(2027, 2, 1, tzinfo=UTC),
                    base_trip_revision=trip.revision,
                    base_place_revisions=[],
                    base_snapshot={"owner_id": OWNER_ID, "trip_id": str(trip.id)},
                    citations=[],
                )
            )
            trip_id = str(trip.id)
    finally:
        engine.dispose()
    return trip_id, {"attachment": attachment_key, "booking": booking_key}


def _age_recipient(identity: Path) -> str:
    result = subprocess.run(
        ["age-keygen", "-o", str(identity)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode != 0:
        raise BackupError("Could not create a temporary age identity for the recovery smoke.")
    os.chmod(identity, 0o600)
    public_key = next(
        (
            line.removeprefix("# public key: ").strip()
            for line in identity.read_text(encoding="utf-8").splitlines()
            if line.startswith("# public key: age1")
        ),
        "",
    )
    if re.fullmatch(r"age1[0-9a-z]+", public_key) is None:
        raise BackupError("The temporary age identity did not contain a valid recipient.")
    return public_key


def _assert_restored_invariants(
    database_url: str, store_dir: Path, trip_id: str, keys: dict[str, str]
) -> None:
    engine = create_engine(database_url, pool_size=1, max_overflow=0)
    try:
        factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
        with Session(engine) as session:
            trip = session.scalar(
                select(Trip).where(Trip.id == UUID(trip_id), Trip.owner_id == OWNER_ID)
            )
            assert trip is not None, "restored owner trip is missing"
            assert trip.title == TRIP_TITLE and trip.revision == 3
            assert len(trip.days) == 1 and len(trip.days[0].items) == 1
            assert trip.days[0].items[0].title == ITEM_TITLE
            assert (
                session.scalar(
                    select(Reservation.id).where(
                        Reservation.trip_id == trip.id,
                        Reservation.provider_name == "Synthetic recovery provider",
                    )
                )
                is not None
            )
            assert (
                session.scalar(
                    select(ItineraryProposal.id).where(
                        ItineraryProposal.trip_id == trip.id,
                        ItineraryProposal.policy_version == "itinerary-proposal-policy-v1",
                    )
                )
                is not None
            )
            booking = session.scalar(
                select(BookingImport).where(
                    BookingImport.trip_id == trip.id,
                    BookingImport.request_key == "recovery-smoke-request",
                )
            )
            assert booking is not None and booking.state == "review_ready"
            assert booking.source_id is not None

        attachments = _private_attachment_references(database_url)
        consistency = _validate_private_reference_inventory(
            attachments,
            [
                {
                    "path": key,
                    "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
                for key, data in (
                    (keys["attachment"], ATTACHMENT_BYTES),
                    (keys["booking"], BOOKING_BYTES),
                )
            ],
        )
        assert consistency["ready"] == 2 and consistency["unreferenced_objects"] == 0
        assert (store_dir / keys["attachment"]).read_bytes() == ATTACHMENT_BYTES
        assert (store_dir / keys["booking"]).read_bytes() == BOOKING_BYTES

        projection = build_export(
            factory,
            owner_id=OWNER_ID,
            trip_id=trip.id,
            options=TripExportRequest(format="json"),
        )
        export = render_export(
            projection,
            export_format="json",
            include_documents=False,
            include_linked_reservations=False,
        )
        exported = json.loads(export.data)
        assert exported["days"][0]["items"][0]["title"] == ITEM_TITLE
    finally:
        engine.dispose()


def run() -> dict[str, object]:
    source_url = os.environ.get("RECOVERY_SOURCE_DATABASE_URL", "")
    destination_url = os.environ.get("RECOVERY_RESTORE_DATABASE_URL", "")
    if not source_url or not destination_url:
        raise BackupError(
            "Set RECOVERY_SOURCE_DATABASE_URL and RECOVERY_RESTORE_DATABASE_URL to "
            "separate disposable PostgreSQL databases."
        )
    if source_url == destination_url:
        raise BackupError("Recovery smoke source and restore databases must be different.")
    if _database_has_business_data(source_url):
        raise BackupError("Recovery smoke source has business data; use a fresh empty database.")
    if _current_revision(source_url) != "0015":
        raise BackupError("Recovery smoke source must be migrated to the current head, 0015.")

    with tempfile.TemporaryDirectory(prefix="travel-recovery-smoke-") as raw_dir:
        scratch = Path(raw_dir)
        store_dir = scratch / "source-store"
        store_dir.mkdir(mode=0o700)
        backup_dir = scratch / "backups"
        restored_store = scratch / "restored-store"
        identity = scratch / "age-identity.txt"
        trip_id, keys = _seed_source(source_url, store_dir)
        recipient = _age_recipient(identity)
        artifact = create_backup(
            database_url=source_url,
            store_dir=store_dir,
            output_dir=backup_dir,
            recipient=recipient,
            writes_stopped=True,
        )
        verified = verify_backup(artifact, identity)
        restored = restore_backup(
            artifact=artifact,
            identity=identity,
            database_url=destination_url,
            store_destination=restored_store,
        )
        _assert_restored_invariants(destination_url, restored_store, trip_id, keys)
        return {
            "backup_verified": verified["verified"],
            "source_revision": _current_revision(source_url),
            "restored_revision": restored["alembic_revision"],
            "row_counts": restored["row_counts"],
            "private_objects": restored["private_objects"],
            "private_references": restored["private_references"],
            "application_export": "passed",
        }


def main() -> int:
    try:
        print(json.dumps(run(), sort_keys=True, indent=2))
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {"error_type": type(exc).__name__, "error": "Recovery smoke did not complete."},
                sort_keys=True,
            ),
            file=os.sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
