from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from google.auth.crypt import RSASigner
from google.auth.jwt import encode
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

import personal_travel.api.routes.auth as auth_routes
import personal_travel.api.routes.imports as import_routes
import personal_travel.api.routes.private_deletions as private_deletion_routes
from personal_travel.auth.contracts import PersonalAIAuthContext, VerifiedPrincipal
from personal_travel.auth.google_oidc import stable_google_owner_id
from personal_travel.auth.sessions import create_session, secret_digest
from personal_travel.config import Settings, get_settings
from personal_travel.models import AuthIdentity, AuthSession, OAuthLoginAttempt, Trip

pytestmark = pytest.mark.usefixtures("clean_database")


def google_settings(**updates: object) -> Settings:
    values: dict[str, object] = {
        "travel_auth_mode": "google_oidc",
        "google_oauth_client_id": "travel-client.apps.googleusercontent.com",
        "google_oauth_client_secret": "synthetic-client-secret",
        "google_oauth_redirect_uri": "https://travel.test/auth/google/callback",
        "google_oauth_allowed_email": "owner@gmail.com",
        "personal_ai_base_url": "https://travel-ai.test",
    }
    return Settings(**(values | updates))


def use_settings(client: TestClient, settings: Settings) -> None:
    client.app.state.auth_settings_provider = lambda: settings
    client.app.dependency_overrides[get_settings] = lambda: settings


def make_identity_and_session(engine: Engine) -> tuple[str, str]:
    subject = "synthetic-subject"
    owner_id = stable_google_owner_id("https://accounts.google.com", subject)
    principal = VerifiedPrincipal(
        issuer="https://accounts.google.com",
        subject=subject,
        owner_id=owner_id,
        email="owner@gmail.com",
        issued_at=int(time.time()),
        expires_at=int(time.time()) + 3600,
    )
    with Session(engine, expire_on_commit=False) as session, session.begin():
        raw_token, csrf_token, _active = create_session(session, principal, ttl_seconds=3600)
    return raw_token, csrf_token


def signed_token(
    nonce: str,
    *,
    audience: str = "travel-client.apps.googleusercontent.com",
    subject: str = "synthetic-login-subject",
) -> tuple[str, str, str]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    signer = RSASigner.from_string(private_bytes)
    now = datetime.now(UTC)
    certificate_subject = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "synthetic-login-key")]
    )
    cert = (
        (
            x509.CertificateBuilder()
            .subject_name(certificate_subject)
            .issuer_name(certificate_subject)
            .public_key(private_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(days=1))
            .sign(private_key, hashes.SHA256())
        )
        .public_bytes(serialization.Encoding.PEM)
        .decode("ascii")
    )
    token = encode(
        signer,
        {
            "iss": "https://accounts.google.com",
            "sub": subject,
            "aud": audience,
            "email": "owner@gmail.com",
            "email_verified": True,
            "iat": int(time.time()),
            "exp": int(time.time()) + 3600,
            "nonce": nonce,
        },
        key_id="synthetic-login-key",
    ).decode("ascii")
    return token, cert, subject


def set_google_key_fixture(monkeypatch: pytest.MonkeyPatch, certificate: str) -> None:
    def get_keys(_url: str, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            status=200,
            data=json.dumps({"synthetic-login-key": certificate}).encode(),
            headers={"cache-control": "public, max-age=60"},
        )

    monkeypatch.setattr("personal_travel.auth.google_oidc._key_request", get_keys)
    monkeypatch.setattr(
        "personal_travel.auth.google_oidc._verified_tokens", __import__("collections").OrderedDict()
    )


def cookie_value(response: ResponseLike, name: str) -> str:
    for header in response.headers.get_list("set-cookie"):
        pair = header.split(";", 1)[0]
        key, separator, value = pair.partition("=")
        if separator and key == name:
            return value
    raise AssertionError(f"missing cookie {name}")


class ResponseLike:
    headers: object


def test_all_domain_route_families_require_session_before_body_or_service(
    api_client: TestClient,
) -> None:
    settings = google_settings()
    use_settings(api_client, settings)
    public_auth_paths = {
        ("GET", "/v1/auth/session"),
        ("GET", "/v1/auth/google/start"),
        ("POST", "/v1/auth/google/callback"),
    }
    domain_paths: set[tuple[str, str]] = set()
    for route_path, operation in api_client.app.openapi()["paths"].items():
        if not route_path.startswith("/v1/"):
            continue
        path = route_path
        parameter_names = (
            "trip_id",
            "day_id",
            "item_id",
            "place_id",
            "reservation_id",
            "saved_place_id",
            "proposal_id",
            "idempotency_key",
        )
        for name in parameter_names:
            path = path.replace(f"{{{name}}}", str(uuid4()))
        for method in operation:
            method = method.upper()
            if method in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                if (method, path) not in public_auth_paths:
                    domain_paths.add((method, path))

    assert len(domain_paths) >= 30
    for method, path in sorted(domain_paths):
        response = api_client.request(method, path, content=b"not-json")
        assert response.status_code == 401, (method, path, response.text)

    assert api_client.get("/health").status_code == 200
    assert api_client.get("/ready").status_code == 200
    assert api_client.get("/openapi.json").status_code == 200
    session_status = api_client.get("/v1/auth/session").json()
    assert session_status == {
        "mode": "google_oidc",
        "authenticated": False,
        "email": None,
        "expires_at": None,
    }
    stale_session = {settings.auth_session_cookie_name: "expired-session"}
    stale_status = api_client.get("/v1/auth/session", cookies=stale_session)
    assert stale_status.status_code == 200
    assert stale_status.json()["authenticated"] is False
    assert api_client.get("/v1/auth/google/start", cookies=stale_session).status_code == 200
    assert api_client.get("/v1/trips", headers={"x-owner-id": "attacker"}).status_code == 400


def test_local_mode_rejects_stale_google_credentials_instead_of_falling_back(
    api_client: TestClient,
) -> None:
    local = Settings(travel_auth_mode="local", owner_id="local")
    use_settings(api_client, local)
    assert api_client.get("/v1/trips").status_code == 200
    assert (
        api_client.get(
            "/v1/trips",
            cookies={local.auth_session_cookie_name: "forged-session"},
        ).status_code
        == 401
    )
    assert (
        api_client.get("/v1/trips", headers={"authorization": "Bearer forged"}).status_code == 401
    )


def test_verified_session_scopes_owner_and_csrf_logout_revokes_session(
    api_client: TestClient,
    database_engine: Engine,
) -> None:
    settings = google_settings()
    use_settings(api_client, settings)
    raw_session, csrf = make_identity_and_session(database_engine)
    foreign_trip = Trip(
        owner_id="local",
        title="local owner trip",
        start_date=datetime(2026, 1, 1, tzinfo=UTC).date(),
        end_date=datetime(2026, 1, 1, tzinfo=UTC).date(),
        timezone="UTC",
    )
    with Session(database_engine) as session, session.begin():
        session.add(foreign_trip)
        session.flush()
        foreign_trip_id = foreign_trip.id

    cookies = {settings.auth_session_cookie_name: raw_session}
    session_status = api_client.get("/v1/auth/session", cookies=cookies)
    assert session_status.status_code == 200
    assert session_status.json()["authenticated"] is True
    assert api_client.get(f"/v1/trips/{foreign_trip_id}", cookies=cookies).status_code == 404

    no_csrf = api_client.post(
        "/v1/trips",
        cookies=cookies,
        headers={"origin": "http://localhost:3000"},
        content=b"not-json",
    )
    assert no_csrf.status_code == 403
    missing_origin = api_client.post(
        "/v1/trips",
        cookies={**cookies, settings.auth_csrf_cookie_name: csrf},
        headers={"x-csrf-token": csrf},
        content=b"not-json",
    )
    assert missing_origin.status_code == 403
    wrong_csrf = api_client.post(
        "/v1/trips",
        cookies={**cookies, settings.auth_csrf_cookie_name: csrf},
        headers={"origin": "http://localhost:3000", "x-csrf-token": "wrong"},
        content=b"not-json",
    )
    assert wrong_csrf.status_code == 403
    logout = api_client.post(
        "/v1/auth/logout",
        cookies={**cookies, settings.auth_csrf_cookie_name: csrf},
        headers={
            "origin": "http://localhost:3000",
            "x-csrf-token": csrf,
        },
    )
    assert logout.status_code == 204
    expired_status = api_client.get("/v1/auth/session", cookies=cookies)
    assert expired_status.status_code == 200
    assert expired_status.json()["authenticated"] is False


def test_existing_session_loses_access_when_owner_allowlist_changes(
    api_client: TestClient, database_engine: Engine
) -> None:
    original = google_settings()
    use_settings(api_client, original)
    raw_session, _csrf = make_identity_and_session(database_engine)
    cookies = {original.auth_session_cookie_name: raw_session}
    assert api_client.get("/v1/trips", cookies=cookies).status_code == 200
    use_settings(api_client, google_settings(google_oauth_allowed_email="another@gmail.com"))
    assert api_client.get("/v1/trips", cookies=cookies).status_code == 401
    assert api_client.get("/v1/auth/session", cookies=cookies).json()["authenticated"] is False


def test_google_code_flow_binds_state_browser_cookie_nonce_and_single_use(
    api_client: TestClient,
    database_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = google_settings(
        personal_ai_auth_mode="google_cloud_run_iam",
        personal_ai_user_id_token_audience="travel-client.apps.googleusercontent.com",
        personal_ai_service_iam_audience="https://travel-ai.test",
        personal_ai_service_account="travel-ai@project.iam.gserviceaccount.com",
    )
    use_settings(api_client, settings)
    start = api_client.get("/v1/auth/google/start")
    assert start.status_code == 200
    authorization = urlsplit(start.json()["authorization_url"])
    values = parse_qs(authorization.query)
    assert authorization.hostname == "accounts.google.com"
    assert values["code_challenge_method"] == ["S256"]
    assert values["scope"] == ["openid email"]
    state = values["state"][0]
    nonce = values["nonce"][0]
    flow_cookie = cookie_value(start, settings.auth_oauth_flow_cookie_name)
    token, certificate, subject = signed_token(nonce)
    set_google_key_fixture(monkeypatch, certificate)

    async def exchange(_code: str, _verifier: str, _settings: Settings) -> str:
        return token

    monkeypatch.setattr("personal_travel.api.routes.auth._exchange_code", exchange)
    with caplog.at_level(logging.DEBUG):
        callback = api_client.post(
            "/v1/auth/google/callback",
            cookies={settings.auth_oauth_flow_cookie_name: flow_cookie},
            json={"code": "synthetic-code", "state": state},
        )
    assert callback.status_code == 200, callback.text
    raw_session = cookie_value(callback, settings.auth_session_cookie_name)
    csrf = cookie_value(callback, settings.auth_csrf_cookie_name)
    user_id_token = cookie_value(callback, settings.auth_ai_token_cookie_name)
    assert user_id_token == token
    assert token not in caplog.text
    assert flow_cookie not in caplog.text
    assert "synthetic-code" not in caplog.text
    cookie_headers = callback.headers.get_list("set-cookie")
    session_cookie = next(
        value
        for value in cookie_headers
        if value.startswith(f"{settings.auth_session_cookie_name}=")
    )
    csrf_cookie = next(
        value for value in cookie_headers if value.startswith(f"{settings.auth_csrf_cookie_name}=")
    )
    ai_cookie = next(
        value
        for value in cookie_headers
        if value.startswith(f"{settings.auth_ai_token_cookie_name}=")
    )
    assert "secure" in session_cookie.lower() and "httponly" in session_cookie.lower()
    assert "samesite=lax" in session_cookie.lower() and "path=/" in session_cookie.lower()
    assert "httponly" not in csrf_cookie.lower() and "samesite=strict" in csrf_cookie.lower()
    assert "secure" in ai_cookie.lower() and "httponly" in ai_cookie.lower()
    with Session(database_engine) as session:
        identity = session.scalar(select(AuthIdentity))
        assert identity is not None
        assert identity.subject == subject
        assert (
            identity.owner_id
            == "usr_"
            + hashlib.sha256(f"https://accounts.google.com\0{subject}".encode()).hexdigest()[:32]
        )
        saved = session.scalar(select(AuthSession))
        assert saved is not None
        assert saved.token_hash == secret_digest(raw_session)
        assert saved.csrf_token_hash == secret_digest(csrf)
        assert raw_session not in saved.token_hash
        assert saved.expires_at <= datetime.now(UTC) + timedelta(seconds=3540)
        assert session.scalar(select(OAuthLoginAttempt)) is None

    assert (
        api_client.get(
            "/v1/auth/session", cookies={settings.auth_session_cookie_name: raw_session}
        ).json()["authenticated"]
        is True
    )

    second_start = api_client.get(
        "/v1/auth/google/start", cookies={settings.auth_session_cookie_name: raw_session}
    )
    second_values = parse_qs(urlsplit(second_start.json()["authorization_url"]).query)
    second_nonce = second_values["nonce"][0]
    second_state = second_values["state"][0]
    second_flow_cookie = cookie_value(second_start, settings.auth_oauth_flow_cookie_name)
    second_token, second_certificate, _ = signed_token(second_nonce)
    set_google_key_fixture(monkeypatch, second_certificate)

    async def second_exchange(_code: str, _verifier: str, _settings: Settings) -> str:
        return second_token

    monkeypatch.setattr("personal_travel.api.routes.auth._exchange_code", second_exchange)
    rotated = api_client.post(
        "/v1/auth/google/callback",
        cookies={
            settings.auth_session_cookie_name: raw_session,
            settings.auth_oauth_flow_cookie_name: second_flow_cookie,
        },
        json={"code": "second-synthetic-code", "state": second_state},
    )
    assert rotated.status_code == 200, rotated.text
    rotated_session = cookie_value(rotated, settings.auth_session_cookie_name)
    assert rotated_session != raw_session
    assert (
        api_client.get(
            "/v1/auth/session", cookies={settings.auth_session_cookie_name: raw_session}
        ).json()["authenticated"]
        is False
    )
    assert (
        api_client.get(
            "/v1/auth/session", cookies={settings.auth_session_cookie_name: rotated_session}
        ).json()["authenticated"]
        is True
    )

    replay = api_client.post(
        "/v1/auth/google/callback",
        cookies={settings.auth_oauth_flow_cookie_name: flow_cookie},
        json={"code": "synthetic-code", "state": state},
    )
    assert replay.status_code == 401


def test_callback_timeout_rolls_back_abandoned_session_write(
    api_client: TestClient, database_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = google_settings()
    use_settings(api_client, settings)
    start = api_client.get("/v1/auth/google/start")
    values = parse_qs(urlsplit(start.json()["authorization_url"]).query)
    state = values["state"][0]
    token, certificate, _subject = signed_token(values["nonce"][0])
    set_google_key_fixture(monkeypatch, certificate)

    async def exchange(_code: str, _verifier: str, _settings: Settings) -> str:
        return token

    original_create = auth_routes.create_session

    def slow_create(*args: object, **kwargs: object):
        result = original_create(*args, **kwargs)
        time.sleep(0.12)
        return result

    monkeypatch.setattr(auth_routes, "_exchange_code", exchange)
    monkeypatch.setattr(auth_routes, "create_session", slow_create)
    monkeypatch.setattr(auth_routes, "CALLBACK_DEADLINE_SECONDS", 0.06)
    callback = api_client.post(
        "/v1/auth/google/callback",
        cookies={
            settings.auth_oauth_flow_cookie_name: cookie_value(
                start, settings.auth_oauth_flow_cookie_name
            )
        },
        json={"code": "synthetic-code", "state": state},
    )
    assert callback.status_code == 503
    time.sleep(0.15)  # let the abandoned worker reach its deadline check
    with Session(database_engine) as session:
        assert session.scalar(select(AuthSession)) is None


def test_callback_blocked_session_flush_rolls_back(
    api_client: TestClient, database_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = google_settings()
    use_settings(api_client, settings)
    start = api_client.get("/v1/auth/google/start")
    values = parse_qs(urlsplit(start.json()["authorization_url"]).query)
    token, certificate, _subject = signed_token(values["nonce"][0])
    set_google_key_fixture(monkeypatch, certificate)

    async def exchange(_code: str, _verifier: str, _settings: Settings) -> str:
        return token

    monkeypatch.setattr(auth_routes, "_exchange_code", exchange)
    monkeypatch.setattr(auth_routes, "CALLBACK_DEADLINE_SECONDS", 0.2)
    with database_engine.connect() as blocker, blocker.begin():
        blocker.execute(text("LOCK TABLE auth_sessions IN ACCESS EXCLUSIVE MODE"))
        callback = api_client.post(
            "/v1/auth/google/callback",
            cookies={
                settings.auth_oauth_flow_cookie_name: cookie_value(
                    start, settings.auth_oauth_flow_cookie_name
                )
            },
            json={"code": "synthetic-code", "state": values["state"][0]},
        )
        assert callback.status_code == 503
    time.sleep(0.1)  # an abandoned worker must finish before inspecting committed rows
    with Session(database_engine) as session:
        assert session.scalar(select(AuthSession)) is None


def test_ai_user_token_must_be_valid_and_match_current_session_before_body_parse(
    api_client: TestClient,
    database_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = google_settings(
        personal_ai_auth_mode="google_cloud_run_iam",
        personal_ai_user_id_token_audience="travel-client.apps.googleusercontent.com",
        personal_ai_service_iam_audience="https://travel-ai.test",
        personal_ai_service_account="travel-ai@project.iam.gserviceaccount.com",
        personal_ai_research_enabled=True,
        personal_ai_comparisons_enabled=True,
    )
    use_settings(api_client, settings)
    raw_session, csrf = make_identity_and_session(database_engine)
    trip_id = uuid4()
    import_id = uuid4()
    comparison_id = uuid4()
    candidate_id = uuid4()
    paths = [
        f"/v1/trips/{trip_id}/research",
        f"/v1/trips/{trip_id}/research/compare",
        f"/v1/trips/{trip_id}/research/comparisons/{comparison_id}/candidates/{candidate_id}/save",
        f"/v1/trips/{trip_id}/imports/{import_id}/confirm",
        f"/v1/trips/{trip_id}/imports/{import_id}/reject",
        f"/v1/trips/{trip_id}/imports/{import_id}/source",
        "/v1/private-import-deletion-intents/retry",
    ]
    cookies = {
        settings.auth_session_cookie_name: raw_session,
        settings.auth_csrf_cookie_name: csrf,
    }
    common_headers = {"origin": "http://localhost:3000", "x-csrf-token": csrf}

    wrong_owner, certificate, _subject = signed_token(
        "synthetic-ai-nonce",
    )
    set_google_key_fixture(monkeypatch, certificate)
    for path in paths:
        method = "DELETE" if path.endswith("/source") else "POST"
        wrong_audience_response = api_client.request(
            method,
            path,
            cookies=cookies,
            headers={**common_headers, "x-user-id-token": wrong_owner},
            content=b"not-json" if method == "POST" else None,
        )
        assert wrong_audience_response.status_code == 403
        assert wrong_audience_response.json()["error"]["code"] == "ai_identity_mismatch"

    valid_audience, certificate, _subject = signed_token(
        "synthetic-other-ai-nonce",
        audience="another-client.apps.googleusercontent.com",
    )
    set_google_key_fixture(monkeypatch, certificate)
    for path in paths:
        method = "DELETE" if path.endswith("/source") else "POST"
        invalid_audience_response = api_client.request(
            method,
            path,
            cookies=cookies,
            headers={**common_headers, "x-user-id-token": valid_audience},
            content=b"not-json" if method == "POST" else None,
        )
        assert invalid_audience_response.status_code == 401
        assert invalid_audience_response.json()["error"]["code"] == "invalid_ai_identity"

    missing_user_token = api_client.post(
        f"/v1/trips/{trip_id}/imports/{import_id}/reject",
        cookies=cookies,
        headers=common_headers,
        content=b"not-json",
    )
    assert missing_user_token.status_code == 401
    assert missing_user_token.json()["error"]["code"] == "ai_identity_required"


def test_comparison_routes_use_the_comparison_gate_independently_of_research(
    api_client: TestClient,
    database_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trip_id, comparison_id, candidate_id = (uuid4() for _ in range(3))
    paths = [
        f"/v1/trips/{trip_id}/research/compare",
        f"/v1/trips/{trip_id}/research/comparisons/{comparison_id}/candidates/{candidate_id}/save",
    ]
    raw_session, csrf = make_identity_and_session(database_engine)
    cookies = {
        "__Host-travel_session": raw_session,
        "__Host-travel_csrf": csrf,
    }
    headers = {"origin": "http://localhost:3000", "x-csrf-token": csrf}
    user_token, certificate, _subject = signed_token(
        "comparison-gate-probe", subject="synthetic-subject"
    )
    set_google_key_fixture(monkeypatch, certificate)

    comparison_only = google_settings(
        personal_ai_auth_mode="google_cloud_run_iam",
        personal_ai_user_id_token_audience="travel-client.apps.googleusercontent.com",
        personal_ai_service_iam_audience="https://travel-ai.test",
        personal_ai_service_account="travel-ai@project.iam.gserviceaccount.com",
        personal_ai_comparisons_enabled=True,
        personal_ai_research_enabled=False,
    )
    use_settings(api_client, comparison_only)
    for path in paths:
        response = api_client.post(
            path,
            cookies=cookies,
            headers={**headers, "x-user-id-token": user_token},
            content=b"not-json",
        )
        assert response.status_code == 422, response.text

    research_only = google_settings(
        personal_ai_auth_mode="google_cloud_run_iam",
        personal_ai_user_id_token_audience="travel-client.apps.googleusercontent.com",
        personal_ai_service_iam_audience="https://travel-ai.test",
        personal_ai_service_account="travel-ai@project.iam.gserviceaccount.com",
        personal_ai_comparisons_enabled=False,
        personal_ai_research_enabled=True,
    )
    use_settings(api_client, research_only)
    for path in paths:
        response = api_client.post(
            path,
            cookies=cookies,
            headers={**headers, "x-user-id-token": user_token},
            content=b"not-json",
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_identity_header"


def test_ai_requires_verified_user_token_matching_session_before_domain_access(
    api_client: TestClient,
    database_engine: Engine,
) -> None:
    settings = google_settings(
        personal_ai_auth_mode="google_cloud_run_iam",
        personal_ai_user_id_token_audience="travel-client.apps.googleusercontent.com",
        personal_ai_service_iam_audience="https://travel-ai.test",
        personal_ai_service_account="travel-ai@project.iam.gserviceaccount.com",
        personal_ai_research_enabled=True,
    )
    use_settings(api_client, settings)
    raw_session, csrf = make_identity_and_session(database_engine)
    response = api_client.post(
        f"/v1/trips/{uuid4()}/research",
        cookies={
            settings.auth_session_cookie_name: raw_session,
            settings.auth_csrf_cookie_name: csrf,
        },
        headers={"origin": "http://localhost:3000", "x-csrf-token": csrf},
        content=b"not-json",
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "ai_identity_required"


def test_authenticated_cleanup_routes_receive_verified_user_and_service_context(
    api_client: TestClient,
    database_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = google_settings(
        personal_ai_auth_mode="google_cloud_run_iam",
        personal_ai_user_id_token_audience="travel-client.apps.googleusercontent.com",
        personal_ai_service_iam_audience="https://travel-ai.test",
        personal_ai_service_account="travel-ai@project.iam.gserviceaccount.com",
        private_imports_enabled=True,
        private_source_dir="/private/tmp/phase6-synthetic-source",
    )
    use_settings(api_client, settings)
    raw_session, csrf = make_identity_and_session(database_engine)
    user_token, certificate, _ = signed_token("synthetic-ai-nonce", subject="synthetic-subject")
    set_google_key_fixture(monkeypatch, certificate)
    headers = {
        "origin": "http://localhost:3000",
        "x-csrf-token": csrf,
        "x-user-id-token": user_token,
    }
    cookies = {
        settings.auth_session_cookie_name: raw_session,
        settings.auth_csrf_cookie_name: csrf,
    }
    trip_id, confirm_id, reject_id, source_id = (uuid4() for _ in range(4))
    retentions = {
        confirm_id: "delete_after_confirmation",
        reject_id: "keep_until_expiry",
    }
    observed: list[tuple[str, PersonalAIAuthContext | None]] = []

    class Store:
        def close(self) -> None:
            return None

    class SourceService:
        def get_import(self, _owner_id, _trip_id, import_id):
            return ({"id": str(import_id), "retention_choice": retentions[import_id]}, None)

    class BookingService:
        def __init__(self, context):
            self.context = context

        def reject(self, _owner_id, _trip_id, import_id):
            observed.append(("reject", self.context))
            return {"id": str(import_id), "state": "rejected"}

        def confirm(self, _owner_id, _trip_id, import_id, _payload):
            observed.append(("confirm", self.context))
            return {"import_id": str(import_id), "saved": True}

        def delete_upstream_extraction(self, _owner_id, _trip_id, _import_id):
            observed.append(("reject_delete", self.context))

    monkeypatch.setattr(import_routes, "_gate", lambda: Store())
    monkeypatch.setattr(import_routes, "_service", lambda _request: SourceService())
    monkeypatch.setattr(
        import_routes,
        "_booking_service",
        lambda request: BookingService(request.scope.get("personal_ai_auth_context")),
    )
    monkeypatch.setattr(
        import_routes,
        "_delete_source",
        lambda request, *_args: observed.append(
            ("source_delete", request.scope.get("personal_ai_auth_context"))
        ),
    )

    confirm = api_client.post(
        f"/v1/trips/{trip_id}/imports/{confirm_id}/confirm",
        cookies=cookies,
        headers=headers,
        json={
            "confirmation_key": str(uuid4()),
            "expected_trip_revision": 0,
            "expected_import_revision": 0,
            "entries": [],
        },
    )
    reject = api_client.post(
        f"/v1/trips/{trip_id}/imports/{reject_id}/reject",
        cookies=cookies,
        headers=headers,
    )
    source_delete = api_client.delete(
        f"/v1/trips/{trip_id}/imports/{source_id}/source",
        cookies=cookies,
        headers=headers,
    )

    assert confirm.status_code == 200
    assert reject.status_code == 200
    assert source_delete.status_code == 204
    assert [kind for kind, _ in observed] == [
        "confirm",
        "source_delete",
        "reject",
        "reject_delete",
        "source_delete",
    ]
    for kind, context in observed:
        assert context is not None, kind
        assert context.user_id_token == user_token
        assert context.service_audience == "https://travel-ai.test"
        assert context.service_account == "travel-ai@project.iam.gserviceaccount.com"

    class RetryService:
        def __init__(self, _factory, client):
            observed.append(("retry_client", client._auth_context))

        async def retry_pending(self, _owner_id):
            return {"attempted": 0, "deleted": 0, "failed": 0, "pending": 0}

    class PersonalAIClientFixture:
        def __init__(self, *, timeout_seconds, auth_context):
            self._auth_context = auth_context
            assert timeout_seconds > 0

    monkeypatch.setattr(private_deletion_routes, "get_settings", lambda: settings)
    monkeypatch.setattr(private_deletion_routes, "PersonalAIClient", PersonalAIClientFixture)
    monkeypatch.setattr(private_deletion_routes, "PrivateDeletionService", RetryService)
    retry = api_client.post(
        "/v1/private-import-deletion-intents/retry", cookies=cookies, headers=headers
    )
    assert retry.status_code == 200
    assert observed[-1][0] == "retry_client"
    retry_context = observed[-1][1]
    assert retry_context is not None
    assert retry_context.user_id_token == user_token
