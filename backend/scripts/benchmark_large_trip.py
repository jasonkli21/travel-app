"""Measure representative API reads and JSON export at the supported trip limit.

This is a manual acceptance tool, not a latency-gated CI test. It creates one
random schema in a disposable PostgreSQL database and drops only that schema.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import tracemalloc
from collections.abc import Callable, Iterator
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from personal_travel.api.dependencies import session_dependency
from personal_travel.config import get_settings
from personal_travel.main import app
from personal_travel.models import ItineraryItem, Place, Trip, TripDay
from personal_travel.services.trips import MAX_TRIP_DAYS

MAX_BENCHMARK_ITEMS = 5_000
MAX_OWNER_PLACES = 5_000
MAX_ITERATIONS = 20


def _create_schema(database_url: str) -> tuple[Engine, Engine, str]:
    admin = create_engine(database_url, pool_size=1, max_overflow=0)
    schema = f"travel_benchmark_{uuid4().hex[:16]}"
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(
        database_url,
        connect_args={"options": f"-csearch_path={schema}"},
        pool_size=2,
        max_overflow=0,
        pool_timeout=1.0,
        pool_pre_ping=True,
    )
    try:
        backend = Path(__file__).resolve().parents[1]
        config = Config(str(backend / "alembic.ini"))
        config.set_main_option("script_location", str(backend / "migrations"))
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            command.check(config)
    except BaseException:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()
        raise
    return admin, engine, schema


def _seed(
    engine: Engine, *, days_count: int, items_per_day: int, owner_places: int
) -> tuple[str, int]:
    settings = get_settings()
    start = date(2027, 1, 1)
    end = start + timedelta(days=days_count - 1)
    trip = Trip(
        owner_id=settings.owner_id,
        title=f"Synthetic {days_count}-day performance trip",
        start_date=start,
        end_date=end,
        timezone="UTC",
    )
    with Session(engine, autoflush=False, expire_on_commit=False) as session, session.begin():
        session.add(trip)
        session.flush()
        places = [
            Place(owner_id=settings.owner_id, name=f"Synthetic place {index + 1:04d}")
            for index in range(owner_places)
        ]
        session.add_all(places)
        session.flush()
        trip_days = [
            TripDay(
                trip_id=trip.id,
                day_index=day_index,
                date=start + timedelta(days=day_index - 1),
                title=f"Synthetic day {day_index:03d}",
            )
            for day_index in range(1, days_count + 1)
        ]
        session.add_all(trip_days)
        session.flush()
        items = [
            ItineraryItem(
                trip_day_id=trip_days[day_index - 1].id,
                place_id=places[(day_index * items_per_day + item_index) % owner_places].id,
                item_type="activity",
                title=f"Synthetic activity {day_index:03d}-{item_index + 1:02d}",
                notes="Deterministic performance fixture; contains no personal data.",
                sort_order=item_index,
                status="planned",
            )
            for day_index in range(1, days_count + 1)
            for item_index in range(items_per_day)
        ]
        session.add_all(items)
        session.flush()
        trip_id = str(trip.id)
    return trip_id, days_count * items_per_day


def _measure(
    client: TestClient,
    request: Callable[[], Any],
    statement_count: list[int],
    *,
    iterations: int,
) -> dict[str, Any]:
    elapsed_ms: list[float] = []
    queries: list[int] = []
    peak_bytes: list[int] = []
    response_bytes: list[int] = []
    for _ in range(iterations):
        statement_count[0] = 0
        tracemalloc.start()
        baseline, _ = tracemalloc.get_traced_memory()
        tracemalloc.reset_peak()
        started = time.perf_counter()
        response = request()
        elapsed_ms.append((time.perf_counter() - started) * 1000)
        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        if response.status_code != 200:
            raise RuntimeError(f"benchmark request returned HTTP {response.status_code}")
        queries.append(statement_count[0])
        peak_bytes.append(max(0, peak - baseline))
        response_bytes.append(len(response.content))
    sorted_elapsed = sorted(elapsed_ms)
    p95_index = max(0, min(len(sorted_elapsed) - 1, int(len(sorted_elapsed) * 0.95)))
    return {
        "iterations": iterations,
        "elapsed_ms": {
            "median": round(statistics.median(elapsed_ms), 2),
            "p95_nearest_rank": round(sorted_elapsed[p95_index], 2),
            "max": round(max(elapsed_ms), 2),
        },
        "sql_statements": {"min": min(queries), "max": max(queries)},
        "response_bytes": {"min": min(response_bytes), "max": max(response_bytes)},
        "python_tracemalloc_peak_bytes": max(peak_bytes),
    }


def _run(args: argparse.Namespace, database_url: str) -> dict[str, Any]:
    if get_settings().travel_auth_mode != "local":
        raise RuntimeError("Run the benchmark with local authentication mode enabled.")
    if args.days > MAX_TRIP_DAYS:
        raise ValueError(f"--days cannot exceed the supported limit of {MAX_TRIP_DAYS}.")
    if args.days * args.items_per_day > MAX_BENCHMARK_ITEMS:
        raise ValueError(f"The benchmark is bounded to {MAX_BENCHMARK_ITEMS} itinerary items.")
    if args.owner_places > MAX_OWNER_PLACES:
        raise ValueError(f"--owner-places cannot exceed {MAX_OWNER_PLACES}.")
    if args.iterations > MAX_ITERATIONS:
        raise ValueError(f"--iterations cannot exceed {MAX_ITERATIONS}.")

    admin, engine, schema = _create_schema(database_url)
    old_overrides = app.dependency_overrides.copy()
    old_factory = getattr(app.state, "auth_session_factory", None)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    statements = [0]

    def count_statement(
        _connection: Any,
        _cursor: Any,
        _statement: str,
        _parameters: Any,
        _context: Any,
        _many: bool,
    ) -> None:
        statements[0] += 1

    def override_session() -> Iterator[Session]:
        with factory() as session:
            yield session

    event.listen(engine, "before_cursor_execute", count_statement)
    app.dependency_overrides[session_dependency] = override_session
    app.state.auth_session_factory = factory
    try:
        trip_id, item_count = _seed(
            engine,
            days_count=args.days,
            items_per_day=args.items_per_day,
            owner_places=args.owner_places,
        )
        with TestClient(app, base_url="http://localhost", raise_server_exceptions=False) as client:
            trip_report = _measure(
                client,
                lambda: client.get(f"/v1/trips/{trip_id}"),
                statements,
                iterations=args.iterations,
            )
            place_report = _measure(
                client,
                lambda: client.get("/v1/places"),
                statements,
                iterations=args.iterations,
            )
            export_report = _measure(
                client,
                lambda: client.post(
                    f"/v1/trips/{trip_id}/exports",
                    json={"format": "json"},
                ),
                statements,
                iterations=args.iterations,
            )
        return {
            "mode": "manual acceptance evidence; no latency threshold",
            "schema": schema,
            "workload": {
                "trip_days": args.days,
                "itinerary_items": item_count,
                "owner_places": args.owner_places,
                "iterations_per_request": args.iterations,
            },
            "requests": {
                "trip_detail": trip_report,
                "owner_places": place_report,
                "json_export": export_report,
            },
            "pool": {
                "size": 2,
                "max_overflow": 0,
                "checked_out_after_requests": engine.pool.checkedout(),
                "status": engine.pool.status(),
            },
        }
    finally:
        event.remove(engine, "before_cursor_execute", count_statement)
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old_overrides)
        app.state.auth_session_factory = old_factory
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=MAX_TRIP_DAYS)
    parser.add_argument("--items-per-day", type=int, default=1)
    parser.add_argument("--owner-places", type=int, default=500)
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument(
        "--confirm-disposable-database",
        action="store_true",
        help="confirm BENCHMARK_DATABASE_URL points to a disposable local PostgreSQL database",
    )
    args = parser.parse_args()
    database_url = os.environ.get("BENCHMARK_DATABASE_URL", "")
    if not args.confirm_disposable_database:
        parser.error("pass --confirm-disposable-database after reviewing the target database")
    if not database_url:
        parser.error("set BENCHMARK_DATABASE_URL to a disposable local PostgreSQL database")
    if args.days < 1 or args.items_per_day < 1 or args.owner_places < 1 or args.iterations < 1:
        parser.error("days, items per day, owner places, and iterations must be positive")
    try:
        print(json.dumps(_run(args, database_url), sort_keys=True, indent=2))
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "error_type": type(exc).__name__,
                    "error": "Benchmark did not complete; check the local database and settings.",
                }
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
