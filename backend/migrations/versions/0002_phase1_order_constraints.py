"""Add Phase 1 ordering constraints.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-02
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_check_constraint("valid_day_index", "trip_days", "day_index >= 1")
    op.create_check_constraint("valid_sort_order", "itinerary_items", "sort_order >= 0")


def downgrade() -> None:
    op.drop_constraint("valid_sort_order", "itinerary_items", type_="check")
    op.drop_constraint("valid_day_index", "trip_days", type_="check")
