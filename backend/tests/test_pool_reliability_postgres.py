from __future__ import annotations

from collections.abc import Iterator
from time import monotonic

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from personal_travel.api.dependencies import session_dependency
from personal_travel.main import app


def test_api_pool_timeout_is_bounded_and_manual_reads_recover(
    database_engine: Engine, clean_database: None
) -> None:
    with database_engine.connect() as connection:
        schema = connection.scalar(text("SELECT current_schema()"))
    assert isinstance(schema, str) and schema.startswith("travel_test_")

    limited_engine = create_engine(
        str(database_engine.url),
        connect_args={"options": f"-csearch_path={schema}"},
        pool_size=1,
        max_overflow=0,
        pool_timeout=0.15,
        pool_pre_ping=True,
    )
    original_overrides = app.dependency_overrides.copy()
    original_factory = getattr(app.state, "auth_session_factory", None)
    factory = sessionmaker(bind=limited_engine, autoflush=False, expire_on_commit=False)

    def override_session() -> Iterator[Session]:
        with factory() as session:
            yield session

    app.dependency_overrides[session_dependency] = override_session
    app.state.auth_session_factory = factory
    try:
        with TestClient(app, base_url="http://localhost", raise_server_exceptions=False) as client:
            with limited_engine.connect() as held_connection:
                held_connection.execute(text("SELECT 1"))
                started = monotonic()
                unavailable = client.get("/v1/trips")
                elapsed = monotonic() - started

            assert unavailable.status_code == 503
            assert unavailable.json()["error"]["code"] == "database_unavailable"
            assert 0.10 <= elapsed < 2.0

            recovered = client.get("/v1/trips")
            assert recovered.status_code == 200
            assert recovered.json() == []
            assert limited_engine.pool.checkedout() == 0
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(original_overrides)
        app.state.auth_session_factory = original_factory
        limited_engine.dispose()
