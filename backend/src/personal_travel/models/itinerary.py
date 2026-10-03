from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from personal_travel.db.base import Base
from personal_travel.models.common import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from personal_travel.models.place import Place
    from personal_travel.models.trip import TripDay


class ItineraryItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "itinerary_items"

    trip_day_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("trip_days.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
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

    trip_day: Mapped[TripDay] = relationship("TripDay", back_populates="items")
    place: Mapped[Place | None] = relationship("Place", back_populates="items")

    __table_args__ = (
        CheckConstraint(
            "item_type IN ('activity','food','lodging','transport','flight','note')",
            name="valid_item_type",
        ),
        CheckConstraint(
            "status IN ('tentative','planned','booked','completed','cancelled')",
            name="valid_status",
        ),
        CheckConstraint(
            "ends_at IS NULL OR starts_at IS NULL OR starts_at <= ends_at", name="valid_time_range"
        ),
        CheckConstraint("sort_order >= 0", name="valid_sort_order"),
    )
