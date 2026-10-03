from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from personal_travel.db.base import Base
from personal_travel.models.common import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from personal_travel.models.itinerary import ItineraryItem
    from personal_travel.models.place import Place
    from personal_travel.models.trip import Trip


class Reservation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "reservations"

    owner_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    trip_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("trips.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    reservation_type: Mapped[str] = mapped_column(String(32), nullable=False, default="other")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="tentative")
    provider_name: Mapped[str] = mapped_column(String(200), nullable=False)
    confirmation_code: Mapped[str | None] = mapped_column(String(160), nullable=True)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    place_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("places.id", ondelete="SET NULL"), nullable=True, index=True
    )
    source_reference: Mapped[str | None] = mapped_column(String(500), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    trip: Mapped[Trip] = relationship("Trip", back_populates="reservations")
    place: Mapped[Place | None] = relationship("Place", back_populates="reservations")
    items: Mapped[list[ItineraryItem]] = relationship("ItineraryItem", back_populates="reservation")

    __table_args__ = (
        CheckConstraint(
            "reservation_type IN ("
            "'lodging','flight','train','car_rental','activity','dining','other')",
            name="ck_reservations_valid_reservation_type",
        ),
        CheckConstraint(
            "status IN ('tentative','confirmed','cancelled')",
            name="ck_reservations_valid_reservation_status",
        ),
        CheckConstraint(
            "ends_at IS NULL OR starts_at IS NULL OR starts_at <= ends_at",
            name="ck_reservations_valid_reservation_time_range",
        ),
        Index("ix_reservations_owner_trip_start", "owner_id", "trip_id", "starts_at"),
    )


class SavedPlace(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "saved_places"

    owner_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    trip_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("trips.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    place_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("places.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    trip: Mapped[Trip] = relationship("Trip", back_populates="saved_places")
    place: Mapped[Place] = relationship("Place", back_populates="saved_places")

    __table_args__ = (
        UniqueConstraint(
            "owner_id", "trip_id", "place_id", name="uq_saved_places_owner_trip_place"
        ),
    )
