from decimal import Decimal

from sqlalchemy import Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from personal_travel.db.base import Base
from personal_travel.models.common import TimestampMixin, UUIDPrimaryKeyMixin


class Place(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "places"

    owner_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider_place_id: Mapped[str | None] = mapped_column(String(256), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "owner_id", "provider", "provider_place_id", name="uq_places_owner_provider_external_id"
        ),
    )
