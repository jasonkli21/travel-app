"""PostgreSQL fixtures use a disposable schema, never application tables.

TEST_DATABASE_URL grants schema creation in an isolated database. Every run applies
real migrations and uses the same session options as the application.
"""

from collections.abc import Iterator
from os import environ
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from personal_travel.api.dependencies import session_dependency
from personal_travel.main import app


@pytest.fixture(scope="session")
def database_engine() -> Iterator[Engine]:
    url = environ.get("TEST_DATABASE_URL")
    if url is None:
        pytest.skip("set TEST_DATABASE_URL to run PostgreSQL tests")
    schema = f"travel_test_{uuid4().hex}"
    admin = create_engine(url)
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "migrations"))
    try:
        with admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            command.check(config)
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()


@pytest.fixture
def clean_database(database_engine: Engine) -> None:
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE saved_places, itinerary_items, reservations, "
                "trip_days, trips, places CASCADE"
            )
        )


@pytest.fixture
def api_client(database_engine: Engine, clean_database: None) -> Iterator[TestClient]:
    def override_session() -> Iterator[Session]:
        with Session(database_engine, autoflush=False, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[session_dependency] = override_session
    try:
        with TestClient(app, base_url="http://localhost") as client:
            yield client
    finally:
        app.dependency_overrides.pop(session_dependency, None)
