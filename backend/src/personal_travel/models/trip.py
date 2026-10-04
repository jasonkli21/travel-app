from __future__ import annotations

import uuid
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from personal_travel.db.base import Base
from personal_travel.models.common import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from personal_travel.models.itinerary import ItineraryItem
    from personal_travel.models.reservation import Reservation, SavedPlace


class Trip(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "trips"

    owner_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    days: Mapped[list[TripDay]] = relationship(
        back_populates="trip", cascade="all, delete-orphan", order_by="TripDay.day_index"
    )
    reservations: Mapped[list[Reservation]] = relationship(
        "Reservation", back_populates="trip", cascade="all, delete-orphan"
    )
    saved_places: Mapped[list[SavedPlace]] = relationship(
        "SavedPlace", back_populates="trip", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("start_date <= end_date", name="valid_date_range"),
        CheckConstraint("revision >= 0", name="revision_nonnegative"),
        Index("ix_trips_owner_start_date", "owner_id", "start_date"),
    )


class TripDay(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "trip_days"

    trip_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trips.id", ondelete="CASCADE"), nullable=False, index=True
    )
    day_index: Mapped[int] = mapped_column(Integer, nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)

    trip: Mapped[Trip] = relationship(back_populates="days")
    items: Mapped[list[ItineraryItem]] = relationship(
        "ItineraryItem",
        back_populates="trip_day",
        cascade="all, delete-orphan",
        order_by="ItineraryItem.sort_order",
    )

    __table_args__ = (
        CheckConstraint("day_index >= 1", name="valid_day_index"),
        UniqueConstraint("trip_id", "day_index", name="uq_trip_days_trip_day_index"),
        UniqueConstraint("trip_id", "date", name="uq_trip_days_trip_date"),
    )
