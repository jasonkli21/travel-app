"""Explicit, audited transfer of one local travel-data graph to a verified owner."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from personal_travel.auth.google_oidc import stable_google_owner_id
from personal_travel.db.base import Base
from personal_travel.domain.proposals import ProposalTripSnapshot
from personal_travel.models import (
    AuthIdentity,
    ItineraryProposal,
    OwnerMigrationAudit,
    Place,
    Trip,
)
from personal_travel.services.errors import DomainError
from personal_travel.services.proposal_preview import validate_proposal_snapshot

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


def _transfer_snapshot(
    proposal: ItineraryProposal, source_owner_id: str, target_owner_id: str
) -> dict[str, Any]:
    snapshot = proposal.base_snapshot
    if not isinstance(snapshot, dict) or snapshot.get("owner_id") != source_owner_id:
        raise OwnerMigrationRejected("A proposal snapshot has a different owner.")
    rewritten = dict(snapshot)
    rewritten["owner_id"] = target_owner_id
    for collection in ("places", "candidates", "reservations"):
        if collection not in rewritten and proposal.state not in {
            "ready",
            "generating",
            "outcome_unknown",
        }:
            continue  # Legacy terminal records cannot be applied or reconciled.
        rows = rewritten.get(collection)
        if not isinstance(rows, list):
            raise OwnerMigrationRejected("A proposal snapshot is malformed.")
        new_rows = []
        for row in rows:
            if not isinstance(row, dict) or row.get("owner_id") != source_owner_id:
                raise OwnerMigrationRejected("A proposal snapshot references a foreign owner.")
            new_rows.append({**row, "owner_id": target_owner_id})
        rewritten[collection] = new_rows
    if "days" in rewritten:
        days = rewritten["days"]
        if not isinstance(days, list):
            raise OwnerMigrationRejected("A proposal snapshot is malformed.")
        new_days = []
        for day in days:
            if not isinstance(day, dict) or not isinstance(day.get("items"), list):
                raise OwnerMigrationRejected("A proposal snapshot is malformed.")
            items = []
            for item in day["items"]:
                if not isinstance(item, dict) or item.get("owner_id") != source_owner_id:
                    raise OwnerMigrationRejected("A proposal item references a foreign owner.")
                items.append({**item, "owner_id": target_owner_id})
            new_days.append({**day, "items": items})
        rewritten["days"] = new_days
    elif proposal.state in {"ready", "generating", "outcome_unknown"}:
        raise OwnerMigrationRejected("An active proposal snapshot is incomplete.")
    if proposal.state in {"ready", "generating", "outcome_unknown"}:
        try:
            validate_proposal_snapshot(
                ProposalTripSnapshot.model_validate_json(json.dumps(rewritten))
            )
        except (ValidationError, ValueError, DomainError) as error:
            raise OwnerMigrationRejected("An active proposal snapshot is invalid.") from error
    return rewritten


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
    try:
        return stable_google_owner_id(issuer, subject)
    except ValueError:
        raise OwnerMigrationRejected("The target identity mapping is invalid.") from None


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
    graph_fingerprint: str,
) -> bytes:
    payload = {
        "source_owner_id": source_owner_id,
        "target_owner_id": target_owner_id,
        "counts": counts,
        "target_counts": target_counts,
        "conflicts": conflicts,
        "graph_fingerprint": graph_fingerprint,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def _graph_fingerprint(session: Session, source_owner_id: str, target_owner_id: str) -> str:
    """Bind an explicit migration plan to the exact source and destination graph.

    The fingerprint is never rendered as row data: only its SHA-256 digest is
    returned in the plan. Including complete row values detects edits between
    dry run and apply even when the graph's row counts and revisions stay the
    same (for example, a reservation's text fields or proposal snapshot).
    """
    digest = hashlib.sha256()
    owner_ids = (source_owner_id, target_owner_id)
    for table_name in OWNER_SCOPED_TABLES:
        table = Base.metadata.tables[table_name]
        rows = session.execute(
            select(table)
            .where(table.c.owner_id.in_(owner_ids))
            .order_by(*table.primary_key.columns)
        ).mappings()
        for row in rows:
            encoded = json.dumps(
                [table_name, dict(row)],
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode()
            digest.update(len(encoded).to_bytes(8, "big"))
            digest.update(encoded)

    trips = Base.metadata.tables["trips"]
    trip_days = Base.metadata.tables["trip_days"]
    itinerary_items = Base.metadata.tables["itinerary_items"]
    related_queries = (
        (
            "trip_days",
            select(trip_days)
            .join(trips, trips.c.id == trip_days.c.trip_id)
            .where(trips.c.owner_id.in_(owner_ids))
            .order_by(*trip_days.primary_key.columns),
        ),
        (
            "itinerary_items",
            select(itinerary_items)
            .join(trip_days, trip_days.c.id == itinerary_items.c.trip_day_id)
            .join(trips, trips.c.id == trip_days.c.trip_id)
            .where(trips.c.owner_id.in_(owner_ids))
            .order_by(*itinerary_items.primary_key.columns),
        ),
    )
    for table_name, statement in related_queries:
        for row in session.execute(statement).mappings():
            encoded = json.dumps(
                [table_name, dict(row)],
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode()
            digest.update(len(encoded).to_bytes(8, "big"))
            digest.update(encoded)

    identity = _verified_target(session, target_owner_id)
    identity_state = [
        identity.owner_id,
        identity.issuer,
        identity.subject,
        identity.status,
    ]
    encoded_identity = json.dumps(identity_state, separators=(",", ":")).encode()
    digest.update(len(encoded_identity).to_bytes(8, "big"))
    digest.update(encoded_identity)
    return digest.hexdigest()


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
    mismatched_snapshots = 0
    for proposal in source_proposals:
        try:
            _transfer_snapshot(proposal, source_owner_id, target_owner_id)
        except OwnerMigrationRejected:
            mismatched_snapshots += 1
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
        "target_saved_place_collision": _related_count(
            session,
            "SELECT count(*) FROM saved_places source JOIN saved_places target "
            "ON source.trip_id=target.trip_id AND source.place_id=target.place_id "
            "WHERE source.owner_id=:owner_id AND target.owner_id=:target_owner_id",
            source_owner_id,
            target_owner_id=target_owner_id,
        ),
        "target_proposal_idempotency_collision": _related_count(
            session,
            "SELECT count(*) FROM itinerary_proposals source "
            "JOIN itinerary_proposals target ON source.trip_id=target.trip_id "
            "AND source.idempotency_key=target.idempotency_key "
            "WHERE source.owner_id=:owner_id AND target.owner_id=:target_owner_id",
            source_owner_id,
            target_owner_id=target_owner_id,
        ),
        "owner_trip_mismatch": _related_count(
            session,
            "SELECT (SELECT count(*) FROM reservations r JOIN trips t ON t.id=r.trip_id "
            "WHERE r.owner_id IN (:owner_id,:target_owner_id) AND t.owner_id<>r.owner_id) + "
            "(SELECT count(*) FROM saved_places s JOIN trips t ON t.id=s.trip_id "
            "JOIN places p ON p.id=s.place_id "
            "WHERE s.owner_id IN (:owner_id,:target_owner_id) "
            "AND (t.owner_id<>s.owner_id OR p.owner_id<>s.owner_id)) + "
            "(SELECT count(*) FROM itinerary_proposals p JOIN trips t ON t.id=p.trip_id "
            "WHERE p.owner_id IN (:owner_id,:target_owner_id) AND t.owner_id<>p.owner_id)",
            source_owner_id,
            target_owner_id=target_owner_id,
        ),
        "cross_owner_trip_place_reference": _related_count(
            session,
            "SELECT (SELECT count(*) FROM itinerary_items i JOIN trip_days d ON d.id=i.trip_day_id "
            "JOIN trips t ON t.id=d.trip_id JOIN places p ON p.id=i.place_id "
            "WHERE t.owner_id IN (:owner_id,:target_owner_id) AND p.owner_id<>t.owner_id) + "
            "(SELECT count(*) FROM reservations r JOIN trips t ON t.id=r.trip_id "
            "JOIN places p ON p.id=r.place_id "
            "WHERE r.owner_id IN (:owner_id,:target_owner_id) "
            "AND p.owner_id<>r.owner_id) + "
            "(SELECT count(*) FROM itinerary_items i JOIN trip_days d ON d.id=i.trip_day_id "
            "JOIN trips t ON t.id=d.trip_id JOIN reservations r ON r.id=i.reservation_id "
            "WHERE t.owner_id IN (:owner_id,:target_owner_id) "
            "AND (r.owner_id<>t.owner_id OR r.trip_id<>t.id)) + "
            "(SELECT count(*) FROM itinerary_items i JOIN trip_days d ON d.id=i.trip_day_id "
            "JOIN trips t ON t.id=d.trip_id JOIN places p ON p.id=i.place_id "
            "WHERE p.owner_id IN (:owner_id,:target_owner_id) "
            "AND t.owner_id<>p.owner_id)",
            source_owner_id,
            target_owner_id=target_owner_id,
        ),
    }
    graph_fingerprint = _graph_fingerprint(session, source_owner_id, target_owner_id)
    payload = _plan_payload(
        source_owner_id,
        target_owner_id,
        counts,
        target_counts,
        conflicts,
        graph_fingerprint,
    )
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
        transferred_proposals = []
        for proposal in session.scalars(
            select(ItineraryProposal).where(ItineraryProposal.id.in_(source_proposal_ids))
        ):
            transferred_proposals.append(
                (proposal.id, _transfer_snapshot(proposal, source_owner_id, target_owner_id))
            )
        for table_name in OWNER_SCOPED_TABLES:
            table = Base.metadata.tables[table_name]
            session.execute(
                update(table)
                .where(table.c.owner_id == source_owner_id)
                .values(owner_id=target_owner_id)
            )
        for proposal_id, snapshot in transferred_proposals:
            transferred = session.get(ItineraryProposal, proposal_id)
            assert transferred is not None
            transferred.base_snapshot = snapshot
            if transferred.state in {"generating", "outcome_unknown"}:
                transferred.state = "failed"
                transferred.failure_code = "owner_migrated_remote_unrecoverable"

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
