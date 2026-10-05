"""Retain durable import identity after source bytes are removed.

Revision ID: 0011
Revises: 0010
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for foreign_key in inspector.get_foreign_keys("booking_imports"):
        if foreign_key["constrained_columns"] == ["source_id"]:
            name = foreign_key["name"]
            if name is None:
                raise RuntimeError("The booking import source foreign key must be named.")
            op.drop_constraint(name, "booking_imports", type_="foreignkey")

    op.alter_column("booking_imports", "source_id", nullable=True)
    op.create_foreign_key(
        "fk_booking_imports_source_id_source_attachments",
        "booking_imports",
        "source_attachments",
        ["source_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column("booking_imports", sa.Column("source_media_type", sa.String(32), nullable=True))
    op.add_column("booking_imports", sa.Column("source_byte_size", sa.BigInteger(), nullable=True))
    op.execute(
        "UPDATE booking_imports AS imports "
        "SET source_media_type = sources.media_type, source_byte_size = sources.byte_size "
        "FROM source_attachments AS sources WHERE sources.id = imports.source_id"
    )
    op.alter_column("booking_imports", "source_media_type", nullable=False)
    op.alter_column("booking_imports", "source_byte_size", nullable=False)
    op.create_check_constraint(
        "ck_booking_imports_source_media_size",
        "booking_imports",
        "(source_media_type = 'text/plain' AND source_byte_size <= 1048576) "
        "OR (source_media_type = 'application/pdf' AND source_byte_size <= 10485760)",
    )


def downgrade() -> None:
    detached_imports = op.get_bind().scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM booking_imports WHERE source_id IS NULL)")
    )
    if detached_imports:
        raise RuntimeError(
            "Cannot downgrade 0011 while imports are detached from deleted sources; "
            "restore a pre-0011 backup or keep the current schema."
        )
    op.drop_constraint("ck_booking_imports_source_media_size", "booking_imports", type_="check")
    op.drop_column("booking_imports", "source_byte_size")
    op.drop_column("booking_imports", "source_media_type")
    op.drop_constraint(
        "fk_booking_imports_source_id_source_attachments", "booking_imports", type_="foreignkey"
    )
    op.alter_column("booking_imports", "source_id", nullable=False)
    op.create_foreign_key(
        "fk_booking_imports_source_id_source_attachments",
        "booking_imports",
        "source_attachments",
        ["source_id"],
        ["id"],
        ondelete="RESTRICT",
    )
