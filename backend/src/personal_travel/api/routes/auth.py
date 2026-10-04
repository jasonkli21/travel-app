"""Server-owned Google sign-in flow and session lifecycle."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
import time
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from urllib.parse import urlencode

import httpx
from anyio.to_thread import run_sync
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from personal_travel.api.dependencies import SessionDependency
from personal_travel.auth.google_oidc import (
    IdentityProviderUnavailable,
    InvalidIdentityToken,
    principal_from_google_token,
)
from personal_travel.auth.sessions import (
    ActiveSession,
    create_session,
    matches_digest,
    revoke_session,
    secret_digest,
)
from personal_travel.config import Settings, get_settings
from personal_travel.models import OAuthLoginAttempt
from personal_travel.services.errors import DomainError

router = APIRouter(prefix="/auth", tags=["authentication"])
GOOGLE_AUTHORIZATION_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
LOGIN_ATTEMPT_TTL_SECONDS = 600
MAX_TOKEN_RESPONSE_BYTES = 16 * 1024
OAUTH_EXCHANGE_DEADLINE_SECONDS = 5.0
CALLBACK_DEADLINE_SECONDS = 7.5  # Next's callback proxy gives the API eight seconds.


class OAuthCallbackRequest(BaseModel):
    code: str = Field(min_length=1, max_length=2048)
    state: str = Field(min_length=20, max_length=128)


class AuthSessionResponse(BaseModel):
    mode: str
    authenticated: bool
    email: str | None = None
    expires_at: datetime | None = None


def _b64url_random(size: int = 32) -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(size)).rstrip(b"=").decode("ascii")


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _require_google_mode(settings: Settings) -> None:
    if settings.travel_auth_mode != "google_oidc":
        raise DomainError(
            "google_sign_in_unavailable",
            "Google sign-in is not configured for this travel service.",
            status_code=503,
        )


@router.get("/google/start")
def start_google_sign_in(
    response: Response,
    session: SessionDependency,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, str]:
    _require_google_mode(settings)
    state = _b64url_random()
    browser_secret = _b64url_random()
    nonce = _b64url_random()
    code_verifier = _b64url_random(48)
    now = datetime.now(UTC)
    attempt = OAuthLoginAttempt(
        state_hash=secret_digest(state),
        browser_secret_hash=secret_digest(browser_secret),
        nonce=nonce,
        code_verifier=code_verifier,
        created_at=now,
        expires_at=now + timedelta(seconds=LOGIN_ATTEMPT_TTL_SECONDS),
    )
    with session.begin():
        session.execute(delete(OAuthLoginAttempt).where(OAuthLoginAttempt.expires_at <= now))
        session.add(attempt)

    redirect_uri = str(settings.google_oauth_redirect_uri)
    query = urlencode(
        {
            "client_id": settings.google_oauth_client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid email",
            "state": state,
            "nonce": nonce,
            "code_challenge": _pkce_challenge(code_verifier),
            "code_challenge_method": "S256",
        }
    )
    response.set_cookie(
        settings.auth_oauth_flow_cookie_name,
        browser_secret,
        max_age=LOGIN_ATTEMPT_TTL_SECONDS,
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )
    return {"authorization_url": f"{GOOGLE_AUTHORIZATION_URL}?{query}"}


def _consume_login_attempt(
    session: Session, *, state: str, browser_secret: str | None, deadline: float
) -> OAuthLoginAttempt:
    if not browser_secret or len(browser_secret) > 128:
        raise DomainError(
            "invalid_login_flow",
            "The sign-in request could not be verified.",
            status_code=401,
        )
    state_hash = secret_digest(state)
    with session.begin():
        attempt = session.scalar(
            select(OAuthLoginAttempt)
            .where(OAuthLoginAttempt.state_hash == state_hash)
            .with_for_update()
        )
        if (
            attempt is None
            or attempt.expires_at <= datetime.now(UTC)
            or not matches_digest(browser_secret, attempt.browser_secret_hash)
        ):
            raise DomainError(
                "invalid_login_flow",
                "The sign-in request could not be verified.",
                status_code=401,
            )
        values = OAuthLoginAttempt(
            state_hash=attempt.state_hash,
            browser_secret_hash=attempt.browser_secret_hash,
            nonce=attempt.nonce,
            code_verifier=attempt.code_verifier,
            created_at=attempt.created_at,
            expires_at=attempt.expires_at,
        )
        session.delete(attempt)
        _require_callback_time(deadline)
    return values


def _require_callback_time(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise TimeoutError("Authentication callback exceeded its deadline.")


async def _exchange_code(code: str, code_verifier: str, settings: Settings) -> str:
    secret = settings.google_oauth_client_secret
    assert secret is not None
    form = {
        "client_id": settings.google_oauth_client_id,
        "client_secret": secret.get_secret_value(),
        "code": code,
        "code_verifier": code_verifier,
        "grant_type": "authorization_code",
        "redirect_uri": str(settings.google_oauth_redirect_uri),
    }
    try:
        async with asyncio.timeout(OAUTH_EXCHANGE_DEADLINE_SECONDS):
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(OAUTH_EXCHANGE_DEADLINE_SECONDS), follow_redirects=False
            ) as client:
                async with client.stream("POST", GOOGLE_TOKEN_URL, data=form) as result:
                    if result.status_code >= 500:
                        raise IdentityProviderUnavailable
                    if result.status_code != 200:
                        raise InvalidIdentityToken
                    body = bytearray()
                    async for chunk in result.aiter_bytes(chunk_size=4096):
                        body.extend(chunk)
                        if len(body) > MAX_TOKEN_RESPONSE_BYTES:
                            raise IdentityProviderUnavailable
    except (httpx.HTTPError, TimeoutError) as error:
        raise IdentityProviderUnavailable from error
    if len(body) > MAX_TOKEN_RESPONSE_BYTES:
        raise IdentityProviderUnavailable
    try:
        payload: Any = json.loads(body)
    except (ValueError, TypeError):
        raise IdentityProviderUnavailable from None
    token = payload.get("id_token") if isinstance(payload, dict) else None
    if not isinstance(token, str) or not token or len(token) > 8192:
        raise IdentityProviderUnavailable
    return token


@router.post("/google/callback")
async def google_callback(
    payload: OAuthCallbackRequest,
    request: Request,
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, bool]:
    _require_google_mode(settings)
    deadline = time.monotonic() + CALLBACK_DEADLINE_SECONDS
    session_factory = request.app.state.auth_session_factory

    def consume_attempt() -> OAuthLoginAttempt:
        with session_factory() as session:
            return _consume_login_attempt(
                session,
                state=payload.state,
                browser_secret=request.cookies.get(settings.auth_oauth_flow_cookie_name),
                deadline=deadline,
            )

    try:
        async with asyncio.timeout_at(deadline):
            attempt = await run_sync(consume_attempt, abandon_on_cancel=True)
    except TimeoutError:
        raise DomainError(
            "authentication_unavailable",
            "Authentication storage is temporarily unavailable.",
            status_code=503,
        ) from None
    try:
        token = await _exchange_code(payload.code, attempt.code_verifier, settings)
        async with asyncio.timeout_at(deadline):
            principal = await run_sync(
                principal_from_google_token, token, settings, abandon_on_cancel=True
            )
    except IdentityProviderUnavailable:
        raise DomainError(
            "identity_provider_unavailable",
            "Google sign-in is temporarily unavailable. Start again to retry.",
            status_code=503,
        ) from None
    except InvalidIdentityToken:
        raise DomainError(
            "invalid_identity_token",
            "Google could not verify the sign-in identity.",
            status_code=401,
        ) from None
    except TimeoutError:
        raise DomainError(
            "identity_provider_unavailable",
            "Google sign-in is temporarily unavailable. Start again to retry.",
            status_code=503,
        ) from None
    if principal.nonce is None or not secrets.compare_digest(principal.nonce, attempt.nonce):
        raise DomainError(
            "invalid_identity_nonce",
            "The sign-in request could not be verified.",
            status_code=401,
        )

    now = datetime.now(UTC)
    session_ttl_seconds = settings.auth_session_ttl_seconds
    if settings.personal_ai_auth_mode == "google_cloud_run_iam":
        remaining_user_token_ttl = principal.expires_at - int(now.timestamp()) - 60
        session_ttl_seconds = min(session_ttl_seconds, max(1, remaining_user_token_ttl))

    def save_session() -> tuple[str, str, ActiveSession]:
        with session_factory() as session, session.begin():
            _require_callback_time(deadline)
            previous_session: ActiveSession | None = request.scope.get("auth_session")
            if previous_session is not None:
                revoke_session(session, previous_session.token_hash, now=now)
            result = create_session(session, principal, ttl_seconds=session_ttl_seconds, now=now)
            _require_callback_time(deadline)
            return result

    try:
        async with asyncio.timeout_at(deadline):
            raw_session, csrf_token, active = await run_sync(save_session, abandon_on_cancel=True)
    except TimeoutError:
        raise DomainError(
            "authentication_unavailable",
            "Authentication storage is temporarily unavailable.",
            status_code=503,
        ) from None
    max_age = max(1, int((active.expires_at - now).total_seconds()))
    response.set_cookie(
        settings.auth_session_cookie_name,
        raw_session,
        max_age=max_age,
        expires=active.expires_at,
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )
    response.set_cookie(
        settings.auth_csrf_cookie_name,
        csrf_token,
        max_age=max_age,
        expires=active.expires_at,
        path="/",
        secure=True,
        httponly=False,
        samesite="strict",
    )
    if settings.personal_ai_auth_mode == "google_cloud_run_iam":
        response.set_cookie(
            settings.auth_ai_token_cookie_name,
            token,
            max_age=max_age,
            expires=active.expires_at,
            path="/",
            secure=True,
            httponly=True,
            samesite="lax",
        )
    else:
        response.delete_cookie(
            settings.auth_ai_token_cookie_name,
            path="/",
            secure=True,
            httponly=True,
            samesite="lax",
        )
    response.delete_cookie(
        settings.auth_oauth_flow_cookie_name,
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )
    return {"authenticated": True}


@router.get("/session", response_model=AuthSessionResponse)
def current_session(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> AuthSessionResponse:
    if settings.travel_auth_mode == "local":
        return AuthSessionResponse(mode="local", authenticated=True)
    principal = request.scope.get("principal")
    if principal is None:
        return AuthSessionResponse(mode="google_oidc", authenticated=False)
    expires_at = datetime.fromtimestamp(principal.expires_at, tz=UTC)
    return AuthSessionResponse(
        mode="google_oidc",
        authenticated=True,
        email=principal.email,
        expires_at=expires_at,
    )


@router.post("/logout", status_code=204)
def logout(
    request: Request,
    response: Response,
    session: SessionDependency,
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    active: ActiveSession | None = request.scope.get("auth_session")
    if active is not None:
        with session.begin():
            revoke_session(session, active.token_hash)
    for cookie_name in (
        settings.auth_session_cookie_name,
        settings.auth_csrf_cookie_name,
        settings.auth_ai_token_cookie_name,
        settings.auth_oauth_flow_cookie_name,
    ):
        response.delete_cookie(
            cookie_name,
            path="/",
            secure=True,
            httponly=cookie_name != settings.auth_csrf_cookie_name,
            samesite="lax",
        )
