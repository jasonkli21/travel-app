"""Retain source attribution for imported provider places.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("places", sa.Column("provider_source_name", sa.String(length=128), nullable=True))
    op.add_column(
        "places",
        sa.Column("provider_source_attribution", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "places", sa.Column("provider_source_license", sa.String(length=255), nullable=True)
    )
    op.add_column("places", sa.Column("provider_source_url", sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column("places", "provider_source_url")
    op.drop_column("places", "provider_source_license")
    op.drop_column("places", "provider_source_attribution")
    op.drop_column("places", "provider_source_name")
