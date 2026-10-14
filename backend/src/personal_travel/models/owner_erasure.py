"""Minimal retained owner fence; canonical data and authentication rows can be erased."""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from personal_travel.db.base import Base


class OwnerErasureLedger(Base):
    __tablename__ = "owner_erasure_ledger"
    owner_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (CheckConstraint("length(owner_id) BETWEEN 1 AND 128", name="owner_length"),)
