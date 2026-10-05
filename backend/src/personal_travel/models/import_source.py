"""Private source records; bytes remain in the opaque local store."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
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
    object_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    media_type: Mapped[str] = mapped_column(String(32), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    display_filename: Mapped[str | None] = mapped_column(String(120))
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (
        CheckConstraint("state IN ('pending','ready','deleting')", name="source_attachment_state"),
        CheckConstraint("byte_size > 0 AND byte_size <= 10485760", name="source_attachment_size"),
        CheckConstraint(
            "(media_type = 'text/plain' AND byte_size <= 1048576) "
            "OR media_type = 'application/pdf'",
            name="source_attachment_media_size",
        ),
        CheckConstraint("length(sha256) = 64", name="source_attachment_hash_length"),
        CheckConstraint("length(object_key) = 32", name="source_attachment_key_length"),
    )


class BookingImport(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "booking_imports"
    owner_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    trip_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trips.id", ondelete="CASCADE"), nullable=False
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_attachments.id", ondelete="RESTRICT"), nullable=False
    )
    request_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="received")
    parser_version: Mapped[str] = mapped_column(String(32), nullable=False, default="source-v1")
    review_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    __table_args__ = (
        UniqueConstraint("owner_id", "trip_id", "request_key", name="uq_import_request_key"),
        UniqueConstraint("owner_id", "trip_id", "source_sha256", name="uq_import_source_hash"),
        UniqueConstraint("source_id", name="uq_import_source"),
        CheckConstraint("length(request_fingerprint) = 64", name="request_fingerprint_length"),
        CheckConstraint("length(source_sha256) = 64", name="source_hash_length"),
        CheckConstraint("review_revision >= 0", name="import_review_revision_nonnegative"),
        CheckConstraint(
            "state IN ('received','extracting','review_ready','applied','rejected','failed')",
            name="booking_import_state",
        ),
    )
