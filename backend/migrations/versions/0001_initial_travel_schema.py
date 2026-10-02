"""Initial travel schema.

Revision ID: 0001
Revises:
Create Date: 2026-10-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "trips",
        sa.Column("owner_id", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("start_date <= end_date", name="valid_date_range"),
        sa.PrimaryKeyConstraint("id", name="pk_trips"),
    )
    op.create_index("ix_trips_owner_id", "trips", ["owner_id"], unique=False)
    op.create_index("ix_trips_owner_start_date", "trips", ["owner_id", "start_date"], unique=False)

    op.create_table(
        "places",
        sa.Column("owner_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=240), nullable=False),
        sa.Column("latitude", sa.Numeric(precision=9, scale=6), nullable=True),
        sa.Column("longitude", sa.Numeric(precision=9, scale=6), nullable=True),
        sa.Column("address", sa.String(length=500), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=True),
        sa.Column("provider_place_id", sa.String(length=256), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_places"),
        sa.UniqueConstraint(
            "owner_id", "provider", "provider_place_id", name="uq_places_owner_provider_external_id"
        ),
    )
    op.create_index("ix_places_owner_id", "places", ["owner_id"], unique=False)

    op.create_table(
        "trip_days",
        sa.Column("trip_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("day_index", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["trip_id"], ["trips.id"], ondelete="CASCADE", name="fk_trip_days_trip_id_trips"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_trip_days"),
        sa.UniqueConstraint("trip_id", "day_index", name="uq_trip_days_trip_day_index"),
        sa.UniqueConstraint("trip_id", "date", name="uq_trip_days_trip_date"),
    )
    op.create_index("ix_trip_days_trip_id", "trip_days", ["trip_id"], unique=False)

    op.create_table(
        "itinerary_items",
        sa.Column("trip_day_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("place_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("item_type", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "item_type IN ('activity','food','lodging','transport','flight','note')",
            name="valid_item_type",
        ),
        sa.CheckConstraint(
            "status IN ('tentative','planned','booked','completed','cancelled')",
            name="valid_status",
        ),
        sa.CheckConstraint(
            "ends_at IS NULL OR starts_at IS NULL OR starts_at <= ends_at", name="valid_time_range"
        ),
        sa.ForeignKeyConstraint(
            ["place_id"],
            ["places.id"],
            ondelete="SET NULL",
            name="fk_itinerary_items_place_id_places",
        ),
        sa.ForeignKeyConstraint(
            ["trip_day_id"],
            ["trip_days.id"],
            ondelete="CASCADE",
            name="fk_itinerary_items_trip_day_id_trip_days",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_itinerary_items"),
    )
    op.create_index("ix_itinerary_items_place_id", "itinerary_items", ["place_id"], unique=False)
    op.create_index(
        "ix_itinerary_items_trip_day_id", "itinerary_items", ["trip_day_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_itinerary_items_trip_day_id", table_name="itinerary_items")
    op.drop_index("ix_itinerary_items_place_id", table_name="itinerary_items")
    op.drop_table("itinerary_items")
    op.drop_index("ix_trip_days_trip_id", table_name="trip_days")
    op.drop_table("trip_days")
    op.drop_index("ix_places_owner_id", table_name="places")
    op.drop_table("places")
    op.drop_index("ix_trips_owner_start_date", table_name="trips")
    op.drop_index("ix_trips_owner_id", table_name="trips")
    op.drop_table("trips")
