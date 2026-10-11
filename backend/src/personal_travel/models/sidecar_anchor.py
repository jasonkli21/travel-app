"""Travel-owned host identity for application-scoped Personal AI conversations."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from personal_travel.db.base import Base


class PersonalAISidecarAnchor(Base):
    __tablename__ = "personal_ai_sidecar_anchors"

    conversation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(128), nullable=False)
    trip_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("trips.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

    __table_args__ = (Index("ix_personal_ai_sidecar_anchors_owner_trip", "owner_id", "trip_id"),)
