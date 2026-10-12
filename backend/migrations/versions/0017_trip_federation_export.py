"""Add explicit, revocable Travel-to-Shopping trip sharing."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("trips", sa.Column("destination", sa.String(length=160), nullable=True))
    op.add_column(
        "trips",
        sa.Column(
            "cross_domain_sharing_allowed",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("trips", "cross_domain_sharing_allowed")
    op.drop_column("trips", "destination")
