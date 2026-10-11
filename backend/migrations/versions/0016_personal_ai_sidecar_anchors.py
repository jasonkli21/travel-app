"""Bind Personal AI sidecar conversations to their Travel trip anchor."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "personal_ai_sidecar_anchors",
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", sa.String(length=128), nullable=False),
        sa.Column("trip_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["trip_id"], ["trips.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("conversation_id"),
    )
    op.create_index(
        "ix_personal_ai_sidecar_anchors_owner_trip",
        "personal_ai_sidecar_anchors",
        ["owner_id", "trip_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_personal_ai_sidecar_anchors_owner_trip",
        table_name="personal_ai_sidecar_anchors",
    )
    op.drop_table("personal_ai_sidecar_anchors")
