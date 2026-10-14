"""Bounded owner erasure and current-marker replay into an isolated restore.

The operator commits the freeze before requesting cleanup. No external deletion
is inferred. Keep this minimal ledger independently of historical backups.
"""

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import Select, Table, delete, exists, func, literal_column, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from personal_travel.db.base import Base
from personal_travel.models.auth import AuthIdentity
from personal_travel.models.import_source import (
    BookingDeletionIntent,
    BookingImport,
    SourceAttachment,
)
from personal_travel.models.owner_erasure import OwnerErasureLedger
from personal_travel.services.private_deletion import enqueue_in_session
from personal_travel.services.source_store import LocalSourceStore

_DATA_TABLES = frozenset(
    {
        "trips",
        "trip_days",
        "itinerary_items",
        "places",
        "reservations",
        "saved_places",
        "source_attachments",
        "booking_imports",
        "itinerary_proposals",
        "personal_ai_sidecar_anchors",
        "delegated_source_commands",
    }
)
_INTERNAL_TABLES = {
    "auth_identities",
    "auth_sessions",
    "booking_deletion_intents",
    "owner_erasure_ledger",
}


class ErasureLedgerSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["travel-erasure-ledger-v1"] = "travel-erasure-ledger-v1"
    observed_at: AwareDatetime
    owners: tuple[str, ...] = Field(max_length=10000)

    @field_validator("owners")
    @classmethod
    def valid_owners(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(values)) != len(values) or any(
            not value or len(value) > 128 for value in values
        ):
            raise ValueError("owner_erasure_scope_invalid")
        return values


class ErasureProgress(BaseModel):
    state: Literal["completed", "pending", "blob_cleanup_pending"]
    remaining: int
    upstream_pending: int


def _tables() -> list[Table]:
    actual = {
        table.name
        for table in Base.metadata.tables.values()
        if "owner_id" in table.c and table.name not in _INTERNAL_TABLES
    }
    if actual | {"trip_days", "itinerary_items"} != _DATA_TABLES:
        raise RuntimeError("owner erasure inventory requires reconciliation")
    return [table for table in reversed(Base.metadata.sorted_tables) if table.name in _DATA_TABLES]


def _scope(table: Table, owner_id: str) -> ColumnElement[bool]:
    trips = Base.metadata.tables["trips"]
    days = Base.metadata.tables["trip_days"]
    if table.name == "trip_days":
        return table.c.trip_id.in_(select(trips.c.id).where(trips.c.owner_id == owner_id))
    if table.name == "itinerary_items":
        return table.c.trip_day_id.in_(
            select(days.c.id)
            .join(trips, days.c.trip_id == trips.c.id)
            .where(trips.c.owner_id == owner_id)
        )
    return table.c.owner_id == owner_id


def ledger_snapshot(session: Session) -> ErasureLedgerSnapshot:
    owners = session.scalars(
        select(OwnerErasureLedger.owner_id).order_by(OwnerErasureLedger.owner_id)
    ).all()
    return ErasureLedgerSnapshot(observed_at=datetime.now(UTC), owners=tuple(owners))


def freeze_owner(session: Session, owner_id: str, *, restored: bool = False) -> OwnerErasureLedger:
    if not owner_id or len(owner_id) > 128:
        raise ValueError("owner_erasure_scope_invalid")
    identity = session.scalar(
        select(AuthIdentity).where(AuthIdentity.owner_id == owner_id).with_for_update()
    )
    ledger = session.get(OwnerErasureLedger, owner_id)
    if ledger is None and identity is None and not restored:
        raise ValueError("owner_erasure_identity_required")
    # Queue attempted external extraction before destroying its canonical pointer.
    for item in session.scalars(
        select(BookingImport).where(BookingImport.owner_id == owner_id)
    ).all():
        if item.extraction_key is not None and item.extraction_post_attempted:
            enqueue_in_session(
                session,
                owner_id=owner_id,
                extraction_key=item.extraction_key,
                source_sha256=item.extraction_text_sha256 or item.source_sha256,
            )
    if identity is not None:
        identity.status = "disabled"
    if ledger is None:
        ledger = OwnerErasureLedger(owner_id=owner_id)
        session.add(ledger)
    if restored:
        ledger.completed_at = None
    session.flush()
    return ledger


def _progress(session: Session, owner_id: str, *, blob_pending: bool = False) -> ErasureProgress:
    remaining = sum(
        session.scalar(select(func.count()).select_from(table).where(_scope(table, owner_id))) or 0
        for table in _tables()
    )
    pending = (
        session.scalar(
            select(func.count())
            .select_from(BookingDeletionIntent)
            .where(BookingDeletionIntent.owner_id == owner_id)
        )
        or 0
    )
    return ErasureProgress(
        state="blob_cleanup_pending"
        if blob_pending
        else "pending"
        if remaining or pending
        else "completed",
        remaining=remaining,
        upstream_pending=pending,
    )


def erase_owner_batch(
    session: Session, owner_id: str, *, store: LocalSourceStore | None, limit: int = 128
) -> ErasureProgress:
    """Caller owns the transaction; resume the same frozen owner after interruption."""
    if not 1 <= limit <= 1000:
        raise ValueError("owner_erasure_batch_invalid")
    ledger = session.scalar(
        select(OwnerErasureLedger).where(OwnerErasureLedger.owner_id == owner_id).with_for_update()
    )
    if ledger is None:
        raise ValueError("owner_erasure_not_frozen")
    budget = limit
    for table in _tables():
        if budget <= 0:
            break
        query: Select[Any] = (
            select(literal_column("ctid::text")).select_from(table).where(_scope(table, owner_id))
        )
        # Do not cascade across an inconsistent owner link, or bypass a pending
        # leaf's byte cleanup. Such rows remain pending for explicit repair.
        for child in Base.metadata.tables.values():
            if child.name not in _DATA_TABLES:
                continue
            for column in child.c:
                for foreign_key in column.foreign_keys:
                    if foreign_key.column.table is table:
                        query = query.where(
                            ~exists(
                                select(1).select_from(child).where(column == foreign_key.column)
                            )
                        )
        ctids = list(session.scalars(query.order_by(*table.primary_key.columns).limit(budget)))
        if table.name == "source_attachments" and ctids:
            if store is None:
                return _progress(session, owner_id, blob_pending=True)
            items = session.scalars(
                select(SourceAttachment).where(literal_column("ctid::text").in_(ctids))
            ).all()
            for item in items:
                lock = store.try_promotion_lock(item.object_key)
                if lock is None:
                    return _progress(session, owner_id, blob_pending=True)
                try:
                    store.delete(item.object_key)
                    store.delete(item.object_key, temp=True)
                finally:
                    store.release_promotion_lock(lock)
        if ctids:
            session.execute(table.delete().where(literal_column("ctid::text").in_(ctids)))
            budget -= len(ctids)
    result = _progress(session, owner_id)
    if result.state == "completed":
        session.execute(delete(AuthIdentity).where(AuthIdentity.owner_id == owner_id))
        ledger.completed_at = datetime.now(UTC)
        session.flush()
    return result


def replay_erasure_ledger(
    session: Session, snapshot: ErasureLedgerSnapshot, *, store: LocalSourceStore
) -> dict[str, int]:
    """Never trust a historical completion flag to certify restored canonical rows."""
    completed = pending = 0
    for owner_id in snapshot.owners:
        freeze_owner(session, owner_id, restored=True)
        result = erase_owner_batch(session, owner_id, store=store, limit=1000)
        if result.state == "completed":
            completed += 1
        else:
            pending += 1
    return {"completed": completed, "pending": pending}
