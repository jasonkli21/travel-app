"""Persist immutable Travel source commands with owner erasure cascade."""

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "delegated_source_commands",
        sa.Column("command_id", sa.Uuid(), primary_key=True),
        sa.Column(
            "owner_id",
            sa.String(128),
            sa.ForeignKey("auth_identities.owner_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("command", sa.JSON(), nullable=False),
        sa.Column("proposal_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_delegated_source_commands_owner_id", "delegated_source_commands", ["owner_id"]
    )


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT 1 FROM delegated_source_commands LIMIT 1")):
        raise RuntimeError("Cannot discard retained delegated task recovery state")
    op.drop_table("delegated_source_commands")
