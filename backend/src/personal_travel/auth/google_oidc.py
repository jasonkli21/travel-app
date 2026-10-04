"""Google OIDC verification using google-auth's vetted signature verifier."""

from __future__ import annotations

import hashlib
import threading
import time
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from google.auth.exceptions import GoogleAuthError, TransportError
from google.auth.transport.requests import Request
from google.oauth2 import id_token

from personal_travel.auth.contracts import VerifiedPrincipal
from personal_travel.config import Settings

MAX_TOKEN_LENGTH = 8192
TOKEN_CACHE_SIZE = 256
TOKEN_CACHE_MARGIN_SECONDS = 60
GOOGLE_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}
GOOGLE_CERT_URL = "https://www.googleapis.com/oauth2/v1/certs"
MAX_CERT_CACHE_SECONDS = 3600
MAX_CERT_RESPONSE_BYTES = 64 * 1024


class InvalidIdentityToken(ValueError):
    """The supplied token is not a valid, correctly scoped Google ID token."""


class IdentityProviderUnavailable(RuntimeError):
    """Google verification keys could not be checked within the request bound."""


@dataclass(frozen=True, slots=True)
class _CachedResponse:
    status: int
    data: bytes
    headers: Mapping[str, str]


class _BoundedRequest(Request):
    """Bound Google key retrieval and cache its single fixed cert endpoint."""

    def __init__(self) -> None:
        super().__init__()
        self._response: _CachedResponse | None = None
        self._expires_at = 0.0
        self._cache_lock = threading.Lock()

    def __call__(
        self,
        url: str,
        method: str = "GET",
        body: bytes | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = 3,
        **kwargs: Any,
    ) -> Any:
        if method.upper() == "GET" and url == GOOGLE_CERT_URL and body is None:
            with self._cache_lock:
                if self._response is not None and time.monotonic() < self._expires_at:
                    return self._response
                response = super().__call__(  # type: ignore[no-untyped-call]
                    url,
                    method=method,
                    body=body,
                    headers=headers,
                    timeout=min(timeout or 3, 3),
                    **kwargs,
                )
                if len(response.data) > MAX_CERT_RESPONSE_BYTES:
                    raise OSError("Google signing-key response exceeded its byte limit.")
                cache_seconds = _cache_max_age(response.headers.get("cache-control", ""))
                self._response = _CachedResponse(
                    status=response.status,
                    data=response.data,
                    headers=dict(response.headers),
                )
                self._expires_at = time.monotonic() + cache_seconds
                return response
        return super().__call__(  # type: ignore[no-untyped-call]
            url,
            method=method,
            body=body,
            headers=headers,
            timeout=min(timeout or 3, 3),
            **kwargs,
        )


def _cache_max_age(value: str) -> int:
    for directive in value.split(","):
        name, separator, raw = directive.strip().partition("=")
        if separator and name.strip().lower() == "max-age":
            try:
                return max(0, min(int(raw.strip().strip('"')), MAX_CERT_CACHE_SECONDS))
            except ValueError:
                return 0
    return 0


_verification_lock = threading.Lock()
_token_cache_lock = threading.Lock()
_verified_tokens: OrderedDict[str, tuple[int, dict[str, Any]]] = OrderedDict()
_key_request = _BoundedRequest()


def _claims(token: str, audience: str) -> dict[str, Any]:
    fingerprint = hashlib.sha256(f"{audience}\0{token}".encode()).hexdigest()
    now = int(time.time())
    with _token_cache_lock:
        cached = _verified_tokens.get(fingerprint)
        if cached is not None and now < cached[0] - TOKEN_CACHE_MARGIN_SECONDS:
            _verified_tokens.move_to_end(fingerprint)
            return cached[1]
        _verified_tokens.pop(fingerprint, None)

    try:
        # google-auth verifies the signature, audience, issuer and expiry. One
        # shared request/cache is protected because Requests sessions are mutable.
        with _verification_lock:
            verified = id_token.verify_oauth2_token(  # type: ignore[no-untyped-call]
                token, _key_request, audience=audience
            )
    except TransportError as error:
        raise IdentityProviderUnavailable from error
    except (GoogleAuthError, ValueError, TypeError, KeyError) as error:
        raise InvalidIdentityToken from error
    except Exception as error:
        raise IdentityProviderUnavailable from error

    if not isinstance(verified, dict):
        raise InvalidIdentityToken
    expiry = verified.get("exp")
    if isinstance(expiry, bool) or not isinstance(expiry, (int, float)):
        raise InvalidIdentityToken
    with _token_cache_lock:
        _verified_tokens[fingerprint] = (int(expiry), verified)
        _verified_tokens.move_to_end(fingerprint)
        while len(_verified_tokens) > TOKEN_CACHE_SIZE:
            _verified_tokens.popitem(last=False)
    return verified


def principal_from_google_token(token: str, settings: Settings) -> VerifiedPrincipal:
    """Verify a Google ID token and derive the upstream-compatible stable owner."""
    if (
        settings.travel_auth_mode != "google_oidc"
        or not token
        or len(token) > MAX_TOKEN_LENGTH
        or any(character.isspace() for character in token)
    ):
        raise InvalidIdentityToken

    claims = _claims(token, settings.google_oauth_client_id)
    issuer = claims.get("iss")
    if issuer not in GOOGLE_ISSUERS or issuer != settings.google_oidc_issuer:
        raise InvalidIdentityToken
    subject = claims.get("sub")
    email = claims.get("email")
    verified_email = claims.get("email_verified")
    issued_at = claims.get("iat")
    expires_at = claims.get("exp")
    now = int(time.time())
    if (
        not isinstance(subject, str)
        or not subject
        or len(subject) > 255
        or not subject.isascii()
        or not isinstance(email, str)
        or email.strip().lower() != settings.google_oauth_allowed_email_normalized
        or verified_email is not True
        or isinstance(issued_at, bool)
        or not isinstance(issued_at, (int, float))
        or isinstance(expires_at, bool)
        or not isinstance(expires_at, (int, float))
        or issued_at > now + 60
        or expires_at <= now + TOKEN_CACHE_MARGIN_SECONDS
    ):
        raise InvalidIdentityToken

    canonical_issuer = "https://accounts.google.com"
    owner_hash = hashlib.sha256(f"{canonical_issuer}\0{subject}".encode()).hexdigest()
    return VerifiedPrincipal(
        issuer=canonical_issuer,
        subject=subject,
        owner_id=f"usr_{owner_hash[:32]}",
        email=email.strip().lower(),
        issued_at=int(issued_at),
        expires_at=int(expires_at),
    )
