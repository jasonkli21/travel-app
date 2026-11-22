"""Owner-scoped create binding and content-free preview attempt."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from personal_travel.db.base import Base


class PrivatePlanBinding(Base):
    __tablename__ = "personal_ai_private_plan_bindings"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    create_key: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    owner_id: Mapped[str] = mapped_column(String(128), nullable=False)
    trip_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("trips.id", ondelete="CASCADE"), nullable=False
    )
    issuer: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    target_id: Mapped[str] = mapped_column(String(200), nullable=False)
    client_namespace: Mapped[str] = mapped_column(String(200), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    conversation_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("personal_ai_sidecar_anchors.conversation_id", ondelete="CASCADE"),
    )
    create_receipt_digest: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("conversation_id", name="uq_private_plan_binding_conversation"),
        CheckConstraint(
            "create_receipt_digest IS NULL OR "
            "(conversation_id IS NOT NULL AND create_receipt_digest ~ '^[0-9a-f]{64}$')",
            name="create_receipt",
        ),
        UniqueConstraint(
            "owner_id",
            "target_id",
            "client_namespace",
            "create_key",
            name="uq_private_plan_create_scope",
        ),
        UniqueConstraint(
            "owner_id",
            "trip_id",
            "target_id",
            "client_namespace",
            name="uq_private_plan_binding_scope",
        ),
    )


class PrivatePlanIntent(Base):
    __tablename__ = "personal_ai_private_plan_intents"

    binding_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("personal_ai_private_plan_bindings.id", ondelete="CASCADE"),
        primary_key=True,
    )
    original_key: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    owner_id: Mapped[str] = mapped_column(String(128), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    source_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    preview_digest: Mapped[str | None] = mapped_column(String(64))
    preview_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("source_revision >= 0", name="source_revision"),
        CheckConstraint(
            "(state = 'preview_ready' AND preview_digest IS NOT NULL "
            "AND preview_expires_at IS NOT NULL) OR "
            "(state IN ('preview_pending', 'preview_unknown') AND preview_digest IS NULL "
            "AND preview_expires_at IS NULL)",
            name="state",
        ),
    )
