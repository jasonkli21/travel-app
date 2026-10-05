"""Disposable mounted-browser fixture; never import from application startup.

Run only with SYNTHETIC_IDENTITY_FIXTURE=1 and a disposable TEST_DATABASE_URL.
The real OAuth state/nonce, signed-token verifier, session, CSRF and CRUD routes
remain active; only Google's external code exchange and signing-key endpoint
are replaced with synthetic responses in this fixture process.

The optional SYNTHETIC_BOOKING_IMPORT_FIXTURE=1 mode accepts only explicit local
configuration and adds the upstream synthetic-fixture marker to extraction POSTs.
Pair it with the upstream test/development auth mode and its fake LLM adapter.
It exists only to exercise the complete mounted travel lifecycle; production
requests never set that marker.
"""

import asyncio
import json
import os
import time
from datetime import UTC, datetime, timedelta

import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from google.auth.crypt import RSASigner
from google.auth.jwt import encode
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

if os.environ.get("SYNTHETIC_IDENTITY_FIXTURE") != "1":
    raise SystemExit("Synthetic identity fixture must be explicitly enabled.")
database_url = os.environ.get("TEST_DATABASE_URL", "")
if "test" not in database_url or "127.0.0.1" not in database_url:
    raise SystemExit("A disposable local TEST_DATABASE_URL is required.")
os.environ["DATABASE_URL"] = database_url
synthetic_booking_import = os.environ.get("SYNTHETIC_BOOKING_IMPORT_FIXTURE") == "1"
if synthetic_booking_import and os.environ.get("APP_ENVIRONMENT") not in {"local", "test"}:
    raise SystemExit("Synthetic booking imports are restricted to local/test environments.")

import personal_travel.api.routes.auth as auth_routes  # noqa: E402
import personal_travel.auth.google_oidc as oidc  # noqa: E402
from personal_travel.api.dependencies import session_dependency  # noqa: E402
from personal_travel.config import get_settings  # noqa: E402
from personal_travel.main import app  # noqa: E402

if synthetic_booking_import:
    synthetic_settings = get_settings()
    if (
        synthetic_settings.travel_auth_mode != "google_oidc"
        or not synthetic_settings.private_imports_enabled
        or synthetic_settings.personal_ai_auth_mode != "none"
        or synthetic_settings.personal_ai_base_url.host not in {"127.0.0.1", "localhost"}
    ):
        raise SystemExit("Synthetic booking mode requires local private imports and loopback AI.")
    # The normal settings validator correctly rejects unauthenticated AI in
    # Google mode. This isolated fixture enables only its local fake upstream.
    object.__setattr__(synthetic_settings, "personal_ai_extractions_enabled", True)

    from personal_travel.clients.personal_ai import PersonalAIClient  # noqa: E402

    original_extraction_request = PersonalAIClient._extraction_request

    async def synthetic_extraction_request(self, client, method, path, payload, **kwargs):
        if method == "POST" and path == "/v1/travel/booking-extractions":
            if payload is None:
                raise RuntimeError("Synthetic extraction payload is missing.")
            payload = payload | {"synthetic_fixture": True}
        return await original_extraction_request(self, client, method, path, payload, **kwargs)

    PersonalAIClient._extraction_request = synthetic_extraction_request

test_schema = os.environ.get("SYNTHETIC_TEST_SCHEMA")
if test_schema is not None:
    if not test_schema.startswith("travel_test_") or not test_schema.replace("_", "").isalnum():
        raise SystemExit("Synthetic test schema is invalid.")
    test_engine = create_engine(
        database_url, connect_args={"options": f"-c search_path={test_schema}"}
    )
    app.state.auth_session_factory = sessionmaker(
        bind=test_engine, autoflush=False, expire_on_commit=False
    )

    def test_session():
        with app.state.auth_session_factory() as session:
            yield session

    app.dependency_overrides[session_dependency] = test_session

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
    .public_bytes(serialization.Encoding.PEM)
    .decode("ascii")
)
nonce_by_verifier: dict[str, str] = {}
original_consume = auth_routes._consume_login_attempt


def consume(*args, **kwargs):
    attempt = original_consume(*args, **kwargs)
    nonce_by_verifier[attempt.code_verifier] = attempt.nonce
    return attempt


async def exchange(code: str, verifier: str, settings):
    if code not in {"synthetic-code", "synthetic-slow-code"}:
        raise oidc.InvalidIdentityToken
    if code == "synthetic-slow-code":
        await asyncio.sleep(float(os.environ.get("SYNTHETIC_SLOW_EXCHANGE_SECONDS", "2")))
    nonce = nonce_by_verifier.pop(verifier)
    current = int(time.time())
    claims = {
        "iss": "https://accounts.google.com",
        "sub": "synthetic-mounted-owner",
        "aud": settings.google_oauth_client_id,
        "email": settings.google_oauth_allowed_email_normalized,
        "email_verified": True,
        "hd": settings.google_oauth_allowed_hosted_domain.strip().lower(),
        "iat": current,
        "exp": current + 3600,
        "nonce": nonce,
    }
    return encode(signer, claims, key_id="synthetic-google-key").decode("ascii")


def synthetic_keys(url: str, **_kwargs):
    if url != oidc.GOOGLE_CERT_URL:
        raise oidc.IdentityProviderUnavailable
    return type(
        "Response",
        (),
        {
            "status": 200,
            "data": json.dumps({"synthetic-google-key": certificate}).encode(),
            "headers": {"cache-control": "public, max-age=60"},
        },
    )()


auth_routes._consume_login_attempt = consume
auth_routes._exchange_code = exchange
oidc._key_request = synthetic_keys
auth_routes.CALLBACK_DEADLINE_SECONDS = float(
    os.environ.get("SYNTHETIC_CALLBACK_DEADLINE_SECONDS", "7.5")
)

if __name__ == "__main__":
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=int(os.environ.get("SYNTHETIC_API_PORT", "8000")),
        loop=os.environ.get("SYNTHETIC_UVICORN_LOOP", "auto"),
        access_log=False,
    )
