from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from personal_travel.db.base import Base
from personal_travel.models.common import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from personal_travel.models.itinerary import ItineraryItem
    from personal_travel.models.reservation import Reservation, SavedPlace


class Place(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "places"

    owner_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    category: Mapped[str | None] = mapped_column(String(120), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    website_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider_place_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    provider_source_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    provider_source_attribution: Mapped[str | None] = mapped_column(String(500), nullable=True)
    provider_source_license: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider_source_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    items: Mapped[list[ItineraryItem]] = relationship("ItineraryItem", back_populates="place")
    reservations: Mapped[list[Reservation]] = relationship("Reservation", back_populates="place")
    saved_places: Mapped[list[SavedPlace]] = relationship("SavedPlace", back_populates="place")

    __table_args__ = (
        UniqueConstraint(
            "owner_id", "provider", "provider_place_id", name="uq_places_owner_provider_external_id"
        ),
    )
