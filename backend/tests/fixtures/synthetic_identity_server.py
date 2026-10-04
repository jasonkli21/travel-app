"""Disposable mounted-browser fixture; never import from application startup.

Run only with SYNTHETIC_IDENTITY_FIXTURE=1 and a disposable TEST_DATABASE_URL.
The real OAuth state/nonce, signed-token verifier, session, CSRF and CRUD routes
remain active; only Google's external code exchange and signing-key endpoint
are replaced with synthetic responses in this fixture process.
"""

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

if os.environ.get("SYNTHETIC_IDENTITY_FIXTURE") != "1":
    raise SystemExit("Synthetic identity fixture must be explicitly enabled.")
database_url = os.environ.get("TEST_DATABASE_URL", "")
if "test" not in database_url or "127.0.0.1" not in database_url:
    raise SystemExit("A disposable local TEST_DATABASE_URL is required.")
os.environ["DATABASE_URL"] = database_url

import personal_travel.api.routes.auth as auth_routes  # noqa: E402
import personal_travel.auth.google_oidc as oidc  # noqa: E402
from personal_travel.main import app  # noqa: E402

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
    if code != "synthetic-code":
        raise oidc.InvalidIdentityToken
    nonce = nonce_by_verifier.pop(verifier)
    current = int(time.time())
    claims = {
        "iss": "https://accounts.google.com",
        "sub": "synthetic-mounted-owner",
        "aud": settings.google_oauth_client_id,
        "email": settings.google_oauth_allowed_email_normalized,
        "email_verified": True,
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

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000, access_log=False)
