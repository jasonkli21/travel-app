"""Fair, bounded, rerunnable private-source reconciliation."""

from __future__ import annotations

import argparse
import bisect
import hashlib
import stat
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.orm import Session

from personal_travel.config import get_settings
from personal_travel.db.session import SessionFactory
from personal_travel.models.import_source import BookingImport, SourceAttachment
from personal_travel.models.trip import Trip
from personal_travel.services.private_deletion import enqueue_in_session
from personal_travel.services.source_store import KEY, LocalSourceStore

SessionFactoryLike = Callable[[], Session]


@dataclass(frozen=True, slots=True)
class CleanupResult:
    reconciled: int
    inspected: int
    next_source_id: str | None
    next_entry: str | None


def cleanup(
    session_factory: SessionFactoryLike,
    store: LocalSourceStore,
    *,
    limit: int = 100,
    owner_id: str | None = None,
    after_source_id: str | None = None,
    after_entry: str | None = None,
    time_limit_seconds: float = 5.0,
) -> CleanupResult:
    """Inspect at most ``limit`` records and continue fair scans from cursors.

    ``after_source_id`` paginates all source metadata by stable UUID; healthy
    rows still advance the cursor, so they cannot starve later damaged rows.
    ``after_entry`` paginates the sorted private-directory snapshot. The CLI
    prints both values; an operator feeds them into the next bounded pass until
    each cursor is null.
    SQL state changes are short conditional transactions. File reads/deletes
    happen after locks have been released.
    """
    if not 1 <= limit <= 1000:
        raise ValueError("Cleanup limit must be between 1 and 1000.")
    if not 0.1 <= time_limit_seconds <= 60:
        raise ValueError("Cleanup time limit must be between 0.1 and 60 seconds.")
    deadline = time.monotonic() + time_limit_seconds
    now = datetime.now(UTC)
    stale = now - timedelta(hours=1)
    try:
        parsed_cursor = UUID(after_source_id) if after_source_id else None
    except ValueError as exc:
        raise ValueError("after_source_id must be a UUID cursor.") from exc
    source_statement = select(
        SourceAttachment.id,
        SourceAttachment.object_key,
        SourceAttachment.sha256,
        SourceAttachment.byte_size,
        SourceAttachment.state,
        SourceAttachment.trip_id,
        SourceAttachment.expires_at,
        SourceAttachment.created_at,
    )
    if parsed_cursor is not None:
        source_statement = source_statement.where(SourceAttachment.id > parsed_cursor)
    if owner_id is not None:
        source_statement = source_statement.where(SourceAttachment.owner_id == owner_id)
    source_statement = source_statement.order_by(SourceAttachment.id).limit(limit + 1)
    with session_factory() as session:
        source_rows = list(session.execute(source_statement))

    reconciled = 0
    inspected = 0
    has_more = len(source_rows) > limit
    source_cursor = after_source_id
    for (
        source_id,
        object_key,
        expected_hash,
        expected_size,
        state,
        trip_id,
        expires_at,
        created_at,
    ) in source_rows[:limit]:
        if time.monotonic() >= deadline:
            has_more = True
            break
        inspected += 1
        source_cursor = str(source_id)
        stale_pending = state == "pending" and created_at <= stale
        if stale_pending and trip_id is not None and expires_at > now:
            if _recover_pending(session_factory, store, source_id, owner_id, stale, now):
                reconciled += 1
                continue
        stale_source = state == "deleting" or trip_id is None or expires_at <= now or stale_pending
        if stale_source:
            claimed_key = _claim_for_deletion(session_factory, source_id, owner_id, stale, now)
            if claimed_key is not None and _finish_deletion(
                session_factory, store, source_id, claimed_key
            ):
                reconciled += 1
            continue
        if state == "ready" and not _matches(store, object_key, expected_hash, expected_size):
            if _claim_ready_for_deletion(session_factory, source_id, object_key):
                if _finish_deletion(session_factory, store, source_id, object_key):
                    reconciled += 1
    if not has_more and len(source_rows) <= limit and inspected == len(source_rows):
        source_cursor = None

    entry_cursor = after_entry
    remaining = limit - inspected
    if remaining > 0 and owner_id is None and time.monotonic() < deadline:
        names = store.entry_names()
        start = bisect.bisect_right(names, after_entry) if after_entry else 0
        page = names[start : start + remaining + 1]
        has_more = len(page) > remaining
        for entry_name in page[:remaining]:
            if time.monotonic() >= deadline:
                has_more = True
                break
            inspected += 1
            entry_cursor = entry_name
            object_key = entry_name.removesuffix(".tmp")
            info = store.entry_stat(entry_name)
            if info is None:
                continue
            if (
                not KEY.fullmatch(object_key)
                or entry_name not in {object_key, object_key + ".tmp"}
                or not stat.S_ISREG(info.st_mode)
                or datetime.fromtimestamp(info.st_mtime, UTC) > stale
            ):
                continue
            with session_factory() as session:
                referenced = session.scalar(
                    select(SourceAttachment.id).where(SourceAttachment.object_key == object_key)
                )
            if referenced is None:
                store.delete(object_key, temp=entry_name.endswith(".tmp"))
                reconciled += 1
        if not has_more and len(page) <= remaining:
            entry_cursor = None

    return CleanupResult(reconciled, inspected, source_cursor, entry_cursor)


def _source_snapshot(
    session_factory: SessionFactoryLike, source_id: UUID, owner_id: str | None
) -> tuple[str, str, int, str, datetime, datetime] | None:
    statement = select(
        SourceAttachment.object_key,
        SourceAttachment.sha256,
        SourceAttachment.byte_size,
        SourceAttachment.state,
        SourceAttachment.expires_at,
        SourceAttachment.created_at,
    ).where(SourceAttachment.id == source_id)
    if owner_id is not None:
        statement = statement.where(SourceAttachment.owner_id == owner_id)
    with session_factory() as session:
        return session.execute(statement).one_or_none()


def _recover_pending(
    session_factory: SessionFactoryLike,
    store: LocalSourceStore,
    source_id: UUID,
    owner_id: str | None,
    stale: datetime,
    now: datetime,
) -> bool:
    snapshot = _source_snapshot(session_factory, source_id, owner_id)
    if snapshot is None:
        return False
    object_key, expected_hash, expected_size, state, expires_at, created_at = snapshot
    if state != "pending" or expires_at <= now or created_at > stale:
        return False
    if not _matches(store, object_key, expected_hash, expected_size):
        return False
    statement = (
        update(SourceAttachment)
        .where(
            SourceAttachment.id == source_id,
            SourceAttachment.state == "pending",
            SourceAttachment.expires_at > now,
            SourceAttachment.created_at <= stale,
        )
        .values(state="ready", updated_at=now)
        .returning(SourceAttachment.id)
    )
    if owner_id is not None:
        statement = statement.where(SourceAttachment.owner_id == owner_id)
    with session_factory() as session, session.begin():
        recovered = session.execute(statement).scalar_one_or_none() is not None
    if recovered:
        store.delete(object_key, temp=True)
    return recovered


def _claim_for_deletion(
    session_factory: SessionFactoryLike,
    source_id: UUID,
    owner_id: str | None,
    stale: datetime,
    now: datetime,
) -> str | None:
    eligible = or_(
        SourceAttachment.state == "deleting",
        SourceAttachment.trip_id.is_(None),
        SourceAttachment.expires_at <= now,
        and_(SourceAttachment.state == "pending", SourceAttachment.created_at <= stale),
    )
    statement = (
        update(SourceAttachment)
        .where(SourceAttachment.id == source_id, eligible)
        .values(state="deleting", updated_at=now)
        .returning(SourceAttachment.object_key)
    )
    if owner_id is not None:
        statement = statement.where(SourceAttachment.owner_id == owner_id)
    with session_factory() as session, session.begin():
        return session.execute(statement).scalar_one_or_none()


def _claim_ready_for_deletion(
    session_factory: SessionFactoryLike, source_id: UUID, object_key: str
) -> bool:
    with session_factory() as session, session.begin():
        trip_id = session.scalar(
            select(SourceAttachment.trip_id).where(SourceAttachment.id == source_id)
        )
        if trip_id is not None:
            session.scalar(select(Trip).where(Trip.id == trip_id).with_for_update())
        imports = list(
            session.scalars(
                select(BookingImport)
                .where(BookingImport.source_id == source_id)
                .order_by(BookingImport.id)
                .with_for_update()
            )
        )
        result = session.execute(
            update(SourceAttachment)
            .where(
                SourceAttachment.id == source_id,
                SourceAttachment.object_key == object_key,
                SourceAttachment.state == "ready",
            )
            .values(state="deleting", updated_at=datetime.now(UTC))
            .returning(SourceAttachment.id)
        )
        if result.scalar_one_or_none() is None:
            return False
        for item in imports:
            item.source_id = None
            item.candidate_snapshot = None
            if item.extraction_key is not None and item.extraction_post_attempted:
                enqueue_in_session(
                    session,
                    owner_id=item.owner_id,
                    extraction_key=item.extraction_key,
                    source_sha256=item.extraction_text_sha256 or item.source_sha256,
                )
                item.upstream_delete_pending = True
            item.extraction_claim_token = None
            item.extraction_claimed_at = None
            if item.state not in {"applied", "rejected", "failed", "expired"}:
                item.state = "expired"
                item.candidate_snapshot = {"failure_code": "source_deleted"}
                item.review_revision += 1
            item.updated_at = datetime.now(UTC)
        return True


def _finish_deletion(
    session_factory: SessionFactoryLike,
    store: LocalSourceStore,
    source_id: UUID,
    object_key: str,
) -> bool:
    # Bytes are removed only after a committed deleting transition. Competing
    # deleters may repeat unlink; the final conditional row delete is idempotent.
    store.delete(object_key)
    store.delete(object_key, temp=True)
    with session_factory() as session, session.begin():
        source = session.scalar(
            select(SourceAttachment).where(
                SourceAttachment.id == source_id,
                SourceAttachment.object_key == object_key,
                SourceAttachment.state == "deleting",
            )
        )
        if source is not None and source.trip_id is not None:
            session.scalar(select(Trip).where(Trip.id == source.trip_id).with_for_update())
        item = session.scalar(
            select(BookingImport).where(BookingImport.source_id == source_id).with_for_update()
        )
        if item is not None:
            item.source_id = None
            item.candidate_snapshot = None
            if item.extraction_key is not None and item.extraction_post_attempted:
                enqueue_in_session(
                    session,
                    owner_id=item.owner_id,
                    extraction_key=item.extraction_key,
                    source_sha256=item.extraction_text_sha256 or item.source_sha256,
                )
                item.upstream_delete_pending = True
            item.extraction_claim_token = None
            item.extraction_claimed_at = None
            if item.state not in {"applied", "rejected", "failed", "expired"}:
                item.state = "expired"
                item.candidate_snapshot = {"failure_code": "source_deleted"}
                item.review_revision += 1
            item.updated_at = datetime.now(UTC)
        result = session.execute(
            delete(SourceAttachment)
            .where(
                SourceAttachment.id == source_id,
                SourceAttachment.object_key == object_key,
                SourceAttachment.state == "deleting",
            )
            .returning(SourceAttachment.id)
        )
        return result.scalar_one_or_none() is not None


def _matches(
    store: LocalSourceStore, object_key: str, expected_hash: str, expected_size: int
) -> bool:
    try:
        data = store.read(object_key)
    except (OSError, ValueError):
        return False
    return len(data) == expected_size and hashlib.sha256(data).hexdigest() == expected_hash


def main() -> None:
    parser = argparse.ArgumentParser(description="Reconcile private-source bytes and metadata")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--owner-id", help="Restrict metadata cleanup to one verified owner.")
    parser.add_argument("--after-source-id", help="Continue the ready-source UUID scan cursor.")
    parser.add_argument("--after-entry", help="Continue the sorted private-file scan cursor.")
    parser.add_argument("--seconds", type=float, default=5.0)
    args = parser.parse_args()
    settings = get_settings()
    if not settings.private_source_dir:
        parser.error("PRIVATE_SOURCE_DIR is required")
    store = LocalSourceStore(settings.private_source_dir)
    try:
        result = cleanup(
            SessionFactory,
            store,
            limit=args.limit,
            owner_id=args.owner_id,
            after_source_id=args.after_source_id,
            after_entry=args.after_entry,
            time_limit_seconds=args.seconds,
        )
        print(
            "Reconciled "
            f"{result.reconciled} entries after inspecting {result.inspected}; "
            f"next source cursor={result.next_source_id or 'complete'}, "
            f"next file cursor={result.next_entry or 'complete'}."
        )
    finally:
        store.close()


if __name__ == "__main__":
    main()
