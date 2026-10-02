import uuid
from datetime import date

from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from personal_travel.db.base import Base
from personal_travel.models.common import TimestampMixin, UUIDPrimaryKeyMixin


class Trip(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "trips"

    owner_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")

    days: Mapped[list["TripDay"]] = relationship(
        back_populates="trip", cascade="all, delete-orphan", order_by="TripDay.day_index"
    )

    __table_args__ = (
        CheckConstraint("start_date <= end_date", name="valid_date_range"),
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

    __table_args__ = (
        UniqueConstraint("trip_id", "day_index", name="uq_trip_days_trip_day_index"),
        UniqueConstraint("trip_id", "date", name="uq_trip_days_trip_date"),
    )
