import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from personal_travel.db.base import Base
from personal_travel.models.common import TimestampMixin, UUIDPrimaryKeyMixin


class ItineraryItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "itinerary_items"

    trip_day_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trip_days.id", ondelete="CASCADE"), nullable=False, index=True
    )
    place_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("places.id", ondelete="SET NULL"), nullable=True, index=True
    )
    item_type: Mapped[str] = mapped_column(String(32), nullable=False, default="activity")
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="tentative")

    __table_args__ = (
        CheckConstraint(
            "item_type IN ('activity','food','lodging','transport','flight','note')",
            name="valid_item_type",
        ),
        CheckConstraint(
            "status IN ('tentative','planned','booked','completed','cancelled')",
            name="valid_status",
        ),
        CheckConstraint("ends_at IS NULL OR starts_at IS NULL OR starts_at <= ends_at", name="valid_time_range"),
    )
