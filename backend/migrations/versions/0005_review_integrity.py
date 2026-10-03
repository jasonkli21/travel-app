"""Repair moved item dates and enforce place/order integrity.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-03

The original cross-day move kept timestamps on the source date. Preserve each
endpoint's local wall-clock time on its owning day. Abort atomically if a repair
would require choosing a DST fold/gap. Back up before applying: downgrade removes
constraints but cannot undo corrected data.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from alembic import context, op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError("Migration 0005 requires online inspection and repair of existing data.")
    connection = op.get_bind()
    invalid_places = connection.scalar(
        sa.text(
            "SELECT count(*) FROM places WHERE (latitude IS NULL) <> (longitude IS NULL) "
            "OR NOT (latitude >= -90 AND latitude <= 90) "
            "OR NOT (longitude >= -180 AND longitude <= 180)"
        )
    )
    if invalid_places:
        raise RuntimeError(
            "Invalid existing place coordinates; correct the paired coordinates before migration."
        )

    items = sa.table(
        "itinerary_items",
        sa.column("id", sa.Uuid()),
        sa.column("starts_at", sa.DateTime(timezone=True)),
        sa.column("ends_at", sa.DateTime(timezone=True)),
        sa.column("sort_order", sa.Integer()),
    )
    rows = connection.execute(
        sa.text(
            "SELECT i.id, i.trip_day_id, i.starts_at, i.ends_at, d.date, t.timezone "
            "FROM itinerary_items i JOIN trip_days d ON d.id = i.trip_day_id "
            "JOIN trips t ON t.id = d.trip_id "
            "ORDER BY i.trip_day_id, i.sort_order, i.created_at, i.id"
        )
    ).mappings()
    previous_day = None
    position = 0
    for row in rows:
        if row["trip_day_id"] != previous_day:
            position = 0
            previous_day = row["trip_day_id"]
        values: dict[str, object] = {"sort_order": position}
        zone = ZoneInfo(row["timezone"])
        for field in ("starts_at", "ends_at"):
            instant = row[field]
            if instant is None:
                continue
            local = instant.astimezone(zone)
            if local.date() == row["date"]:
                continue
            naive = datetime.combine(row["date"], local.time().replace(tzinfo=None))
            candidates = []
            for fold in (0, 1):
                candidate = naive.replace(tzinfo=zone, fold=fold)
                if candidate.astimezone(UTC).astimezone(zone).replace(tzinfo=None) == naive:
                    candidates.append(candidate)
            if not candidates or (
                len(candidates) == 2 and candidates[0].utcoffset() != candidates[1].utcoffset()
            ):
                raise RuntimeError(
                    "Moved item requires an invalid or ambiguous local time; "
                    "correct its schedule before retrying migration 0005."
                )
            values[field] = candidates[0]
        connection.execute(sa.update(items).where(items.c.id == row["id"]).values(**values))
        position += 1

    op.create_unique_constraint(
        "uq_itinerary_items_day_order", "itinerary_items", ["trip_day_id", "sort_order"]
    )
    op.create_check_constraint(
        "valid_coordinate_pair", "places", "(latitude IS NULL) = (longitude IS NULL)"
    )
    op.create_check_constraint("valid_latitude", "places", "latitude >= -90 AND latitude <= 90")
    op.create_check_constraint(
        "valid_longitude", "places", "longitude >= -180 AND longitude <= 180"
    )


def downgrade() -> None:
    op.drop_constraint("valid_longitude", "places", type_="check")
    op.drop_constraint("valid_latitude", "places", type_="check")
    op.drop_constraint("valid_coordinate_pair", "places", type_="check")
    op.drop_constraint("uq_itinerary_items_day_order", "itinerary_items", type_="unique")
