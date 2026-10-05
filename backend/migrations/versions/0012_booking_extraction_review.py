"""Durable booking extraction and idempotent confirmation lifecycle.

Revision ID: 0012
Revises: 0011
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "booking_imports",
        sa.Column(
            "retention_choice",
            sa.String(32),
            nullable=False,
            server_default="delete_after_confirmation",
        ),
    )
    op.add_column(
        "booking_imports", sa.Column("extraction_key", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.add_column(
        "booking_imports",
        sa.Column("extraction_claim_token", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "booking_imports",
        sa.Column("extraction_claimed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "booking_imports",
        sa.Column(
            "extraction_post_attempted", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column(
        "booking_imports",
        sa.Column(
            "upstream_delete_pending", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column(
        "booking_imports",
        sa.Column("upstream_extraction_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column("booking_imports", sa.Column("upstream_revision", sa.String(40), nullable=True))
    op.add_column(
        "booking_imports",
        sa.Column("upstream_result_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("booking_imports", sa.Column("candidate_snapshot", sa.JSON(), nullable=True))
    op.add_column(
        "booking_imports",
        sa.Column("confirmation_key", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "booking_imports", sa.Column("confirmation_fingerprint", sa.String(64), nullable=True)
    )
    op.add_column("booking_imports", sa.Column("confirmation_outcome", sa.JSON(), nullable=True))
    op.drop_constraint("ck_booking_imports_booking_import_state", "booking_imports", type_="check")
    op.create_check_constraint(
        "ck_booking_imports_booking_import_state",
        "booking_imports",
        "state IN ('received','extracting','review_ready','applied','rejected','failed','expired')",
    )
    op.create_unique_constraint(
        "uq_booking_import_extraction_key",
        "booking_imports",
        ["owner_id", "trip_id", "extraction_key"],
    )
    op.create_check_constraint(
        "ck_booking_import_retention_choice",
        "booking_imports",
        "retention_choice IN ('delete_after_confirmation','keep_until_expiry')",
    )
    op.create_check_constraint(
        "ck_booking_import_confirmation_fingerprint_length",
        "booking_imports",
        "confirmation_fingerprint IS NULL OR length(confirmation_fingerprint) = 64",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_booking_import_confirmation_fingerprint_length", "booking_imports", type_="check"
    )
    op.drop_constraint("ck_booking_import_retention_choice", "booking_imports", type_="check")
    op.drop_constraint("uq_booking_import_extraction_key", "booking_imports", type_="unique")
    op.drop_constraint("ck_booking_imports_booking_import_state", "booking_imports", type_="check")
    op.create_check_constraint(
        "ck_booking_imports_booking_import_state",
        "booking_imports",
        "state IN ('received','extracting','review_ready','applied','rejected','failed')",
    )
    for name in (
        "confirmation_outcome",
        "confirmation_fingerprint",
        "confirmation_key",
        "candidate_snapshot",
        "upstream_result_expires_at",
        "upstream_revision",
        "upstream_extraction_id",
        "upstream_delete_pending",
        "extraction_post_attempted",
        "extraction_claimed_at",
        "extraction_claim_token",
        "extraction_key",
        "retention_choice",
    ):
        op.drop_column("booking_imports", name)
