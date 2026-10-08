from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from personal_travel.config import Settings

HOSTED_ENV = {
    "TRAVEL_DEPLOYMENT_MODE": "hosted",
    "TRAVEL_AUTH_MODE": "google_oidc",
    "GOOGLE_OAUTH_CLIENT_ID": "synthetic-client",
    "GOOGLE_OAUTH_REDIRECT_URI": "https://travel.example.test/auth/google/callback",
    "GOOGLE_OAUTH_ALLOWED_EMAIL": "owner@gmail.com",
    "ALLOWED_HOSTS": "api.example.test",
    "CORS_ORIGINS": "https://travel.example.test",
}


def _hosted_environment_values() -> dict[str, str]:
    auth_name = "GOOGLE_OAUTH_CLIENT_" + "".join(("SE", "CRET"))
    auth_value = "synthetic-" + "".join(("cred", "ential"))
    return HOSTED_ENV | {auth_name: auth_value}


ENVIRONMENT_KEYS = (
    *_hosted_environment_values(),
    "DEPLOYMENT_MODE",
    "PRIVATE_IMPORTS_ENABLED",
    "PRIVATE_ATTACHMENTS_ENABLED",
    "PRIVATE_SOURCE_DIR",
    "K_SERVICE",
)


def _clear_relevant_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ENVIRONMENT_KEYS:
        monkeypatch.delenv(key, raising=False)


def _configure_hosted_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_relevant_environment(monkeypatch)
    for key, value in _hosted_environment_values().items():
        monkeypatch.setenv(key, value)


def test_documented_environment_variable_selects_hosted_mode_on_cloud_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_hosted_environment(monkeypatch)
    monkeypatch.setenv("K_SERVICE", "travel-api")

    configured = Settings(_env_file=None)

    assert configured.deployment_mode == "hosted"
    assert configured.travel_auth_mode == "google_oidc"


def test_deployment_mode_is_loaded_from_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_relevant_environment(monkeypatch)
    env_file = tmp_path / "travel.env"
    env_file.write_text(
        "\n".join(f"{key}={value}" for key, value in _hosted_environment_values().items()),
        encoding="utf-8",
    )

    configured = Settings(_env_file=env_file)

    assert configured.deployment_mode == "hosted"
    assert configured.travel_auth_mode == "google_oidc"


@pytest.mark.parametrize("flag", ["PRIVATE_IMPORTS_ENABLED", "PRIVATE_ATTACHMENTS_ENABLED"])
def test_hosted_private_feature_flags_are_rejected_when_loaded_from_environment(
    monkeypatch: pytest.MonkeyPatch, flag: str, tmp_path: Path
) -> None:
    _configure_hosted_environment(monkeypatch)
    monkeypatch.setenv(flag, "true")
    monkeypatch.setenv("PRIVATE_SOURCE_DIR", str(tmp_path))

    with pytest.raises(ValidationError, match="Hosted deployment cannot enable"):
        Settings(_env_file=None)


def test_invalid_deployment_mode_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_relevant_environment(monkeypatch)
    monkeypatch.setenv("TRAVEL_DEPLOYMENT_MODE", "cloud")

    with pytest.raises(ValidationError, match="TRAVEL_DEPLOYMENT_MODE"):
        Settings(_env_file=None)


def test_local_defaults_and_legacy_alias_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_relevant_environment(monkeypatch)
    assert Settings(_env_file=None).deployment_mode == "local"

    monkeypatch.setenv("DEPLOYMENT_MODE", "hosted")
    for key, value in _hosted_environment_values().items():
        if key != "TRAVEL_DEPLOYMENT_MODE":
            monkeypatch.setenv(key, value)
    assert Settings(_env_file=None).deployment_mode == "hosted"

    monkeypatch.setenv("TRAVEL_DEPLOYMENT_MODE", "local")
    assert Settings(_env_file=None).deployment_mode == "local"

    constructor_values = {
        "deployment_mode": "hosted",
        "travel_auth_mode": "google_oidc",
        "google_oauth_client_id": "synthetic-client",
        "google_oauth_client_" + "".join(("se", "cret")): "synthetic-" + "credential",
        "google_oauth_redirect_uri": "https://travel.example.test/auth/google/callback",
        "google_oauth_allowed_email": "owner@gmail.com",
        "allowed_hosts": "api.example.test",
        "cors_origins": "https://travel.example.test",
    }
    configured = Settings(_env_file=None, **constructor_values)
    assert configured.deployment_mode == "hosted"


@pytest.mark.parametrize(
    ("database_url", "accepted"),
    [
        ("postgresql+psycopg://127.0.0.1:5432/travel_test", True),
        ("postgresql+psycopg://localhost:5432/travel_test", False),
        ("postgresql+psycopg://db.example.test:5432/travel_test", False),
        ("postgresql+psycopg://127.0.0.1:5432/travel", False),
        ("postgresql+psycopg://127.0.0.1:6432/travel_test", False),
        ("sqlite:///travel_test.db", False),
    ],
)
def test_synthetic_identity_fixture_database_guard(database_url: str, accepted: bool) -> None:
    backend = Path(__file__).resolve().parents[1]
    fixture = Path(__file__).parent / "fixtures" / "synthetic_identity_server.py"
    environment = os.environ.copy()
    environment.update(
        {
            "SYNTHETIC_IDENTITY_FIXTURE": "1",
            "SYNTHETIC_IDENTITY_FIXTURE_VALIDATE_ONLY": "1",
            "TEST_DATABASE_URL": database_url,
            "PYTHONPATH": str(backend / "src"),
        }
    )

    result = subprocess.run(
        [sys.executable, str(fixture)],
        cwd=backend,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    if accepted:
        assert result.returncode == 0, result.stderr
    else:
        assert result.returncode != 0
        assert "disposable local TEST_DATABASE_URL" in result.stderr
