"""Private source records; bytes remain in the opaque local store."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from personal_travel.db.base import Base
from personal_travel.models.common import TimestampMixin, UUIDPrimaryKeyMixin


class SourceAttachment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "source_attachments"
    owner_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    trip_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trips.id", ondelete="SET NULL"), index=True
    )
    reservation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("reservations.id", ondelete="SET NULL"), index=True
    )
    purpose: Mapped[str] = mapped_column(String(24), nullable=False, default="booking_source")
    object_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    media_type: Mapped[str] = mapped_column(String(32), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    display_filename: Mapped[str | None] = mapped_column(String(120))
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    upload_request_key: Mapped[str | None] = mapped_column(String(128))
    upload_request_fingerprint: Mapped[str | None] = mapped_column(String(64))
    __table_args__ = (
        Index(
            "ix_source_attachments_owner_trip_purpose",
            "owner_id",
            "trip_id",
            "purpose",
        ),
        UniqueConstraint("owner_id", "upload_request_key", name="uq_attachment_upload_request"),
        CheckConstraint("state IN ('pending','ready','deleting')", name="source_attachment_state"),
        CheckConstraint(
            "purpose IN ('booking_source','trip_attachment')", name="source_attachment_purpose"
        ),
        CheckConstraint("byte_size > 0 AND byte_size <= 10485760", name="source_attachment_size"),
        CheckConstraint(
            "(media_type = 'text/plain' AND byte_size <= 1048576) "
            "OR (media_type = 'application/pdf' AND byte_size <= 10485760) "
            "OR (media_type IN ('image/jpeg','image/png') AND byte_size <= 10485760)",
            name="source_attachment_media_size",
        ),
        CheckConstraint("length(sha256) = 64", name="source_attachment_hash_length"),
        CheckConstraint("length(object_key) = 32", name="source_attachment_key_length"),
        CheckConstraint(
            "(upload_request_key IS NULL AND upload_request_fingerprint IS NULL) OR "
            "(length(upload_request_key) BETWEEN 8 AND 128 AND "
            "length(upload_request_fingerprint) = 64)",
            name="source_attachment_upload_request",
        ),
    )


class BookingImport(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "booking_imports"
    owner_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    trip_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trips.id", ondelete="CASCADE"), nullable=False
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_attachments.id", ondelete="SET NULL"), nullable=True
    )
    request_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_media_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="received")
    parser_version: Mapped[str] = mapped_column(String(32), nullable=False, default="source-v1")
    review_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retention_choice: Mapped[str] = mapped_column(
        String(32), nullable=False, default="delete_after_confirmation"
    )
    extraction_key: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    extraction_claim_token: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    extraction_claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    extraction_key_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    extraction_text_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    extraction_post_attempted: Mapped[bool] = mapped_column(nullable=False, default=False)
    upstream_delete_pending: Mapped[bool] = mapped_column(nullable=False, default=False)
    upstream_extraction_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    upstream_revision: Mapped[str | None] = mapped_column(String(40), nullable=True)
    upstream_result_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    candidate_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    confirmation_key: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    confirmation_fingerprint: Mapped[str | None] = mapped_column(String(64))
    confirmation_outcome: Mapped[dict[str, object] | None] = mapped_column(JSON)
    __table_args__ = (
        UniqueConstraint("owner_id", "trip_id", "request_key", name="uq_import_request_key"),
        UniqueConstraint("owner_id", "trip_id", "source_sha256", name="uq_import_source_hash"),
        UniqueConstraint(
            "owner_id", "trip_id", "extraction_key", name="uq_booking_import_extraction_key"
        ),
        UniqueConstraint("source_id", name="uq_import_source"),
        CheckConstraint("length(request_fingerprint) = 64", name="request_fingerprint_length"),
        CheckConstraint("length(source_sha256) = 64", name="source_hash_length"),
        CheckConstraint(
            "extraction_text_sha256 IS NULL OR length(extraction_text_sha256) = 64",
            name="extraction_text_hash_length",
        ),
        CheckConstraint(
            "(source_media_type = 'text/plain' AND source_byte_size <= 1048576) "
            "OR (source_media_type = 'application/pdf' AND source_byte_size <= 10485760)",
            name="source_media_size",
        ),
        CheckConstraint("review_revision >= 0", name="import_review_revision_nonnegative"),
        CheckConstraint(
            "retention_choice IN ('delete_after_confirmation','keep_until_expiry')",
            name="booking_import_retention_choice",
        ),
        CheckConstraint(
            "confirmation_fingerprint IS NULL OR length(confirmation_fingerprint) = 64",
            name="booking_import_confirmation_fingerprint_length",
        ),
        CheckConstraint(
            "state IN ('received','extracting','review_ready','applied','rejected','failed',"
            "'expired')",
            name="booking_import_state",
        ),
    )


class BookingDeletionIntent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Minimal owner-scoped key needed to finish upstream privacy deletion."""

    __tablename__ = "booking_deletion_intents"
    owner_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    extraction_key: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    __table_args__ = (
        UniqueConstraint("owner_id", "extraction_key", name="uq_booking_deletion_owner_key"),
        CheckConstraint("length(source_sha256) = 64", name="booking_deletion_hash_length"),
    )
