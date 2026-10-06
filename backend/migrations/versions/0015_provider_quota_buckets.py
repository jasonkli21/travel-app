"""Add shared provider admission counters.

Revision ID: 0015
Revises: 0014
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "provider_quota_buckets",
        sa.Column("scope_hash", sa.String(length=64), nullable=False),
        sa.Column("bucket_key", sa.String(length=48), nullable=False),
        sa.Column("window_seconds", sa.Integer(), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "length(scope_hash) = 64", name="ck_provider_quota_buckets_scope_hash_length"
        ),
        sa.CheckConstraint(
            "length(bucket_key) BETWEEN 1 AND 48",
            name="ck_provider_quota_buckets_bucket_key_length",
        ),
        sa.CheckConstraint(
            "window_seconds IN (60,86400)",
            name="ck_provider_quota_buckets_supported_window",
        ),
        sa.CheckConstraint("used > 0", name="ck_provider_quota_buckets_used_positive"),
        sa.PrimaryKeyConstraint(
            "scope_hash",
            "bucket_key",
            "window_seconds",
            "window_start",
            name="pk_provider_quota_buckets",
        ),
    )
    op.create_index(
        "ix_provider_quota_buckets_window_start",
        "provider_quota_buckets",
        ["window_start"],
    )


def downgrade() -> None:
    op.drop_index("ix_provider_quota_buckets_window_start", table_name="provider_quota_buckets")
    op.drop_table("provider_quota_buckets")
