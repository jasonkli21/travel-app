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
from sqlalchemy.orm import Session, sessionmaker

from personal_travel.api.dependencies import session_dependency
from personal_travel.config import get_settings
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
                "TRUNCATE booking_deletion_intents, booking_imports, source_attachments, "
                "owner_migration_audits, "
                "auth_sessions, oauth_login_attempts, "
                "auth_identities, itinerary_proposals, saved_places, itinerary_items, "
                "reservations, trip_days, trips, places CASCADE"
            )
        )


@pytest.fixture
def api_client(database_engine: Engine, clean_database: None) -> Iterator[TestClient]:
    original_overrides = app.dependency_overrides.copy()
    original_auth_factory = getattr(app.state, "auth_session_factory", None)
    original_settings_provider = getattr(app.state, "auth_settings_provider", None)

    def override_session() -> Iterator[Session]:
        with Session(database_engine, autoflush=False, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[session_dependency] = override_session
    auth_factory = sessionmaker(bind=database_engine, autoflush=False, expire_on_commit=False)
    app.state.auth_session_factory = auth_factory
    app.state.auth_settings_provider = get_settings
    try:
        with TestClient(app, base_url="http://localhost") as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(original_overrides)
        app.state.auth_session_factory = original_auth_factory
        app.state.auth_settings_provider = original_settings_provider
