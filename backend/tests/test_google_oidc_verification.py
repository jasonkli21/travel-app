from __future__ import annotations

import hashlib
import json
import time
from collections import OrderedDict
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from google.auth.crypt import RSASigner
from google.auth.exceptions import TransportError
from google.auth.jwt import encode
from pydantic import ValidationError

from personal_travel.auth.contracts import VerifiedPrincipal
from personal_travel.auth.google_oidc import (
    GOOGLE_CERT_URL,
    IdentityProviderUnavailable,
    InvalidIdentityToken,
    _BoundedRequest,
    cloud_run_service_id_token,
    principal_from_google_token,
)
from personal_travel.config import Settings


def oidc_settings(**updates: object) -> Settings:
    defaults: dict[str, object] = {
        "travel_auth_mode": "google_oidc",
        "google_oauth_client_id": "travel-client.apps.googleusercontent.com",
        "google_oauth_client_secret": "synthetic-secret",
        "google_oauth_redirect_uri": "https://travel.test/auth/google/callback",
        "google_oauth_allowed_email": "owner@gmail.com",
        "personal_ai_auth_mode": "none",
    }
    return Settings(**(defaults | updates))


def test_session_lifetime_is_capped_at_eight_hours() -> None:
    assert oidc_settings(auth_session_ttl_seconds=28800).auth_session_ttl_seconds == 28800
    with pytest.raises(ValidationError):
        oidc_settings(auth_session_ttl_seconds=28801)


def signing_fixture() -> tuple[RSASigner, str]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    signer = RSASigner.from_string(private_bytes)
    now = datetime.now(UTC)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "synthetic-google-key")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .sign(private_key, hashes.SHA256())
    )
    return signer, certificate.public_bytes(serialization.Encoding.PEM).decode("ascii")


def google_token(signer: RSASigner, **updates: object) -> str:
    now = int(time.time())
    claims: dict[str, object] = {
        "iss": "https://accounts.google.com",
        "sub": "synthetic-stable-google-subject",
        "aud": "travel-client.apps.googleusercontent.com",
        "email": "owner@gmail.com",
        "email_verified": True,
        "iat": now,
        "exp": now + 3600,
        "nonce": "synthetic-login-nonce",
    }
    claims.update(updates)
    return encode(signer, claims, key_id="synthetic-google-key").decode("ascii")


@pytest.fixture
def google_keys(monkeypatch: pytest.MonkeyPatch) -> tuple[RSASigner, list[str]]:
    signer, certificate = signing_fixture()
    requests: list[str] = []

    def get_keys(url: str, **kwargs: object) -> SimpleNamespace:
        requests.append(url)
        assert url == GOOGLE_CERT_URL
        return SimpleNamespace(
            status=200,
            data=json.dumps({"synthetic-google-key": certificate}).encode(),
            headers={"cache-control": "public, max-age=300"},
        )

    monkeypatch.setattr("personal_travel.auth.google_oidc._key_request", get_keys)
    monkeypatch.setattr("personal_travel.auth.google_oidc._verified_tokens", OrderedDict())
    return signer, requests


def test_google_oidc_verifies_signature_claims_and_upstream_owner_mapping(google_keys) -> None:
    signer, key_requests = google_keys
    token = google_token(signer)

    principal = principal_from_google_token(token, oidc_settings())

    assert isinstance(principal, VerifiedPrincipal)
    assert principal.issuer == "https://accounts.google.com"
    assert principal.subject == "synthetic-stable-google-subject"
    assert (
        principal.owner_id
        == "usr_"
        + hashlib.sha256(
            b"https://accounts.google.com\0synthetic-stable-google-subject"
        ).hexdigest()[:32]
    )
    assert principal.email == "owner@gmail.com"
    assert len(key_requests) == 1
    assert principal_from_google_token(token, oidc_settings()) == principal
    assert len(key_requests) == 1


@pytest.mark.parametrize(
    "claims",
    [
        {"iss": "https://attacker.example"},
        {"aud": "attacker-client"},
        {"exp": int(time.time()) - 30},
        {"iat": int(time.time()) + 120},
        {"email": "other@gmail.com"},
        {"email_verified": False},
        {"aud": ["travel-client.apps.googleusercontent.com", "attacker-client"]},
        {"azp": "attacker-client"},
        {"exp": float("nan")},
        {"exp": float("inf")},
        {"iat": float("nan")},
    ],
)
def test_google_oidc_rejects_forged_or_unapproved_claims(google_keys, claims) -> None:
    signer, _ = google_keys

    with pytest.raises(InvalidIdentityToken):
        principal_from_google_token(google_token(signer, **claims), oidc_settings())


def test_google_oidc_rejects_signature_forgery(google_keys) -> None:
    _trusted_signer, _ = google_keys
    forged_signer, _certificate = signing_fixture()
    forged = google_token(forged_signer)

    with pytest.raises(InvalidIdentityToken):
        principal_from_google_token(forged, oidc_settings())


def test_cloud_run_transport_token_is_verified_for_service_account_and_audience(
    google_keys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signer, _ = google_keys
    audience = "https://personal-ai.test"
    service_account = "travel-ai@project.iam.gserviceaccount.com"
    token = google_token(
        signer,
        aud=audience,
        email=service_account,
    )
    monkeypatch.setattr(
        "personal_travel.auth.google_oidc.id_token.fetch_id_token",
        lambda _request, target: token if target == audience else "unexpected",
    )

    assert cloud_run_service_id_token(audience, service_account) == token
    with pytest.raises(InvalidIdentityToken):
        cloud_run_service_id_token(audience, "other@project.iam.gserviceaccount.com")


def test_cloud_run_transport_token_rejects_wrong_audience(google_keys, monkeypatch) -> None:
    signer, _ = google_keys
    token = google_token(
        signer,
        aud="https://wrong-audience.test",
        email="travel-ai@project.iam.gserviceaccount.com",
    )
    monkeypatch.setattr(
        "personal_travel.auth.google_oidc.id_token.fetch_id_token",
        lambda _request, _audience: token,
    )

    with pytest.raises(InvalidIdentityToken):
        cloud_run_service_id_token(
            "https://personal-ai.test", "travel-ai@project.iam.gserviceaccount.com"
        )


def test_google_oidc_fails_closed_when_signing_keys_are_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(*_args: object, **_kwargs: object) -> None:
        raise TransportError("synthetic network failure")

    monkeypatch.setattr("personal_travel.auth.google_oidc._key_request", unavailable)
    monkeypatch.setattr("personal_travel.auth.google_oidc._verified_tokens", OrderedDict())
    signer, _ = signing_fixture()

    with pytest.raises(IdentityProviderUnavailable):
        principal_from_google_token(google_token(signer), oidc_settings())


def test_google_key_fetch_is_bounded_and_cache_is_single_entry() -> None:
    calls: list[float | None] = []

    class SyntheticResponse:
        status_code = 200
        headers = {"cache-control": "public, max-age=99999"}

        def __enter__(self) -> SyntheticResponse:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def iter_content(self, *, chunk_size: int):
            assert chunk_size == 8192
            yield b"{}"

    class SyntheticSession:
        def close(self) -> None:
            return None

        def request(self, method: str, url: str, **kwargs: object) -> SyntheticResponse:
            assert url == GOOGLE_CERT_URL
            assert method == "GET"
            assert kwargs["stream"] is True
            assert kwargs["allow_redirects"] is False
            calls.append(kwargs["timeout"])
            return SyntheticResponse()

    request = _BoundedRequest()
    request.session = SyntheticSession()  # type: ignore[assignment]

    request(GOOGLE_CERT_URL, timeout=30)
    request(GOOGLE_CERT_URL, timeout=30)

    assert calls == [3]


def test_google_key_response_is_capped_before_buffering() -> None:
    class OversizedResponse:
        status_code = 200
        headers = {"cache-control": "public, max-age=300"}

        def __enter__(self) -> OversizedResponse:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def iter_content(self, *, chunk_size: int):
            assert chunk_size == 8192
            yield b"x" * 8192
            yield b"x" * 8192
            yield b"x" * (64 * 1024)

    class SyntheticSession:
        def close(self) -> None:
            return None

        def request(self, *_args: object, **_kwargs: object) -> OversizedResponse:
            return OversizedResponse()

    request = _BoundedRequest()
    request.session = SyntheticSession()  # type: ignore[assignment]
    with pytest.raises(TransportError):
        request(GOOGLE_CERT_URL)


@pytest.mark.parametrize(
    "settings",
    [
        {
            "personal_ai_auth_mode": "google_cloud_run_iam",
            "personal_ai_user_id_token_audience": "other",
            "personal_ai_service_iam_audience": "https://travel-ai.test",
            "personal_ai_service_account": "travel-ai@project.iam.gserviceaccount.com",
        },
        {"google_oauth_allowed_email": ""},
    ],
)
def test_identity_configuration_fails_closed_on_incomplete_ai_or_allowlist(settings) -> None:
    with pytest.raises(ValueError):
        oidc_settings(**settings)
