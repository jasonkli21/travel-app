"""Opaque, database-backed browser sessions and CSRF proofs."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from personal_travel.auth.contracts import VerifiedPrincipal
from personal_travel.auth.google_oidc import stable_google_owner_id
from personal_travel.models import AuthIdentity, AuthSession


@dataclass(frozen=True, slots=True)
class ActiveSession:
    principal: VerifiedPrincipal
    token_hash: str
    csrf_token_hash: str
    expires_at: datetime


def secret_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def matches_digest(value: str, expected_digest: str) -> bool:
    return hmac.compare_digest(secret_digest(value), expected_digest)


def persist_verified_identity(session: Session, principal: VerifiedPrincipal) -> AuthIdentity:
    if principal.owner_id != stable_google_owner_id(principal.issuer, principal.subject):
        raise ValueError("The verified identity does not match the stable Google owner ID.")
    identity = session.get(AuthIdentity, principal.owner_id)
    if identity is None:
        identity = AuthIdentity(
            owner_id=principal.owner_id,
            issuer=principal.issuer,
            subject=principal.subject,
            email=principal.email,
            status="active",
        )
        session.add(identity)
        session.flush()
        return identity
    if identity.issuer != principal.issuer or identity.subject != principal.subject:
        raise ValueError("The verified identity mapping conflicts with its stable owner ID.")
    if identity.status != "active":
        raise ValueError("This verified identity is disabled.")
    if identity.email != principal.email:
        identity.email = principal.email
        session.flush()
    return identity


def create_session(
    session: Session,
    principal: VerifiedPrincipal,
    *,
    ttl_seconds: int,
    now: datetime | None = None,
) -> tuple[str, str, ActiveSession]:
    current = now or datetime.now(UTC)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("Session time must include a timezone.")
    persist_verified_identity(session, principal)
    raw_token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    token_hash = secret_digest(raw_token)
    csrf_token_hash = secret_digest(csrf_token)
    expires_at = current + timedelta(seconds=ttl_seconds)
    session.add(
        AuthSession(
            token_hash=token_hash,
            owner_id=principal.owner_id,
            csrf_token_hash=csrf_token_hash,
            issued_at=current,
            expires_at=expires_at,
        )
    )
    session.flush()
    active = ActiveSession(
        principal=principal,
        token_hash=token_hash,
        csrf_token_hash=csrf_token_hash,
        expires_at=expires_at,
    )
    return raw_token, csrf_token, active


def load_active_session(
    session: Session, raw_token: str, *, now: datetime | None = None
) -> ActiveSession | None:
    if not raw_token or len(raw_token) > 128 or any(char.isspace() for char in raw_token):
        return None
    current = now or datetime.now(UTC)
    token_hash = secret_digest(raw_token)
    result = session.execute(
        select(AuthSession, AuthIdentity)
        .join(AuthIdentity, AuthIdentity.owner_id == AuthSession.owner_id)
        .where(AuthSession.token_hash == token_hash)
    ).one_or_none()
    if result is None:
        return None
    browser_session, identity = result
    try:
        valid_owner_mapping = identity.owner_id == stable_google_owner_id(
            identity.issuer, identity.subject
        )
    except ValueError:
        valid_owner_mapping = False
    if (
        browser_session.revoked_at is not None
        or browser_session.expires_at <= current
        or identity.status != "active"
        or not valid_owner_mapping
    ):
        return None
    principal = VerifiedPrincipal(
        issuer=identity.issuer,
        subject=identity.subject,
        owner_id=identity.owner_id,
        email=identity.email,
        issued_at=int(browser_session.issued_at.timestamp()),
        expires_at=int(browser_session.expires_at.timestamp()),
    )
    return ActiveSession(
        principal=principal,
        token_hash=token_hash,
        csrf_token_hash=browser_session.csrf_token_hash,
        expires_at=browser_session.expires_at,
    )


def revoke_session(session: Session, token_hash: str, *, now: datetime | None = None) -> None:
    session.execute(
        update(AuthSession)
        .where(AuthSession.token_hash == token_hash, AuthSession.revoked_at.is_(None))
        .values(revoked_at=now or datetime.now(UTC))
    )
