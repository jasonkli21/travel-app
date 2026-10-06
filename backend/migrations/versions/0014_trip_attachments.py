"""Extend the private source lifecycle for trip and reservation attachments.

Revision ID: 0014
Revises: 0013
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "source_attachments",
        sa.Column("reservation_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_source_attachments_reservation_id_reservations",
        "source_attachments",
        "reservations",
        ["reservation_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_source_attachments_reservation_id", "source_attachments", ["reservation_id"]
    )
    op.add_column(
        "source_attachments",
        sa.Column("purpose", sa.String(length=24), server_default="booking_source", nullable=False),
    )
    op.alter_column("source_attachments", "purpose", server_default=None)
    op.add_column("source_attachments", sa.Column("upload_request_key", sa.String(length=128)))
    op.add_column(
        "source_attachments", sa.Column("upload_request_fingerprint", sa.String(length=64))
    )
    op.alter_column(
        "source_attachments", "expires_at", existing_type=sa.DateTime(timezone=True), nullable=True
    )
    op.drop_constraint(
        "ck_source_attachments_source_attachment_media_size",
        "source_attachments",
        type_="check",
    )
    op.create_check_constraint(
        "ck_source_attachments_source_attachment_media_size",
        "source_attachments",
        "(media_type = 'text/plain' AND byte_size <= 1048576) "
        "OR (media_type = 'application/pdf' AND byte_size <= 10485760) "
        "OR (media_type IN ('image/jpeg','image/png') AND byte_size <= 10485760)",
    )
    op.create_check_constraint(
        "ck_source_attachments_source_attachment_purpose",
        "source_attachments",
        "purpose IN ('booking_source','trip_attachment')",
    )
    op.create_check_constraint(
        "ck_source_attachments_source_attachment_upload_request",
        "source_attachments",
        "(upload_request_key IS NULL AND upload_request_fingerprint IS NULL) OR "
        "(upload_request_key IS NOT NULL AND upload_request_fingerprint IS NOT NULL AND "
        "length(upload_request_key) BETWEEN 8 AND 128 AND "
        "length(upload_request_fingerprint) = 64)",
    )
    op.create_unique_constraint(
        "uq_attachment_upload_request", "source_attachments", ["owner_id", "upload_request_key"]
    )
    op.create_index(
        "ix_source_attachments_owner_trip_purpose",
        "source_attachments",
        ["owner_id", "trip_id", "purpose"],
    )


def downgrade() -> None:
    has_trip_documents = (
        op.get_bind()
        .execute(
            sa.text("SELECT 1 FROM source_attachments WHERE purpose = 'trip_attachment' LIMIT 1")
        )
        .first()
    )
    if has_trip_documents is not None:
        raise RuntimeError(
            "Cannot downgrade 0014 while trip attachments exist; export or remove them first."
        )
    op.drop_index("ix_source_attachments_owner_trip_purpose", table_name="source_attachments")
    op.drop_constraint("uq_attachment_upload_request", "source_attachments", type_="unique")
    op.drop_constraint(
        "ck_source_attachments_source_attachment_upload_request",
        "source_attachments",
        type_="check",
    )
    op.drop_constraint(
        "ck_source_attachments_source_attachment_purpose", "source_attachments", type_="check"
    )
    op.drop_constraint(
        "ck_source_attachments_source_attachment_media_size",
        "source_attachments",
        type_="check",
    )
    op.create_check_constraint(
        "ck_source_attachments_source_attachment_media_size",
        "source_attachments",
        "(media_type = 'text/plain' AND byte_size <= 1048576) OR media_type = 'application/pdf'",
    )
    op.alter_column(
        "source_attachments", "expires_at", existing_type=sa.DateTime(timezone=True), nullable=False
    )
    op.drop_column("source_attachments", "upload_request_fingerprint")
    op.drop_column("source_attachments", "upload_request_key")
    op.drop_column("source_attachments", "purpose")
    op.drop_index("ix_source_attachments_reservation_id", table_name="source_attachments")
    op.drop_constraint(
        "fk_source_attachments_reservation_id_reservations",
        "source_attachments",
        type_="foreignkey",
    )
    op.drop_column("source_attachments", "reservation_id")
