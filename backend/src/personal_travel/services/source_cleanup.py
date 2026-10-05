"""Bounded, rerunnable private-source reconciliation."""

from __future__ import annotations

import argparse
import hashlib
import stat
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from personal_travel.config import get_settings
from personal_travel.db.session import SessionFactory
from personal_travel.models.import_source import BookingImport, SourceAttachment
from personal_travel.services.source_store import KEY, LocalSourceStore


def cleanup(
    session: Session,
    store: LocalSourceStore,
    *,
    limit: int = 100,
    owner_id: str | None = None,
) -> int:
    """Reconcile old pending/deleting/expired rows, then old unreferenced files."""
    if not 1 <= limit <= 1000:
        raise ValueError("Cleanup limit must be between 1 and 1000.")
    now = datetime.now(UTC)
    stale = now - timedelta(hours=1)
    eligible = or_(
        SourceAttachment.state == "deleting",
        SourceAttachment.trip_id.is_(None),
        SourceAttachment.expires_at <= now,
        and_(SourceAttachment.state == "pending", SourceAttachment.created_at <= stale),
    )
    statement = (
        select(SourceAttachment)
        .where(eligible)
        .order_by(SourceAttachment.created_at, SourceAttachment.id)
        .limit(limit)
    )
    if owner_id is not None:
        statement = statement.where(SourceAttachment.owner_id == owner_id)
    rows = session.scalars(statement).all()
    changed = 0
    for source in rows:
        if source.state == "pending" and source.trip_id is not None and source.expires_at > now:
            try:
                data = store.read(source.object_key)
            except FileNotFoundError:
                data = b""
            except ValueError:
                data = b""
            if len(data) == source.byte_size and hashlib.sha256(data).hexdigest() == source.sha256:
                source.state = "ready"
                store.delete(source.object_key, temp=True)
                session.commit()
                changed += 1
                continue
        _finish_deletion(session, store, source)
        changed += 1

    remaining = limit - changed
    if remaining > 0:
        ready_statement = (
            select(SourceAttachment)
            .where(SourceAttachment.state == "ready")
            .order_by(SourceAttachment.created_at, SourceAttachment.id)
            .limit(remaining)
        )
        if owner_id is not None:
            ready_statement = ready_statement.where(SourceAttachment.owner_id == owner_id)
        for source in session.scalars(ready_statement).all():
            if store.exists(source.object_key):
                store.delete(source.object_key, temp=True)
                continue
            _finish_deletion(session, store, source)
            changed += 1
            remaining -= 1
            if remaining == 0:
                break

    remaining = limit - changed
    if remaining > 0 and owner_id is None:
        # An owner-scoped cleanup cannot infer ownership of an orphan, so it
        # leaves orphan reconciliation to an explicit operator-wide pass.
        examined = 0
        for entry_name, info in store.entry_stats():
            if remaining == 0 or examined >= limit * 10:
                break
            examined += 1
            name = entry_name.removesuffix(".tmp")
            if not KEY.fullmatch(name) or entry_name not in {name, name + ".tmp"}:
                continue
            if not stat.S_ISREG(info.st_mode) or datetime.fromtimestamp(info.st_mtime, UTC) > stale:
                continue
            if session.scalar(
                select(SourceAttachment.id).where(SourceAttachment.object_key == name)
            ):
                continue
            store.delete(name, temp=entry_name.endswith(".tmp"))
            changed += 1
            remaining -= 1
    return changed


def _finish_deletion(session: Session, store: LocalSourceStore, source: SourceAttachment) -> None:
    source.state = "deleting"
    session.commit()
    store.delete(source.object_key)
    store.delete(source.object_key, temp=True)
    session.query(BookingImport).filter(BookingImport.source_id == source.id).delete()
    session.delete(source)
    session.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Reconcile private-source bytes and metadata")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--owner-id", help="Restrict metadata cleanup to one verified owner.")
    args = parser.parse_args()
    settings = get_settings()
    if not settings.private_source_dir:
        parser.error("PRIVATE_SOURCE_DIR is required")
    store = LocalSourceStore(settings.private_source_dir)
    try:
        with SessionFactory() as session:
            count = cleanup(session, store, limit=args.limit, owner_id=args.owner_id)
        print(f"Reconciled {count} private source entries.")
    finally:
        store.close()


if __name__ == "__main__":
    main()
