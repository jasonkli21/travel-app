"""Add monotonic trip and shared-place revisions.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "trips",
        sa.Column("revision", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "places",
        sa.Column("revision", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_check_constraint("ck_trips_revision_nonnegative", "trips", "revision >= 0")
    op.create_check_constraint("ck_places_revision_nonnegative", "places", "revision >= 0")


def downgrade() -> None:
    op.drop_constraint("ck_places_revision_nonnegative", "places", type_="check")
    op.drop_constraint("ck_trips_revision_nonnegative", "trips", type_="check")
    op.drop_column("places", "revision")
    op.drop_column("trips", "revision")
