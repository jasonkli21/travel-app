"""Google OIDC verification using google-auth's vetted signature verifier."""

from __future__ import annotations

import hashlib
import math
import threading
import time
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, TypeGuard
from urllib.parse import urlsplit

import requests
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
CANONICAL_GOOGLE_ISSUER = "https://accounts.google.com"
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
            budget = min(timeout or 3, 3)
            deadline = time.monotonic() + budget
            if not self._cache_lock.acquire(timeout=budget):
                raise TransportError("Google key cache is busy.")  # type: ignore[no-untyped-call]
            try:
                if self._response is not None and time.monotonic() < self._expires_at:
                    return self._response
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TransportError("Google key fetch exceeded its deadline.")  # type: ignore[no-untyped-call]
                response = self._request_bounded(
                    url=url,
                    method=method,
                    body=body,
                    headers=headers,
                    timeout=remaining,
                    max_bytes=MAX_CERT_RESPONSE_BYTES,
                )
                cache_seconds = _cache_max_age(response.headers.get("cache-control", ""))
                self._response = _CachedResponse(
                    status=response.status,
                    data=response.data,
                    headers=dict(response.headers),
                )
                self._expires_at = time.monotonic() + cache_seconds
                return response
            finally:
                self._cache_lock.release()
        parsed = urlsplit(url)
        if (
            method.upper() == "GET"
            and body is None
            and parsed.scheme == "http"
            and parsed.hostname == "metadata.google.internal"
            and parsed.path.startswith("/computeMetadata/v1/")
        ):
            return self._request_bounded(
                url=url,
                method=method,
                body=body,
                headers=headers,
                timeout=min(timeout or 3, 3),
                max_bytes=MAX_CERT_RESPONSE_BYTES,
            )
        raise TransportError(  # type: ignore[no-untyped-call]
            "Google identity transport rejected an unexpected endpoint."
        )

    def _request_bounded(
        self,
        *,
        url: str,
        method: str,
        body: bytes | None,
        headers: Mapping[str, str] | None,
        timeout: float,
        max_bytes: int,
    ) -> _CachedResponse:
        deadline = time.monotonic() + timeout
        try:
            with self.session.request(
                method,
                url,
                data=body,
                headers=headers,
                timeout=timeout,
                stream=True,
                allow_redirects=False,
            ) as response:
                payload = bytearray()
                for chunk in response.iter_content(chunk_size=8192):
                    if time.monotonic() >= deadline:
                        raise OSError("Google identity request exceeded its deadline.")
                    payload.extend(chunk)
                    if len(payload) > max_bytes:
                        raise OSError("Google identity response exceeded its byte limit.")
                return _CachedResponse(
                    status=response.status_code,
                    data=bytes(payload),
                    headers=dict(response.headers),
                )
        except (requests.RequestException, OSError) as error:
            raise TransportError(error) from error  # type: ignore[no-untyped-call]


def _cache_max_age(value: str) -> int:
    for directive in value.split(","):
        name, separator, raw = directive.strip().partition("=")
        if separator and name.strip().lower() == "max-age":
            try:
                return max(0, min(int(raw.strip().strip('"')), MAX_CERT_CACHE_SECONDS))
            except ValueError:
                return 0
    return 0


def _is_finite_number(value: object) -> TypeGuard[int | float]:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and (isinstance(value, int) or math.isfinite(value))
    )


def stable_google_owner_id(issuer: str, subject: str) -> str:
    if issuer not in GOOGLE_ISSUERS or not subject:
        raise ValueError("A verified Google issuer and subject are required.")
    digest = hashlib.sha256(f"{CANONICAL_GOOGLE_ISSUER}\0{subject}".encode()).hexdigest()
    return f"usr_{digest[:32]}"


def authoritative_allowed_email(claims: Mapping[str, Any], settings: Settings) -> bool:
    email = claims.get("email")
    if not isinstance(email, str) or claims.get("email_verified") is not True:
        return False
    normalized = email.strip().lower()
    if normalized != settings.google_oauth_allowed_email_normalized:
        return False
    domain = normalized.rsplit("@", 1)[-1]
    if domain in {"gmail.com", "googlemail.com"}:
        return True
    hosted_domain = settings.google_oauth_allowed_hosted_domain.strip().lower()
    return bool(hosted_domain and domain == hosted_domain and claims.get("hd") == hosted_domain)


_verification_lock = threading.Lock()
_token_cache_lock = threading.Lock()
_verified_tokens: OrderedDict[str, tuple[int, dict[str, Any]]] = OrderedDict()
_key_request = _BoundedRequest()


def _claims(token: str, audience: str) -> dict[str, Any]:
    deadline = time.monotonic() + 3
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
        if not _verification_lock.acquire(timeout=max(0, deadline - time.monotonic())):
            raise IdentityProviderUnavailable
        try:
            if time.monotonic() >= deadline:
                raise IdentityProviderUnavailable
            verified = id_token.verify_oauth2_token(  # type: ignore[no-untyped-call]
                token, _key_request, audience=audience
            )
            if time.monotonic() >= deadline:
                raise IdentityProviderUnavailable
        finally:
            _verification_lock.release()
    except TransportError as error:
        raise IdentityProviderUnavailable from error
    except (GoogleAuthError, ValueError, TypeError, KeyError) as error:
        raise InvalidIdentityToken from error
    except Exception as error:
        raise IdentityProviderUnavailable from error

    if not isinstance(verified, dict):
        raise InvalidIdentityToken
    expiry = verified.get("exp")
    if not _is_finite_number(expiry):
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
    token_audience = claims.get("aud")
    authorized_party = claims.get("azp")
    issued_at = claims.get("iat")
    expires_at = claims.get("exp")
    nonce = claims.get("nonce")
    now = int(time.time())
    if (
        not isinstance(subject, str)
        or not subject
        or len(subject) > 255
        or not subject.isascii()
        or token_audience
        not in (settings.google_oauth_client_id, [settings.google_oauth_client_id])
        or (authorized_party is not None and authorized_party != settings.google_oauth_client_id)
        or not authoritative_allowed_email(claims, settings)
        or not _is_finite_number(issued_at)
        or not _is_finite_number(expires_at)
        or issued_at > now + 60
        or expires_at <= issued_at
        or expires_at <= now + TOKEN_CACHE_MARGIN_SECONDS
    ):
        raise InvalidIdentityToken

    return VerifiedPrincipal(
        issuer=CANONICAL_GOOGLE_ISSUER,
        subject=subject,
        owner_id=stable_google_owner_id(issuer, subject),
        email=settings.google_oauth_allowed_email_normalized,
        issued_at=int(issued_at),
        expires_at=int(expires_at),
        nonce=nonce if isinstance(nonce, str) else None,
    )


def cloud_run_service_id_token(audience: str, expected_service_account: str) -> str:
    """Fetch and independently verify the configured Cloud Run transport identity."""
    if not audience.startswith("https://") or not expected_service_account.strip():
        raise IdentityProviderUnavailable
    try:
        token = id_token.fetch_id_token(_key_request, audience)  # type: ignore[no-untyped-call]
        if not isinstance(token, str) or not token or len(token) > MAX_TOKEN_LENGTH:
            raise InvalidIdentityToken
        claims = _claims(token, audience)
    except IdentityProviderUnavailable:
        raise
    except InvalidIdentityToken:
        raise
    except Exception as error:
        raise IdentityProviderUnavailable from error
    issuer = claims.get("iss")
    token_audience = claims.get("aud")
    authorized_party = claims.get("azp")
    subject = claims.get("sub")
    email = claims.get("email")
    email_verified = claims.get("email_verified")
    issued_at = claims.get("iat")
    expires_at = claims.get("exp")
    now = int(time.time())
    if (
        issuer not in GOOGLE_ISSUERS
        or token_audience not in (audience, [audience])
        or not isinstance(email, str)
        or email.strip().lower() != expected_service_account.strip().lower()
        or email_verified is not True
        or not _is_finite_number(issued_at)
        or issued_at > now + 60
        or not _is_finite_number(expires_at)
        or expires_at <= issued_at
        or expires_at <= now + TOKEN_CACHE_MARGIN_SECONDS
        or not isinstance(subject, str)
        or not subject.isdecimal()
        or authorized_party != subject
    ):
        raise InvalidIdentityToken
    return token
