"""Durable import and opaque attachment metadata.

Revision ID: 0010
Revises: 0009
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "source_attachments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column(
            "trip_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("trips.id", ondelete="SET NULL")
        ),
        sa.Column("object_key", sa.String(64), nullable=False, unique=True),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("media_type", sa.String(32), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("display_filename", sa.String(120)),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "state IN ('pending','ready','deleting')",
            name="ck_source_attachments_source_attachment_state",
        ),
        sa.CheckConstraint(
            "byte_size > 0 AND byte_size <= 10485760",
            name="ck_source_attachments_source_attachment_size",
        ),
        sa.CheckConstraint(
            "(media_type = 'text/plain' AND byte_size <= 1048576) "
            "OR media_type = 'application/pdf'",
            name="ck_source_attachments_source_attachment_media_size",
        ),
        sa.CheckConstraint(
            "length(sha256) = 64", name="ck_source_attachments_source_attachment_hash_length"
        ),
        sa.CheckConstraint(
            "length(object_key) = 32", name="ck_source_attachments_source_attachment_key_length"
        ),
    )
    op.create_index("ix_source_attachments_owner_id", "source_attachments", ["owner_id"])
    op.create_index("ix_source_attachments_trip_id", "source_attachments", ["trip_id"])
    op.create_table(
        "booking_imports",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column(
            "trip_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("trips.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("source_attachments.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("request_key", sa.String(128), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("parser_version", sa.String(32), nullable=False),
        sa.Column("review_revision", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("owner_id", "trip_id", "request_key", name="uq_import_request_key"),
        sa.UniqueConstraint("owner_id", "trip_id", "source_sha256", name="uq_import_source_hash"),
        sa.UniqueConstraint("source_id", name="uq_import_source"),
        sa.CheckConstraint(
            "length(request_fingerprint) = 64",
            name="ck_booking_imports_request_fingerprint_length",
        ),
        sa.CheckConstraint(
            "length(source_sha256) = 64", name="ck_booking_imports_source_hash_length"
        ),
        sa.CheckConstraint(
            "review_revision >= 0", name="ck_booking_imports_review_revision_nonnegative"
        ),
        sa.CheckConstraint(
            "state IN ('received','extracting','review_ready','applied','rejected','failed')",
            name="ck_booking_imports_booking_import_state",
        ),
    )
    op.create_index("ix_booking_imports_owner_id", "booking_imports", ["owner_id"])


def downgrade() -> None:
    op.drop_table("booking_imports")
    op.drop_table("source_attachments")
