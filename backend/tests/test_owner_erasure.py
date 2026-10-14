"""Synthetic native PostgreSQL erasure tests; never use an application database."""

from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from personal_travel.models.auth import AuthIdentity
from personal_travel.models.import_source import BookingDeletionIntent, SourceAttachment
from personal_travel.models.owner_erasure import OwnerErasureLedger
from personal_travel.models.trip import Trip
from personal_travel.services.owner_erasure import (
    ErasureLedgerSnapshot,
    erase_owner_batch,
    freeze_owner,
    replay_erasure_ledger,
)
from personal_travel.services.source_store import LocalSourceStore
from scripts.secure_backup import BackupError, restore_backup


def seed(session: Session, owner: str) -> Trip:
    session.add(
        AuthIdentity(
            owner_id=owner,
            issuer="https://accounts.google.com",
            subject=owner,
            email=f"{owner}@example.invalid",
            status="active",
        )
    )
    session.flush()
    trip = Trip(
        owner_id=owner,
        title="Synthetic private trip",
        start_date=date(2026, 10, 14),
        end_date=date(2026, 10, 14),
        timezone="UTC",
        revision=1,
    )
    session.add(trip)
    session.flush()
    return trip


def test_freeze_rejects_stale_writes_and_resumes_bounded_owner_cleanup(
    database_engine: Engine, clean_database: None
) -> None:
    with Session(database_engine) as session, session.begin():
        trip = seed(session, "erased-owner")
        other = seed(session, "retained-owner")
        trip_id, other_id = trip.id, other.id
        freeze_owner(session, "erased-owner")
    with (
        Session(database_engine) as session,
        pytest.raises(IntegrityError, match="owner is frozen"),
    ):
        session.add(
            Trip(
                owner_id="erased-owner",
                title="Must not return",
                start_date=date(2026, 10, 14),
                end_date=date(2026, 10, 14),
                timezone="UTC",
                revision=1,
            )
        )
        session.commit()
    with Session(database_engine) as session, session.begin():
        result = erase_owner_batch(session, "erased-owner", store=None, limit=1)
        assert result.state == "completed"
        assert session.get(Trip, trip_id) is None
        assert session.get(Trip, other_id) is not None
        assert session.get(AuthIdentity, "erased-owner") is None
        assert session.get(OwnerErasureLedger, "erased-owner") is not None


def test_blob_cleanup_pending_and_upstream_pending_are_not_completion(
    database_engine: Engine, clean_database: None, tmp_path: Path
) -> None:
    store = LocalSourceStore(tmp_path / "private")
    try:
        key = "a" * 32
        (store.root / key).write_bytes(b"synthetic")
        with Session(database_engine) as session, session.begin():
            trip = seed(session, "pending-owner")
            session.add(
                SourceAttachment(
                    owner_id=trip.owner_id,
                    trip_id=trip.id,
                    purpose="trip_attachment",
                    object_key=key,
                    sha256="1" * 64,
                    media_type="text/plain",
                    byte_size=9,
                    display_filename="synthetic.txt",
                    state="ready",
                )
            )
            session.add(
                BookingDeletionIntent(
                    owner_id=trip.owner_id,
                    extraction_key=uuid4(),
                    source_sha256="2" * 64,
                )
            )
            session.flush()
            freeze_owner(session, trip.owner_id)
        with Session(database_engine) as session, session.begin():
            blocked = erase_owner_batch(session, "pending-owner", store=None)
            assert blocked.state == "blob_cleanup_pending" and blocked.upstream_pending == 1
        with Session(database_engine) as session, session.begin():
            result = erase_owner_batch(session, "pending-owner", store=store)
            assert result.state == "pending" and result.upstream_pending == 1
            assert not (store.root / key).exists()
            assert session.get(AuthIdentity, "pending-owner") is not None
    finally:
        store.close()


def test_current_marker_replay_erases_an_older_snapshot_and_retains_other_owner(
    database_engine: Engine, clean_database: None, tmp_path: Path
) -> None:
    store = LocalSourceStore(tmp_path / "restore-private")
    try:
        with Session(database_engine) as session, session.begin():
            restored = seed(session, "deleted-after-backup")
            other = seed(session, "other-restore-owner")
            restored_id, other_id = restored.id, other.id
            # A historical completion flag cannot certify canonical rows in a restore.
            session.add(
                OwnerErasureLedger(owner_id=restored.owner_id, completed_at=datetime.now(UTC))
            )
        with Session(database_engine) as session, session.begin():
            snapshot = ErasureLedgerSnapshot(
                observed_at=datetime.now(UTC), owners=("deleted-after-backup",)
            )
            assert replay_erasure_ledger(session, snapshot, store=store) == {
                "completed": 1,
                "pending": 0,
            }
            assert session.get(Trip, restored_id) is None
            assert session.get(Trip, other_id) is not None
    finally:
        store.close()


def test_restore_requires_current_markers_before_connecting_or_promoting(tmp_path: Path) -> None:
    with pytest.raises(BackupError, match="current independently retained"):
        restore_backup(
            artifact=tmp_path / "absent",
            identity=tmp_path / "absent",
            database_url="",
            store_destination=tmp_path / "must-not-exist",
        )
    assert not (tmp_path / "must-not-exist").exists()
