from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker

from personal_travel.api.dependencies import session_dependency
from personal_travel.api.middleware import _provider_operation
from personal_travel.config import Settings
from personal_travel.main import app
from personal_travel.models.auth import ProviderQuotaBucket
from personal_travel.services.provider_admission import (
    GLOBAL_SCOPE_HASH,
    ProviderAdmissionUnavailable,
    QuotaExceeded,
    admit_provider_request,
    purge_quota_buckets,
)


@pytest.fixture
def quota_factory(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'quota.db'}")
    ProviderQuotaBucket.__table__.create(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()


def test_owner_window_limits_are_atomic_and_owner_values_are_hashed(quota_factory) -> None:
    now = datetime(2026, 10, 5, 12, 4, 30, tzinfo=UTC)
    for _ in range(8):
        admitted = admit_provider_request(
            quota_factory,
            owner_id="synthetic-owner@example.test",
            operation="geoapify_search",
            global_per_minute=30,
            global_per_day=200,
            now=now,
        )
        assert admitted.charged_units == 1
    with pytest.raises(QuotaExceeded) as exceeded:
        admit_provider_request(
            quota_factory,
            owner_id="synthetic-owner@example.test",
            operation="geoapify_search",
            global_per_minute=30,
            global_per_day=200,
            now=now,
        )
    assert exceeded.value.retry_after_seconds == 30

    with quota_factory() as session:
        rows = list(session.scalars(select(ProviderQuotaBucket)))
    assert all("synthetic-owner" not in row.scope_hash + row.bucket_key for row in rows)
    assert len(rows) == 4
    assert sum(row.used for row in rows if row.scope_hash == GLOBAL_SCOPE_HASH) == 16


def test_provider_global_cap_is_shared_and_route_weight_is_reserved(quota_factory) -> None:
    now = datetime(2026, 10, 5, 12, 4, 30, tzinfo=UTC)
    result = admit_provider_request(
        quota_factory,
        owner_id="owner-a",
        operation="geoapify_route",
        global_per_minute=6,
        global_per_day=60,
        now=now,
    )
    assert result.charged_units == 6

    with pytest.raises(QuotaExceeded):
        admit_provider_request(
            quota_factory,
            owner_id="owner-b",
            operation="geoapify_search",
            global_per_minute=6,
            global_per_day=60,
            now=now,
        )

    reset = now + timedelta(seconds=30)
    admit_provider_request(
        quota_factory,
        owner_id="owner-b",
        operation="geoapify_search",
        global_per_minute=6,
        global_per_day=60,
        now=reset,
    )


def test_quota_cleanup_is_bounded_and_only_removes_expired_buckets(quota_factory) -> None:
    old = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    current = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
    admit_provider_request(
        quota_factory,
        owner_id="owner-a",
        operation="personal_ai_research",
        global_per_minute=12,
        global_per_day=100,
        now=old,
    )
    with quota_factory() as session, session.begin():
        removed = purge_quota_buckets(session, before=current - timedelta(days=2), limit=4)
    assert removed == 4
    with quota_factory() as session:
        assert session.scalar(select(func.count()).select_from(ProviderQuotaBucket)) == 0


def test_provider_operation_mapping_is_gated_and_excludes_local_mutations() -> None:
    settings = Settings(_env_file=None).model_copy(
        update={
            "geoapify_api_key": SecretStr("synthetic-key"),
            "personal_ai_research_enabled": True,
            "personal_ai_comparisons_enabled": True,
            "personal_ai_proposals_enabled": True,
            "personal_ai_extractions_enabled": True,
        }
    )
    trip_id = "12345678-1234-1234-1234-123456789abc"
    import_id = "abcdefab-cdef-abcd-efab-cdefabcdefab"
    assert (
        _provider_operation(f"/v1/trips/{trip_id}/places/search", "GET", settings)
        == "geoapify_search"
    )
    assert (
        _provider_operation(f"/v1/trips/{trip_id}/logistics/estimate", "POST", settings)
        == "geoapify_route"
    )
    assert (
        _provider_operation(f"/v1/trips/{trip_id}/research", "POST", settings)
        == "personal_ai_research"
    )
    assert (
        _provider_operation(f"/v1/trips/{trip_id}/research/compare", "POST", settings)
        == "personal_ai_comparison"
    )
    assert (
        _provider_operation(f"/v1/trips/{trip_id}/proposals", "POST", settings)
        == "personal_ai_proposal"
    )
    assert (
        _provider_operation(f"/v1/trips/{trip_id}/imports/{import_id}/extract", "POST", settings)
        == "personal_ai_extraction"
    )
    assert (
        _provider_operation(f"/v1/trips/{trip_id}/imports/{import_id}/confirm", "POST", settings)
        is None
    )
    assert (
        _provider_operation("/v1/private-import-deletion-intents/retry", "POST", settings) is None
    )
    assert _provider_operation(f"/v1/trips/{trip_id}/proposals", "GET", settings) is None
    assert (
        _provider_operation(
            f"/v1/trips/{trip_id}/research/comparisons/{import_id}/candidates/{import_id}/save",
            "POST",
            settings,
        )
        is None
    )
    no_providers = Settings(_env_file=None)
    assert _provider_operation(f"/v1/trips/{trip_id}/places/search", "GET", no_providers) is None


def test_compact_uuid_paths_receive_the_same_provider_admission_mapping() -> None:
    settings = Settings(_env_file=None).model_copy(
        update={
            "geoapify_api_key": SecretStr("synthetic-key"),
            "personal_ai_research_enabled": True,
            "personal_ai_extractions_enabled": True,
        }
    )
    trip_id = "12345678123412341234123456789abc"
    import_id = "abcdefabcdefabcdefabcdefabcdefab"
    assert (
        _provider_operation(f"/v1/trips/{trip_id}/places/search", "GET", settings)
        == "geoapify_search"
    )
    assert (
        _provider_operation(f"/v1/trips/{trip_id}/research", "POST", settings)
        == "personal_ai_research"
    )
    assert (
        _provider_operation(f"/v1/trips/{trip_id}/imports/{import_id}/extract", "POST", settings)
        == "personal_ai_extraction"
    )
    # Local privacy deletion has its own late-bound admission immediately
    # before the upstream call; the mutation route itself is never blocked.
    assert (
        _provider_operation(f"/v1/trips/{trip_id}/imports/{import_id}/source", "DELETE", settings)
        is None
    )


def test_hosted_settings_require_verified_identity_and_explicit_web_origins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError, match="verified Google identity"):
        Settings(_env_file=None, deployment_mode="hosted")

    hosted = Settings(
        _env_file=None,
        deployment_mode="hosted",
        travel_auth_mode="google_oidc",
        google_oauth_client_id="synthetic-client",
        google_oauth_client_secret="synthetic-secret",
        google_oauth_redirect_uri="https://travel.example.test/auth/google/callback",
        google_oauth_allowed_email="owner@gmail.com",
        allowed_hosts="api.example.test",
        cors_origins="https://travel.example.test",
    )
    assert hosted.deployment_mode == "hosted"

    with pytest.raises(ValueError, match="explicit non-loopback API hosts"):
        Settings(
            _env_file=None,
            deployment_mode="hosted",
            travel_auth_mode="google_oidc",
            google_oauth_client_id="synthetic-client",
            google_oauth_client_secret="synthetic-secret",
            google_oauth_redirect_uri="https://travel.example.test/auth/google/callback",
            google_oauth_allowed_email="owner@gmail.com",
            allowed_hosts="*",
            cors_origins="https://travel.example.test",
        )

    monkeypatch.setenv("K_SERVICE", "travel-api")
    with pytest.raises(ValueError, match="TRAVEL_DEPLOYMENT_MODE=hosted"):
        Settings(_env_file=None, deployment_mode="local")


def test_http_provider_budget_returns_retry_after_before_route_dispatch(
    quota_factory,
) -> None:
    settings = Settings(_env_file=None).model_copy(
        update={"geoapify_api_key": SecretStr("synthetic-key")}
    )
    original_factory = getattr(app.state, "auth_session_factory", None)
    original_settings_provider = getattr(app.state, "auth_settings_provider", None)
    app.state.auth_session_factory = quota_factory
    app.state.auth_settings_provider = lambda: settings

    def route_database_unavailable():
        raise RuntimeError("private database failure")

    app.dependency_overrides[session_dependency] = route_database_unavailable
    trip_id = "12345678-1234-1234-1234-123456789abc"
    try:
        with TestClient(app, base_url="http://localhost") as client:
            first = client.get(f"/v1/trips/{trip_id}/places/search", params={"q": "coffee"})
            assert first.status_code == 500
            # Fill the remaining seven owner units without dispatching the route.
            for _ in range(7):
                admit_provider_request(
                    quota_factory,
                    owner_id="local",
                    operation="geoapify_search",
                    global_per_minute=30,
                    global_per_day=200,
                )
            denied = client.get(f"/v1/trips/{trip_id}/places/search", params={"q": "coffee"})
            compact_denied = client.get(
                "/v1/trips/12345678123412341234123456789abc/places/search",
                params={"q": "coffee"},
            )
    finally:
        app.dependency_overrides.pop(session_dependency, None)
        app.state.auth_session_factory = original_factory
        app.state.auth_settings_provider = original_settings_provider

    assert denied.status_code == 429
    assert denied.headers["Retry-After"]
    assert denied.json()["error"]["code"] == "provider_quota_exceeded"
    assert compact_denied.status_code == 429


def test_postgres_shared_budget_is_atomic_across_connections(database_engine) -> None:
    factory = sessionmaker(bind=database_engine, autoflush=False, expire_on_commit=False)
    with database_engine.begin() as connection:
        connection.execute(text("TRUNCATE provider_quota_buckets"))
    now = datetime.now(UTC).replace(second=0, microsecond=0)

    def attempt(index: int) -> bool:
        try:
            admit_provider_request(
                factory,
                owner_id=f"synthetic-owner-{index}",
                operation="geoapify_search",
                global_per_minute=4,
                global_per_day=100,
                now=now,
            )
            return True
        except QuotaExceeded:
            return False
        except ProviderAdmissionUnavailable as exc:
            pytest.fail(f"Provider admission unexpectedly failed: {exc!r}")

    with ThreadPoolExecutor(max_workers=12) as executor:
        admitted = list(executor.map(attempt, range(12)))
    assert sum(admitted) == 4
