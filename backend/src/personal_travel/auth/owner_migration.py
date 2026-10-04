"""Explicit, audited transfer of one local travel-data graph to a verified owner."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from personal_travel.db.base import Base
from personal_travel.models import (
    AuthIdentity,
    ItineraryProposal,
    OwnerMigrationAudit,
    Place,
    Trip,
)

OWNER_SCOPED_TABLES = (
    "trips",
    "places",
    "reservations",
    "saved_places",
    "itinerary_proposals",
)
LOCK_TABLES_SQL = (
    "LOCK TABLE trips, places, reservations, saved_places, itinerary_proposals, "
    "auth_identities, owner_migration_audits IN SHARE ROW EXCLUSIVE MODE"
)
MAX_BACKUP_BYTES = 16 * 1024 * 1024 * 1024


class OwnerMigrationRejected(ValueError):
    """The requested owner migration failed its explicit safety checks."""


@dataclass(frozen=True, slots=True)
class OwnerMigrationPlan:
    source_owner_id: str
    target_owner_id: str
    counts: dict[str, int]
    target_counts: dict[str, int]
    conflicts: dict[str, int]
    plan_digest: str

    @property
    def can_apply(self) -> bool:
        return not any(self.conflicts.values())

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_owner_id": self.source_owner_id,
            "target_owner_id": self.target_owner_id,
            "counts": self.counts,
            "target_counts": self.target_counts,
            "conflicts": self.conflicts,
            "plan_digest": self.plan_digest,
            "can_apply": self.can_apply,
        }


def stable_owner_id(issuer: str, subject: str) -> str:
    canonical_issuer = "https://accounts.google.com"
    if issuer not in {canonical_issuer, "accounts.google.com"} or not subject:
        raise OwnerMigrationRejected("The target identity mapping is invalid.")
    digest = hashlib.sha256(f"{canonical_issuer}\0{subject}".encode()).hexdigest()
    return f"usr_{digest[:32]}"


def _lock_graph(session: Session) -> None:
    session.execute(text(LOCK_TABLES_SQL))


def _verified_target(session: Session, target_owner_id: str) -> AuthIdentity:
    identity = session.get(AuthIdentity, target_owner_id)
    if (
        identity is None
        or identity.status != "active"
        or identity.owner_id != stable_owner_id(identity.issuer, identity.subject)
    ):
        raise OwnerMigrationRejected(
            "The target must have an active identity record created by verified Google sign-in."
        )
    return identity


def _count(session: Session, table_name: str, owner_id: str) -> int:
    table = Base.metadata.tables.get(table_name)
    if table is None:
        raise OwnerMigrationRejected(f"Required owner table {table_name} is unavailable.")
    return int(
        session.scalar(select(func.count()).select_from(table).where(table.c.owner_id == owner_id))
        or 0
    )


def _related_count(
    session: Session,
    statement: str,
    owner_id: str,
    *,
    target_owner_id: str | None = None,
) -> int:
    params = {"owner_id": owner_id}
    if target_owner_id is not None:
        params["target_owner_id"] = target_owner_id
    return int(session.scalar(text(statement), params) or 0)


def _plan_payload(
    source_owner_id: str,
    target_owner_id: str,
    counts: dict[str, int],
    target_counts: dict[str, int],
    conflicts: dict[str, int],
) -> bytes:
    payload = {
        "source_owner_id": source_owner_id,
        "target_owner_id": target_owner_id,
        "counts": counts,
        "target_counts": target_counts,
        "conflicts": conflicts,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def inspect_owner_migration(
    session: Session, *, source_owner_id: str, target_owner_id: str, acquire_lock: bool = True
) -> OwnerMigrationPlan:
    if not source_owner_id or not target_owner_id or source_owner_id == target_owner_id:
        raise OwnerMigrationRejected("Source and target owners must be present and different.")
    if acquire_lock:
        _lock_graph(session)
    _verified_target(session, target_owner_id)

    counts = {name: _count(session, name, source_owner_id) for name in OWNER_SCOPED_TABLES}
    target_counts = {name: _count(session, name, target_owner_id) for name in OWNER_SCOPED_TABLES}
    counts.update(
        {
            "trip_days": _related_count(
                session,
                "SELECT count(*) FROM trip_days d JOIN trips t ON t.id=d.trip_id "
                "WHERE t.owner_id=:owner_id",
                source_owner_id,
            ),
            "itinerary_items": _related_count(
                session,
                "SELECT count(*) FROM itinerary_items i JOIN trip_days d ON d.id=i.trip_day_id "
                "JOIN trips t ON t.id=d.trip_id WHERE t.owner_id=:owner_id",
                source_owner_id,
            ),
            "itinerary_items_linked_to_reservations": _related_count(
                session,
                "SELECT count(*) FROM itinerary_items i JOIN trip_days d ON d.id=i.trip_day_id "
                "JOIN trips t ON t.id=d.trip_id WHERE t.owner_id=:owner_id "
                "AND i.reservation_id IS NOT NULL",
                source_owner_id,
            ),
        }
    )

    source_proposals = session.scalars(
        select(ItineraryProposal).where(ItineraryProposal.owner_id == source_owner_id)
    ).all()
    mismatched_snapshots = sum(
        not isinstance(proposal.base_snapshot, dict)
        or proposal.base_snapshot.get("owner_id") != source_owner_id
        for proposal in source_proposals
    )
    counts["proposal_snapshot_owner_id"] = len(source_proposals) - mismatched_snapshots

    conflicts = {
        "proposal_snapshot_owner_mismatch": mismatched_snapshots,
        "target_provider_place_collision": _related_count(
            session,
            "SELECT count(*) FROM places source JOIN places target "
            "ON source.provider=target.provider "
            "AND source.provider_place_id=target.provider_place_id "
            "WHERE source.owner_id=:owner_id AND target.owner_id=:target_owner_id "
            "AND source.provider IS NOT NULL AND source.provider_place_id IS NOT NULL",
            source_owner_id,
            target_owner_id=target_owner_id,
        ),
        "owner_trip_mismatch": _related_count(
            session,
            "SELECT (SELECT count(*) FROM reservations r JOIN trips t ON t.id=r.trip_id "
            "WHERE r.owner_id=:owner_id AND t.owner_id<>:owner_id) + "
            "(SELECT count(*) FROM saved_places s JOIN trips t ON t.id=s.trip_id "
            "JOIN places p ON p.id=s.place_id WHERE s.owner_id=:owner_id "
            "AND (t.owner_id<>:owner_id OR p.owner_id<>:owner_id)) + "
            "(SELECT count(*) FROM itinerary_proposals p JOIN trips t ON t.id=p.trip_id "
            "WHERE p.owner_id=:owner_id AND t.owner_id<>:owner_id)",
            source_owner_id,
        ),
        "cross_owner_trip_place_reference": _related_count(
            session,
            "SELECT (SELECT count(*) FROM itinerary_items i JOIN trip_days d ON d.id=i.trip_day_id "
            "JOIN trips t ON t.id=d.trip_id JOIN places p ON p.id=i.place_id "
            "WHERE t.owner_id=:owner_id AND p.owner_id<>:owner_id) + "
            "(SELECT count(*) FROM reservations r JOIN trips t ON t.id=r.trip_id "
            "JOIN places p ON p.id=r.place_id WHERE r.owner_id=:owner_id "
            "AND p.owner_id<>:owner_id) + "
            "(SELECT count(*) FROM itinerary_items i JOIN trip_days d ON d.id=i.trip_day_id "
            "JOIN trips t ON t.id=d.trip_id JOIN reservations r ON r.id=i.reservation_id "
            "WHERE t.owner_id=:owner_id AND (r.owner_id<>:owner_id OR r.trip_id<>t.id)) + "
            "(SELECT count(*) FROM itinerary_items i JOIN trip_days d ON d.id=i.trip_day_id "
            "JOIN trips t ON t.id=d.trip_id JOIN places p ON p.id=i.place_id "
            "WHERE p.owner_id=:owner_id AND t.owner_id<>:owner_id)",
            source_owner_id,
        ),
    }
    payload = _plan_payload(source_owner_id, target_owner_id, counts, target_counts, conflicts)
    return OwnerMigrationPlan(
        source_owner_id=source_owner_id,
        target_owner_id=target_owner_id,
        counts=counts,
        target_counts=target_counts,
        conflicts=conflicts,
        plan_digest=hashlib.sha256(payload).hexdigest(),
    )


def _revision_fingerprint(session: Session) -> str:
    revisions: list[tuple[str, str, int]] = []
    for model in (Trip, Place):
        rows = session.execute(select(model.id, model.revision).order_by(model.id)).all()
        revisions.extend((model.__tablename__, str(row.id), int(row.revision)) for row in rows)
    return hashlib.sha256(json.dumps(revisions, separators=(",", ":")).encode()).hexdigest()


def _backup_digest(path: Path) -> str:
    if not path.is_file():
        raise OwnerMigrationRejected("A regular verified backup file is required.")
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as backup:
        while chunk := backup.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_BACKUP_BYTES:
                raise OwnerMigrationRejected("The backup file exceeds the 16 GiB verification cap.")
            digest.update(chunk)
    if size == 0:
        raise OwnerMigrationRejected("The backup file is empty.")
    return digest.hexdigest()


def apply_owner_migration(
    session: Session,
    *,
    source_owner_id: str,
    target_owner_id: str,
    run_id: UUID,
    expected_plan_digest: str,
    backup_file: Path,
    expected_backup_sha256: str,
    confirmation: str,
) -> OwnerMigrationPlan:
    required_confirmation = f"{source_owner_id} -> {target_owner_id}"
    if confirmation != required_confirmation:
        raise OwnerMigrationRejected("Explicit source-to-target confirmation is required.")
    backup_sha256 = _backup_digest(backup_file)
    if backup_sha256 != expected_backup_sha256.lower():
        raise OwnerMigrationRejected("The backup file SHA-256 does not match the verified digest.")

    with session.begin():
        plan = inspect_owner_migration(
            session,
            source_owner_id=source_owner_id,
            target_owner_id=target_owner_id,
            acquire_lock=True,
        )
        if plan.plan_digest != expected_plan_digest:
            raise OwnerMigrationRejected(
                "The data graph changed after dry run; inspect a new plan."
            )
        if not plan.can_apply:
            raise OwnerMigrationRejected("The dry run found graph or provider-identity conflicts.")
        if session.get(OwnerMigrationAudit, run_id) is not None:
            raise OwnerMigrationRejected("The migration run ID has already been recorded.")

        before_revisions = _revision_fingerprint(session)
        source_proposal_ids = list(
            session.scalars(
                select(ItineraryProposal.id).where(ItineraryProposal.owner_id == source_owner_id)
            )
        )
        for table_name in OWNER_SCOPED_TABLES:
            table = Base.metadata.tables[table_name]
            session.execute(
                update(table)
                .where(table.c.owner_id == source_owner_id)
                .values(owner_id=target_owner_id)
            )
        for proposal in session.scalars(
            select(ItineraryProposal).where(ItineraryProposal.id.in_(source_proposal_ids))
        ):
            snapshot = dict(proposal.base_snapshot)
            snapshot["owner_id"] = target_owner_id
            proposal.base_snapshot = snapshot

        session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        after_revisions = _revision_fingerprint(session)
        if before_revisions != after_revisions:
            raise OwnerMigrationRejected("Owner migration unexpectedly changed travel revisions.")
        session.add(
            OwnerMigrationAudit(
                id=run_id,
                source_owner_id=source_owner_id,
                target_owner_id=target_owner_id,
                actor_owner_id=target_owner_id,
                plan_digest=plan.plan_digest,
                backup_sha256=backup_sha256,
                row_counts=plan.counts,
                occurred_at=datetime.now(UTC),
            )
        )
    return plan
