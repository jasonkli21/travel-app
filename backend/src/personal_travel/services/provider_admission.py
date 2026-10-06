"""Shared, fail-closed admission budgets for billable provider requests."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, or_, select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from personal_travel.models.auth import ProviderQuotaBucket

SessionFactoryLike = Callable[[], Session]
MINUTE_SECONDS = 60
DAY_SECONDS = 86_400
GLOBAL_SCOPE_HASH = hashlib.sha256(b"personal-travel:global-provider-budget:v1").hexdigest()


@dataclass(frozen=True, slots=True)
class QuotaRule:
    operation: str
    provider: str
    weight: int
    owner_per_minute: int
    owner_per_day: int


@dataclass(frozen=True, slots=True)
class AdmissionResult:
    operation: str
    provider: str
    charged_units: int


class QuotaExceeded(Exception):
    def __init__(self, *, retry_after_seconds: int, operation: str) -> None:
        super().__init__("The provider request budget is exhausted.")
        self.retry_after_seconds = retry_after_seconds
        self.operation = operation


class ProviderAdmissionUnavailable(Exception):
    """The shared quota store is unavailable; provider work must fail closed."""


# Per-owner caps bound one owner's traffic. Provider-wide caps remain shared
# across owners and app instances. Route estimates reserve the maximum six
# Geoapify HTTP calls for a 50-leg request before route projection is known.
RULES: dict[str, QuotaRule] = {
    "geoapify_search": QuotaRule("geoapify_search", "geoapify", 1, 8, 100),
    "geoapify_route": QuotaRule("geoapify_route", "geoapify", 6, 12, 60),
    "personal_ai_research": QuotaRule("personal_ai_research", "personal_ai", 1, 4, 40),
    "personal_ai_comparison": QuotaRule("personal_ai_comparison", "personal_ai", 1, 3, 20),
    "personal_ai_proposal": QuotaRule("personal_ai_proposal", "personal_ai", 1, 2, 10),
    "personal_ai_extraction": QuotaRule("personal_ai_extraction", "personal_ai", 1, 1, 5),
    "personal_ai_deletion": QuotaRule("personal_ai_deletion", "personal_ai", 1, 2, 20),
    "personal_ai_deletion_batch": QuotaRule(
        "personal_ai_deletion_batch", "personal_ai", 10, 10, 20
    ),
}


def admit_provider_request(
    session_factory: SessionFactoryLike,
    *,
    owner_id: str,
    operation: str,
    global_per_minute: int,
    global_per_day: int,
    now: datetime | None = None,
) -> AdmissionResult:
    rule = RULES.get(operation)
    if rule is None:
        raise ValueError("Unknown provider admission operation.")
    if not owner_id or min(global_per_minute, global_per_day) < rule.weight:
        raise ValueError("Provider admission limits must fit the operation weight.")

    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        raise ValueError("Quota time must be timezone-aware.")
    current = current.astimezone(UTC)
    minute_start = _window_start(current, MINUTE_SECONDS)
    day_start = _window_start(current, DAY_SECONDS)
    owner_hash = hashlib.sha256(b"personal-travel:owner:" + owner_id.encode("utf-8")).hexdigest()
    buckets = (
        (
            owner_hash,
            f"owner:{rule.provider}:{rule.operation}",
            minute_start,
            MINUTE_SECONDS,
            rule.owner_per_minute,
        ),
        (
            owner_hash,
            f"owner:{rule.provider}:{rule.operation}",
            day_start,
            DAY_SECONDS,
            rule.owner_per_day,
        ),
        (
            GLOBAL_SCOPE_HASH,
            f"provider:{rule.provider}",
            minute_start,
            MINUTE_SECONDS,
            global_per_minute,
        ),
        (GLOBAL_SCOPE_HASH, f"provider:{rule.provider}", day_start, DAY_SECONDS, global_per_day),
    )
    # Stable row order minimizes deadlocks when requests share multiple windows.
    ordered_buckets = sorted(buckets, key=lambda row: (row[0], row[1], row[3], row[2]))
    try:
        with session_factory() as session, session.begin():
            dialect = session.get_bind().dialect.name
            if dialect not in {"postgresql", "sqlite"}:
                raise ProviderAdmissionUnavailable("Unsupported quota storage dialect.")
            for scope_hash, bucket_key, window_start, seconds, maximum in ordered_buckets:
                values = {
                    "scope_hash": scope_hash,
                    "bucket_key": bucket_key,
                    "window_seconds": seconds,
                    "window_start": window_start,
                    "used": rule.weight,
                }
                statement: Any
                if dialect == "postgresql":
                    statement = postgres_insert(ProviderQuotaBucket).values(**values)
                else:
                    statement = sqlite_insert(ProviderQuotaBucket).values(**values)
                statement = statement.on_conflict_do_update(
                    index_elements=[
                        ProviderQuotaBucket.scope_hash,
                        ProviderQuotaBucket.bucket_key,
                        ProviderQuotaBucket.window_seconds,
                        ProviderQuotaBucket.window_start,
                    ],
                    set_={"used": ProviderQuotaBucket.used + rule.weight},
                    where=ProviderQuotaBucket.used + rule.weight <= maximum,
                ).returning(ProviderQuotaBucket.used)
                if session.execute(statement).scalar_one_or_none() is None:
                    retry_after = max(
                        1,
                        int((window_start + timedelta(seconds=seconds) - current).total_seconds()),
                    )
                    raise QuotaExceeded(
                        retry_after_seconds=retry_after,
                        operation=operation,
                    )
    except (QuotaExceeded, ProviderAdmissionUnavailable):
        raise
    except Exception as exc:
        # Database exception text may include connection strings or parameters.
        raise ProviderAdmissionUnavailable from exc
    return AdmissionResult(operation, rule.provider, rule.weight)


def purge_quota_buckets(session: Session, *, before: datetime, limit: int = 1000) -> int:
    """Delete expired quota rows in a bounded operational cleanup pass."""
    if before.tzinfo is None or not 1 <= limit <= 1000:
        raise ValueError("Quota cleanup requires an aware cutoff and bounded limit.")
    keys = (
        select(
            ProviderQuotaBucket.scope_hash,
            ProviderQuotaBucket.bucket_key,
            ProviderQuotaBucket.window_seconds,
            ProviderQuotaBucket.window_start,
        )
        .where(ProviderQuotaBucket.window_start < before.astimezone(UTC))
        .limit(limit)
    )
    rows = list(session.execute(keys))
    if not rows:
        return 0
    for offset in range(0, len(rows), 100):
        conditions = [
            (
                (ProviderQuotaBucket.scope_hash == scope_hash)
                & (ProviderQuotaBucket.bucket_key == bucket_key)
                & (ProviderQuotaBucket.window_seconds == seconds)
                & (ProviderQuotaBucket.window_start == window_start)
            )
            for scope_hash, bucket_key, seconds, window_start in rows[offset : offset + 100]
        ]
        session.execute(delete(ProviderQuotaBucket).where(or_(*conditions)))
    return len(rows)


def _window_start(current: datetime, seconds: int) -> datetime:
    timestamp = int(current.timestamp())
    return datetime.fromtimestamp(timestamp - timestamp % seconds, UTC)
