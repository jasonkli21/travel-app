"""Retain upstream authentication time without backfilling unknown sessions."""

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("auth_sessions", sa.Column("upstream_auth_time", sa.BigInteger(), nullable=True))
    op.create_check_constraint(
        op.f("ck_auth_sessions_valid_upstream_auth_time"),
        "auth_sessions",
        "upstream_auth_time IS NULL OR upstream_auth_time >= 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_auth_sessions_valid_upstream_auth_time"), "auth_sessions", type_="check"
    )
    op.drop_column("auth_sessions", "upstream_auth_time")
