"""Persist the accepted upstream revision and per-operation evidence support.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LEGACY_UPSTREAM_REVISION = "8535cad3a146b1a19cab0958c439f170d19b8095"


def upgrade() -> None:
    op.add_column(
        "itinerary_proposals", sa.Column("upstream_revision", sa.String(length=40), nullable=True)
    )
    op.add_column("itinerary_proposals", sa.Column("operation_support", sa.JSON(), nullable=True))
    proposals = sa.table(
        "itinerary_proposals",
        sa.column("upstream_revision", sa.String(length=40)),
        sa.column("operation_support", sa.JSON()),
    )
    op.execute(
        proposals.update().values(
            upstream_revision=_LEGACY_UPSTREAM_REVISION,
            operation_support=[],
        )
    )
    op.alter_column("itinerary_proposals", "upstream_revision", nullable=False)
    op.alter_column("itinerary_proposals", "operation_support", nullable=False)


def downgrade() -> None:
    op.drop_column("itinerary_proposals", "operation_support")
    op.drop_column("itinerary_proposals", "upstream_revision")
