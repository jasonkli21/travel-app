from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from personal_travel.auth.owner_migration import (
    OwnerMigrationRejected,
    _transfer_snapshot,
    apply_owner_migration,
    inspect_owner_migration,
    stable_owner_id,
)
from personal_travel.domain.proposals import ProposalTripSnapshot
from personal_travel.models import (
    AuthIdentity,
    BookingDeletionIntent,
    BookingImport,
    ItineraryItem,
    ItineraryProposal,
    OwnerMigrationAudit,
    Place,
    Reservation,
    SavedPlace,
    SourceAttachment,
    Trip,
    TripDay,
)
from personal_travel.services.proposal_preview import (
    build_proposal_snapshot,
    validate_proposal_snapshot,
)

pytestmark = pytest.mark.usefixtures("clean_database")


def target_identity(session: Session, subject: str = "synthetic-owner-subject") -> str:
    owner_id = stable_owner_id("https://accounts.google.com", subject)
    session.add(
        AuthIdentity(
            owner_id=owner_id,
            issuer="https://accounts.google.com",
            subject=subject,
            email="owner@gmail.com",
            status="active",
        )
    )
    session.flush()
    return owner_id


def source_graph(session: Session, *, provider_place_id: str | None = None) -> tuple[Trip, Place]:
    trip = Trip(
        owner_id="local",
        title="Synthetic local trip",
        start_date=datetime(2026, 1, 2, tzinfo=UTC).date(),
        end_date=datetime(2026, 1, 2, tzinfo=UTC).date(),
        timezone="UTC",
        revision=4,
    )
    place = Place(
        owner_id="local",
        name="Synthetic venue",
        provider="geoapify" if provider_place_id else None,
        provider_place_id=provider_place_id,
        revision=2,
    )
    session.add_all([trip, place])
    session.flush()
    day = TripDay(trip_id=trip.id, day_index=1, date=trip.start_date)
    session.add(day)
    session.flush()
    reservation = Reservation(
        owner_id="local",
        trip_id=trip.id,
        reservation_type="lodging",
        status="tentative",
        provider_name="Synthetic hotel",
        place_id=place.id,
    )
    session.add(reservation)
    session.flush()
    item = ItineraryItem(
        trip_day_id=day.id,
        place_id=place.id,
        reservation_id=reservation.id,
        item_type="lodging",
        title="Check in",
        sort_order=0,
        status="planned",
    )
    session.add(item)
    session.add(SavedPlace(owner_id="local", trip_id=trip.id, place_id=place.id))
    session.add(
        ItineraryProposal(
            owner_id="local",
            trip_id=trip.id,
            idempotency_key=uuid4(),
            downstream_key=uuid4(),
            request_fingerprint="f" * 64,
            state="failed",
            schema_version="itinerary-proposal-v1",
            policy_version="itinerary-proposal-policy-v2",
            upstream_revision="6" * 40,
            support_mode="context_only",
            trip_handle="h_triphandle000000001",
            generation_deadline=datetime(2026, 1, 1, tzinfo=UTC),
            base_trip_revision=trip.revision,
            base_place_revisions=[],
            base_snapshot={"owner_id": "local", "trip_id": str(trip.id)},
            citations=[],
        )
    )
    session.flush()
    return trip, place


def inspect(engine: Engine, target_owner_id: str):
    with Session(engine, expire_on_commit=False) as session, session.begin():
        return inspect_owner_migration(
            session,
            source_owner_id="local",
            target_owner_id=target_owner_id,
        )


def test_dry_run_counts_full_owner_graph_without_mutating(database_engine: Engine) -> None:
    with Session(database_engine) as session, session.begin():
        target = target_identity(session)
        source_graph(session, provider_place_id="synthetic-place")

    plan = inspect(database_engine, target)

    assert plan.can_apply, plan.conflicts
    assert plan.counts["trips"] == 1
    assert plan.counts["trip_days"] == 1
    assert plan.counts["itinerary_items"] == 1
    assert plan.counts["itinerary_items_linked_to_reservations"] == 1
    assert plan.counts["places"] == 1
    assert plan.counts["reservations"] == 1
    assert plan.counts["saved_places"] == 1
    assert plan.counts["itinerary_proposals"] == 1
    assert plan.counts["proposal_snapshot_owner_id"] == 1
    with Session(database_engine) as session:
        assert session.scalar(select(Trip.owner_id)) == "local"
        proposal = session.scalar(select(ItineraryProposal))
        assert proposal is not None
        assert proposal.base_snapshot["owner_id"] == "local"


def test_owner_migration_requires_verified_target_and_provider_collision_is_reported(
    database_engine: Engine,
) -> None:
    with Session(database_engine) as session, session.begin():
        source_graph(session, provider_place_id="duplicate-provider-key")
        session.add(
            Place(
                owner_id="usr_" + "a" * 32,
                name="Existing target place",
                provider="geoapify",
                provider_place_id="duplicate-provider-key",
            )
        )

    with Session(database_engine) as session, session.begin():
        with pytest.raises(OwnerMigrationRejected, match="active identity record"):
            inspect_owner_migration(
                session,
                source_owner_id="local",
                target_owner_id="usr_" + "a" * 32,
            )

    with Session(database_engine) as session, session.begin():
        target = target_identity(session)
        session.add(
            Place(
                owner_id=target,
                name="Existing target place",
                provider="geoapify",
                provider_place_id="duplicate-provider-key",
            )
        )

    plan = inspect(database_engine, target)
    assert plan.conflicts["target_provider_place_collision"] == 1
    assert not plan.can_apply


def test_owner_migration_reports_saved_place_and_proposal_key_collisions(
    database_engine: Engine,
) -> None:
    with Session(database_engine) as session, session.begin():
        target = target_identity(session)
        trip, place = source_graph(session)
        source_proposal = session.scalar(
            select(ItineraryProposal).where(ItineraryProposal.owner_id == "local")
        )
        assert source_proposal is not None
        session.add(SavedPlace(owner_id=target, trip_id=trip.id, place_id=place.id))
        session.add(
            ItineraryProposal(
                owner_id=target,
                trip_id=trip.id,
                idempotency_key=source_proposal.idempotency_key,
                downstream_key=uuid4(),
                request_fingerprint="t" * 64,
                state="failed",
                schema_version=source_proposal.schema_version,
                policy_version=source_proposal.policy_version,
                upstream_revision=source_proposal.upstream_revision,
                support_mode=source_proposal.support_mode,
                trip_handle=source_proposal.trip_handle,
                generation_deadline=source_proposal.generation_deadline,
                base_trip_revision=source_proposal.base_trip_revision,
                base_place_revisions=[],
                base_snapshot={"owner_id": target, "trip_id": str(trip.id)},
                citations=[],
            )
        )

    plan = inspect(database_engine, target)

    assert plan.conflicts["target_saved_place_collision"] == 1
    assert plan.conflicts["target_proposal_idempotency_collision"] == 1
    assert plan.conflicts["owner_trip_mismatch"] > 0
    assert not plan.can_apply


def test_explicit_owner_migration_updates_graph_and_snapshot_preserving_revisions(
    database_engine: Engine,
    tmp_path: Path,
) -> None:
    with Session(database_engine) as session, session.begin():
        target = target_identity(session)
        trip, place = source_graph(session, provider_place_id="synthetic-place")
        trip_id, place_id = trip.id, place.id

    plan = inspect(database_engine, target)
    backup = tmp_path / "synthetic-backup.sql"
    backup.write_bytes(b"synthetic disposable test database backup")
    backup_hash = hashlib.sha256(backup.read_bytes()).hexdigest()
    run_id = uuid4()
    with Session(database_engine, expire_on_commit=False) as session:
        result = apply_owner_migration(
            session,
            source_owner_id="local",
            target_owner_id=target,
            run_id=run_id,
            expected_plan_digest=plan.plan_digest,
            backup_file=backup,
            expected_backup_sha256=backup_hash,
            confirmation=f"local -> {target}",
        )

    assert result.plan_digest == plan.plan_digest
    with Session(database_engine) as session:
        assert session.get(Trip, trip_id).owner_id == target
        assert session.get(Trip, trip_id).revision == 4
        assert session.get(Place, place_id).owner_id == target
        assert session.get(Place, place_id).revision == 2
        assert session.scalar(select(Reservation.owner_id)) == target
        assert session.scalar(select(SavedPlace.owner_id)) == target
        proposal = session.scalar(select(ItineraryProposal))
        assert proposal is not None
        assert proposal.owner_id == target
        assert proposal.base_snapshot["owner_id"] == target
        assert session.get(OwnerMigrationAudit, run_id) is not None
        assert session.scalar(select(ItineraryItem.id)) is not None


def test_transfer_rewrites_complete_active_snapshot_and_invalidates_remote_generation(
    database_engine: Engine, tmp_path: Path
) -> None:
    with Session(database_engine) as session, session.begin():
        target = target_identity(session)
        trip, _ = source_graph(session)
        session.expire_all()
        trip = session.get(Trip, trip.id)
        assert trip is not None
        snapshot = jsonable_encoder(build_proposal_snapshot(trip, "local").model_dump(mode="json"))
        proposal = session.scalar(select(ItineraryProposal))
        assert proposal is not None
        proposal.base_snapshot = snapshot
        proposal.state = "outcome_unknown"
        _transfer_snapshot(proposal, "local", target)

    plan = inspect(database_engine, target)
    assert plan.can_apply, plan.conflicts
    backup = tmp_path / "synthetic-backup.sql"
    backup.write_bytes(b"synthetic disposable test database backup")
    with Session(database_engine) as session:
        apply_owner_migration(
            session,
            source_owner_id="local",
            target_owner_id=target,
            run_id=uuid4(),
            expected_plan_digest=plan.plan_digest,
            backup_file=backup,
            expected_backup_sha256=hashlib.sha256(backup.read_bytes()).hexdigest(),
            confirmation=f"local -> {target}",
        )
    with Session(database_engine) as session:
        proposal = session.scalar(select(ItineraryProposal))
        assert proposal is not None
        transferred = ProposalTripSnapshot.model_validate_json(json.dumps(proposal.base_snapshot))
        validate_proposal_snapshot(transferred)
        assert transferred.owner_id == target
        assert all(value.owner_id == target for value in transferred.places)
        assert all(value.owner_id == target for value in transferred.candidates)
        assert all(value.owner_id == target for value in transferred.reservations)
        assert all(item.owner_id == target for day in transferred.days for item in day.items)
        assert proposal.state == "failed"
        assert proposal.failure_code == "owner_migrated_remote_unrecoverable"


def test_transfer_rejects_foreign_nested_snapshot_owner(database_engine: Engine) -> None:
    with Session(database_engine) as session, session.begin():
        target = target_identity(session)
        trip, _ = source_graph(session)
        session.expire_all()
        trip = session.get(Trip, trip.id)
        assert trip is not None
        snapshot = jsonable_encoder(build_proposal_snapshot(trip, "local").model_dump(mode="json"))
        snapshot["places"][0]["owner_id"] = "foreign"
        proposal = session.scalar(select(ItineraryProposal))
        assert proposal is not None
        proposal.base_snapshot = snapshot
        proposal.state = "ready"
    plan = inspect(database_engine, target)
    assert plan.conflicts["proposal_snapshot_owner_mismatch"] == 1
    assert not plan.can_apply


def test_owner_migration_rejects_same_count_graph_changes_after_dry_run(
    database_engine: Engine,
    tmp_path: Path,
) -> None:
    with Session(database_engine) as session, session.begin():
        target = target_identity(session)
        trip, _ = source_graph(session)
        trip_id = trip.id

    plan = inspect(database_engine, target)
    with Session(database_engine) as session, session.begin():
        trip = session.get(Trip, trip_id)
        assert trip is not None
        trip.title = "Edited after dry run without a revision bump"

    backup = tmp_path / "synthetic-backup.sql"
    backup.write_bytes(b"synthetic disposable test database backup")
    backup_hash = hashlib.sha256(backup.read_bytes()).hexdigest()
    with Session(database_engine) as session:
        with pytest.raises(OwnerMigrationRejected, match="changed after dry run"):
            apply_owner_migration(
                session,
                source_owner_id="local",
                target_owner_id=target,
                run_id=uuid4(),
                expected_plan_digest=plan.plan_digest,
                backup_file=backup,
                expected_backup_sha256=backup_hash,
                confirmation=f"local -> {target}",
            )
    with Session(database_engine) as session:
        assert session.scalar(select(Trip.owner_id)) == "local"
        assert session.scalar(select(OwnerMigrationAudit.id)) is None


def test_owner_migration_mismatched_backup_plan_confirmation_and_rollback(
    database_engine: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with Session(database_engine) as session, session.begin():
        target = target_identity(session)
        source_graph(session)
    plan = inspect(database_engine, target)
    backup = tmp_path / "backup.sql"
    backup.write_bytes(b"synthetic backup")
    backup_hash = hashlib.sha256(backup.read_bytes()).hexdigest()

    with Session(database_engine) as session:
        with pytest.raises(OwnerMigrationRejected, match="does not match"):
            apply_owner_migration(
                session,
                source_owner_id="local",
                target_owner_id=target,
                run_id=uuid4(),
                expected_plan_digest=plan.plan_digest,
                backup_file=backup,
                expected_backup_sha256="0" * 64,
                confirmation=f"local -> {target}",
            )
    with Session(database_engine) as session:
        with pytest.raises(OwnerMigrationRejected, match="confirmation"):
            apply_owner_migration(
                session,
                source_owner_id="local",
                target_owner_id=target,
                run_id=uuid4(),
                expected_plan_digest=plan.plan_digest,
                backup_file=backup,
                expected_backup_sha256=backup_hash,
                confirmation="local -> arbitrary-owner",
            )

    from personal_travel.auth import owner_migration

    original_fingerprint = owner_migration._revision_fingerprint
    fingerprint_calls = 0

    def fail_after_write(session: Session) -> str:
        nonlocal fingerprint_calls
        fingerprint_calls += 1
        return original_fingerprint(session) if fingerprint_calls == 1 else "changed"

    monkeypatch.setattr(owner_migration, "_revision_fingerprint", fail_after_write)
    with Session(database_engine) as session:
        with pytest.raises(OwnerMigrationRejected, match="revisions"):
            apply_owner_migration(
                session,
                source_owner_id="local",
                target_owner_id=target,
                run_id=uuid4(),
                expected_plan_digest=plan.plan_digest,
                backup_file=backup,
                expected_backup_sha256=backup_hash,
                confirmation=f"local -> {target}",
            )
    with Session(database_engine) as session:
        assert session.scalar(select(Trip.owner_id)) == "local"
        assert session.scalar(select(OwnerMigrationAudit.id)) is None


def test_owner_migration_refuses_private_booking_graph_before_any_transfer(
    database_engine: Engine, tmp_path: Path
) -> None:
    with Session(database_engine) as session, session.begin():
        target = target_identity(session)
        trip, _ = source_graph(session)
        source = SourceAttachment(
            owner_id="local",
            trip_id=trip.id,
            object_key="a" * 32,
            sha256="a" * 64,
            media_type="text/plain",
            byte_size=1,
            display_filename="synthetic.txt",
            state="ready",
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
        session.add(source)
        session.flush()
        extraction_key = uuid4()
        session.add(
            BookingImport(
                owner_id="local",
                trip_id=trip.id,
                source_id=source.id,
                request_key="synthetic-private-key",
                request_fingerprint="b" * 64,
                source_sha256="a" * 64,
                source_media_type="text/plain",
                source_byte_size=1,
                state="received",
                parser_version="source-v1",
                review_revision=0,
                retention_choice="keep_until_expiry",
                extraction_key=extraction_key,
                extraction_post_attempted=True,
                extraction_text_sha256="c" * 64,
            )
        )
        session.add(
            BookingDeletionIntent(
                owner_id="local",
                extraction_key=uuid4(),
                source_sha256="d" * 64,
            )
        )

    plan = inspect(database_engine, target)
    assert not plan.can_apply
    assert plan.conflicts["private_records_require_separate_migration"] == 3
    assert plan.counts["source_attachments"] == 1
    assert plan.counts["booking_imports"] == 1
    assert plan.counts["booking_deletion_intents"] == 1

    backup = tmp_path / "private-graph-backup.sql"
    backup.write_bytes(b"synthetic test database backup")
    backup_hash = hashlib.sha256(backup.read_bytes()).hexdigest()
    with Session(database_engine) as session:
        with pytest.raises(OwnerMigrationRejected, match="conflicts"):
            apply_owner_migration(
                session,
                source_owner_id="local",
                target_owner_id=target,
                run_id=uuid4(),
                expected_plan_digest=plan.plan_digest,
                backup_file=backup,
                expected_backup_sha256=backup_hash,
                confirmation=f"local -> {target}",
            )
    with Session(database_engine) as session:
        assert session.scalar(select(Trip.owner_id)) == "local"
        assert session.scalar(select(SourceAttachment.owner_id)) == "local"
        assert session.scalar(select(BookingImport.owner_id)) == "local"
        assert session.scalar(select(BookingDeletionIntent.owner_id)) == "local"
        assert session.scalar(select(OwnerMigrationAudit.id)) is None
