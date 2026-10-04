from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from personal_travel.db.base import Base
from personal_travel.models.common import TimestampMixin, UUIDPrimaryKeyMixin


class ItineraryProposal(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "itinerary_proposals"

    owner_id: Mapped[str] = mapped_column(String(128), nullable=False)
    trip_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trips.id", ondelete="CASCADE"), nullable=False
    )
    idempotency_key: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    downstream_key: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    upstream_revision: Mapped[str] = mapped_column(String(40), nullable=False)
    support_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    trip_handle: Mapped[str] = mapped_column(String(66), nullable=False)
    upstream_proposal_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    generation_deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    base_trip_revision: Mapped[int] = mapped_column(nullable=False)
    base_place_revisions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    base_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    operations: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    operation_support: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    preview: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    citations: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_code: Mapped[str | None] = mapped_column(String(80))
    applied_outcome: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_itinerary_proposals_owner_trip_created", "owner_id", "trip_id", "created_at"),
        UniqueConstraint(
            "owner_id", "trip_id", "idempotency_key", name="uq_proposals_owner_trip_idempotency"
        ),
        CheckConstraint(
            "state IN ('generating','outcome_unknown','ready','failed','applied','rejected')",
            name="ck_proposals_valid_state",
        ),
        CheckConstraint(
            "support_mode IN ('context_only','research_evidence')",
            name="ck_proposals_valid_support_mode",
        ),
        CheckConstraint("base_trip_revision >= 0", name="ck_proposals_base_revision_nonnegative"),
    )
