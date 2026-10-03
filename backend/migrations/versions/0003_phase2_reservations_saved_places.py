"""Add Phase 2 reservations and saved places.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("places", sa.Column("category", sa.String(length=120), nullable=True))
    op.add_column("places", sa.Column("phone", sa.String(length=64), nullable=True))
    op.add_column("places", sa.Column("website_url", sa.String(length=500), nullable=True))

    op.create_table(
        "reservations",
        sa.Column("owner_id", sa.String(length=128), nullable=False),
        sa.Column("trip_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reservation_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("provider_name", sa.String(length=200), nullable=False),
        sa.Column("confirmation_code", sa.String(length=160), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("place_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_reference", sa.String(length=500), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
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
            "reservation_type IN ("
            "'lodging','flight','train','car_rental','activity','dining','other')",
            name="ck_reservations_valid_reservation_type",
        ),
        sa.CheckConstraint(
            "status IN ('tentative','confirmed','cancelled')",
            name="ck_reservations_valid_reservation_status",
        ),
        sa.CheckConstraint(
            "ends_at IS NULL OR starts_at IS NULL OR starts_at <= ends_at",
            name="ck_reservations_valid_reservation_time_range",
        ),
        sa.ForeignKeyConstraint(
            ["place_id"],
            ["places.id"],
            ondelete="SET NULL",
            name="fk_reservations_place_id_places",
        ),
        sa.ForeignKeyConstraint(
            ["trip_id"],
            ["trips.id"],
            ondelete="CASCADE",
            name="fk_reservations_trip_id_trips",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_reservations"),
    )
    op.create_index("ix_reservations_owner_id", "reservations", ["owner_id"], unique=False)
    op.create_index("ix_reservations_trip_id", "reservations", ["trip_id"], unique=False)
    op.create_index("ix_reservations_place_id", "reservations", ["place_id"], unique=False)
    op.create_index(
        "ix_reservations_owner_trip_start",
        "reservations",
        ["owner_id", "trip_id", "starts_at"],
        unique=False,
    )

    op.add_column(
        "itinerary_items",
        sa.Column("reservation_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "ix_itinerary_items_reservation_id", "itinerary_items", ["reservation_id"], unique=False
    )
    op.create_foreign_key(
        "fk_itinerary_items_reservation_id_reservations",
        "itinerary_items",
        "reservations",
        ["reservation_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "saved_places",
        sa.Column("owner_id", sa.String(length=128), nullable=False),
        sa.Column("trip_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("place_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
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
            ["place_id"],
            ["places.id"],
            ondelete="CASCADE",
            name="fk_saved_places_place_id_places",
        ),
        sa.ForeignKeyConstraint(
            ["trip_id"],
            ["trips.id"],
            ondelete="CASCADE",
            name="fk_saved_places_trip_id_trips",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_saved_places"),
        sa.UniqueConstraint(
            "owner_id", "trip_id", "place_id", name="uq_saved_places_owner_trip_place"
        ),
    )
    op.create_index("ix_saved_places_owner_id", "saved_places", ["owner_id"], unique=False)
    op.create_index("ix_saved_places_trip_id", "saved_places", ["trip_id"], unique=False)
    op.create_index("ix_saved_places_place_id", "saved_places", ["place_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_saved_places_place_id", table_name="saved_places")
    op.drop_index("ix_saved_places_trip_id", table_name="saved_places")
    op.drop_index("ix_saved_places_owner_id", table_name="saved_places")
    op.drop_table("saved_places")

    op.drop_constraint(
        "fk_itinerary_items_reservation_id_reservations",
        "itinerary_items",
        type_="foreignkey",
    )
    op.drop_index("ix_itinerary_items_reservation_id", table_name="itinerary_items")
    op.drop_column("itinerary_items", "reservation_id")

    op.drop_index("ix_reservations_owner_trip_start", table_name="reservations")
    op.drop_index("ix_reservations_place_id", table_name="reservations")
    op.drop_index("ix_reservations_trip_id", table_name="reservations")
    op.drop_index("ix_reservations_owner_id", table_name="reservations")
    op.drop_table("reservations")

    op.drop_column("places", "website_url")
    op.drop_column("places", "phone")
    op.drop_column("places", "category")
