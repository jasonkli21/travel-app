"""Bounded cleanup for expired auth artifacts and provider quota counters."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import delete, select
from sqlalchemy.engine import CursorResult

from personal_travel.db.session import SessionFactory
from personal_travel.models.auth import AuthSession, OAuthLoginAttempt, ProviderQuotaBucket
from personal_travel.services.provider_admission import purge_quota_buckets


def cleanup(*, limit: int, execute: bool) -> dict[str, int | str]:
    if not 1 <= limit <= 1000:
        raise ValueError("Cleanup limit must be between 1 and 1000.")
    now = datetime.now(UTC)
    expired_quota_before = now - timedelta(days=1)
    result: dict[str, int | str] = {"mode": "execute" if execute else "dry_run"}
    with SessionFactory() as session, session.begin():
        for name, model, column in (
            ("expired_sessions", AuthSession, AuthSession.expires_at),
            ("expired_oauth_attempts", OAuthLoginAttempt, OAuthLoginAttempt.expires_at),
        ):
            key = model.__mapper__.primary_key[0]
            keys = list(
                session.scalars(select(key).where(column < now).order_by(column).limit(limit))
            )
            if execute and keys:
                deleted = cast(
                    CursorResult[Any], session.execute(delete(model).where(key.in_(keys)))
                )
                result[name] = int(deleted.rowcount or 0)
            else:
                result[name] = len(keys)

        quota_keys = list(
            session.execute(
                select(
                    ProviderQuotaBucket.scope_hash,
                    ProviderQuotaBucket.bucket_key,
                    ProviderQuotaBucket.window_seconds,
                    ProviderQuotaBucket.window_start,
                )
                .where(ProviderQuotaBucket.window_start < expired_quota_before)
                .order_by(ProviderQuotaBucket.window_start)
                .limit(limit)
            )
        )
        result["expired_provider_buckets"] = (
            purge_quota_buckets(session, before=expired_quota_before, limit=limit)
            if execute
            else len(quota_keys)
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=500, help="maximum rows per artifact class")
    parser.add_argument(
        "--execute", action="store_true", help="delete the bounded expired rows; default is dry-run"
    )
    args = parser.parse_args()
    try:
        result = cleanup(limit=args.limit, execute=args.execute)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
