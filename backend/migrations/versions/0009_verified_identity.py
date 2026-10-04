"""Add verified identities, opaque sessions, OAuth attempts, and migration audit.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "auth_identities",
        sa.Column("owner_id", sa.String(length=128), nullable=False),
        sa.Column("issuer", sa.String(length=255), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("migration_version", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('active','disabled')", name="ck_auth_identities_valid_status"
        ),
        sa.PrimaryKeyConstraint("owner_id", name="pk_auth_identities"),
        sa.UniqueConstraint("issuer", "subject", name="uq_auth_identities_issuer_subject"),
    )

    op.create_table(
        "auth_sessions",
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("owner_id", sa.String(length=128), nullable=False),
        sa.Column("csrf_token_hash", sa.String(length=64), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("issued_at < expires_at", name="ck_auth_sessions_valid_lifetime"),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["auth_identities.owner_id"],
            ondelete="CASCADE",
            name="fk_auth_sessions_owner_id_auth_identities",
        ),
        sa.PrimaryKeyConstraint("token_hash", name="pk_auth_sessions"),
    )
    op.create_index("ix_auth_sessions_owner_expiry", "auth_sessions", ["owner_id", "expires_at"])

    op.create_table(
        "oauth_login_attempts",
        sa.Column("state_hash", sa.String(length=64), nullable=False),
        sa.Column("browser_secret_hash", sa.String(length=64), nullable=False),
        sa.Column("nonce", sa.String(length=128), nullable=False),
        sa.Column("code_verifier", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "created_at < expires_at", name="ck_oauth_login_attempts_valid_lifetime"
        ),
        sa.PrimaryKeyConstraint("state_hash", name="pk_oauth_login_attempts"),
    )
    op.create_index("ix_oauth_login_attempts_expiry", "oauth_login_attempts", ["expires_at"])

    op.create_table(
        "owner_migration_audits",
        sa.Column("source_owner_id", sa.String(length=128), nullable=False),
        sa.Column("target_owner_id", sa.String(length=128), nullable=False),
        sa.Column("actor_owner_id", sa.String(length=128), nullable=False),
        sa.Column("plan_digest", sa.String(length=64), nullable=False),
        sa.Column("backup_sha256", sa.String(length=64), nullable=False),
        sa.Column("row_counts", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.CheckConstraint(
            "source_owner_id <> target_owner_id", name="ck_owner_migration_audits_different_owners"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_owner_migration_audits"),
    )
    op.create_index(
        "ix_owner_migration_audits_target_time",
        "owner_migration_audits",
        ["target_owner_id", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_owner_migration_audits_target_time", table_name="owner_migration_audits")
    op.drop_table("owner_migration_audits")
    op.drop_index("ix_oauth_login_attempts_expiry", table_name="oauth_login_attempts")
    op.drop_table("oauth_login_attempts")
    op.drop_index("ix_auth_sessions_owner_expiry", table_name="auth_sessions")
    op.drop_table("auth_sessions")
    op.drop_table("auth_identities")
