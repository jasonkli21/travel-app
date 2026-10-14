"""Travel-owned immutable source commands, separate from canonical trip state."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import JSON, DateTime, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from personal_travel.db.base import Base


class DelegatedSourceCommand(Base):
    __tablename__ = "delegated_source_commands"
    command_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    owner_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("auth_identities.owner_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    command: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    proposal_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
