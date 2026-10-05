"""Add extracted-text identity and durable upstream deletion intents.

Revision ID: 0013
Revises: 0012
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "booking_imports", sa.Column("extraction_key_created_at", sa.DateTime(timezone=True))
    )
    op.execute(
        sa.text(
            "UPDATE booking_imports SET extraction_key_created_at = created_at "
            "WHERE extraction_key IS NOT NULL"
        )
    )
    op.add_column(
        "booking_imports", sa.Column("extraction_text_sha256", sa.String(64), nullable=True)
    )
    op.create_check_constraint(
        "ck_booking_imports_extraction_text_hash_length",
        "booking_imports",
        "extraction_text_sha256 IS NULL OR length(extraction_text_sha256) = 64",
    )
    op.create_table(
        "booking_deletion_intents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("extraction_key", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("length(source_sha256) = 64", name="ck_booking_deletion_hash_length"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_id", "extraction_key", name="uq_booking_deletion_owner_key"),
    )
    op.create_index(
        "ix_booking_deletion_intents_owner_id", "booking_deletion_intents", ["owner_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_booking_deletion_intents_owner_id", table_name="booking_deletion_intents")
    op.drop_table("booking_deletion_intents")
    op.drop_constraint(
        "ck_booking_imports_extraction_text_hash_length", "booking_imports", type_="check"
    )
    op.drop_column("booking_imports", "extraction_text_sha256")
    op.drop_column("booking_imports", "extraction_key_created_at")
