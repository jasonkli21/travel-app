from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from personal_travel.db.base import Base
from personal_travel.models.common import TimestampMixin, UUIDPrimaryKeyMixin


class AuthIdentity(TimestampMixin, Base):
    __tablename__ = "auth_identities"

    owner_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    issuer: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    migration_version: Mapped[str] = mapped_column(
        String(64), nullable=False, default="phase6-google-oidc-v1"
    )

    __table_args__ = (
        UniqueConstraint("issuer", "subject", name="uq_auth_identities_issuer_subject"),
        CheckConstraint("status IN ('active','disabled')", name="valid_status"),
    )


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("auth_identities.owner_id", ondelete="CASCADE"), nullable=False
    )
    csrf_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("issued_at < expires_at", name="valid_lifetime"),
        Index("ix_auth_sessions_owner_expiry", "owner_id", "expires_at"),
    )


class OAuthLoginAttempt(Base):
    __tablename__ = "oauth_login_attempts"

    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    browser_secret_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    nonce: Mapped[str] = mapped_column(String(128), nullable=False)
    code_verifier: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("created_at < expires_at", name="valid_lifetime"),
        Index("ix_oauth_login_attempts_expiry", "expires_at"),
    )


class OwnerMigrationAudit(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "owner_migration_audits"

    source_owner_id: Mapped[str] = mapped_column(String(128), nullable=False)
    target_owner_id: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_owner_id: Mapped[str] = mapped_column(String(128), nullable=False)
    plan_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    backup_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    row_counts: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("source_owner_id <> target_owner_id", name="different_owners"),
        Index("ix_owner_migration_audits_target_time", "target_owner_id", "occurred_at"),
    )
