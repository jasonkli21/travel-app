"""Add the owner-scoped itinerary proposal lifecycle.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "itinerary_proposals",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", sa.String(length=128), nullable=False),
        sa.Column("trip_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("downstream_key", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("schema_version", sa.String(length=64), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("support_mode", sa.String(length=32), nullable=False),
        sa.Column("trip_handle", sa.String(length=66), nullable=False),
        sa.Column("upstream_proposal_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("generation_deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column("base_trip_revision", sa.Integer(), nullable=False),
        sa.Column("base_place_revisions", sa.JSON(), nullable=False),
        sa.Column("base_snapshot", sa.JSON(), nullable=False),
        sa.Column("operations", sa.JSON(), nullable=True),
        sa.Column("preview", sa.JSON(), nullable=True),
        sa.Column("citations", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(length=80), nullable=True),
        sa.Column("applied_outcome", sa.JSON(), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "state IN ('generating','outcome_unknown','ready','failed','applied','rejected')",
            name="ck_proposals_valid_state",
        ),
        sa.CheckConstraint(
            "support_mode IN ('context_only','research_evidence')",
            name="ck_proposals_valid_support_mode",
        ),
        sa.CheckConstraint(
            "base_trip_revision >= 0", name="ck_proposals_base_revision_nonnegative"
        ),
        sa.ForeignKeyConstraint(["trip_id"], ["trips.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_id", "trip_id", "idempotency_key", name="uq_proposals_owner_trip_idempotency"
        ),
    )
    op.create_index(
        "ix_itinerary_proposals_owner_trip_created",
        "itinerary_proposals",
        ["owner_id", "trip_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_itinerary_proposals_owner_trip_created", table_name="itinerary_proposals")
    op.drop_table("itinerary_proposals")
