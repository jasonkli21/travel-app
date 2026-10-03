from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

pytestmark = pytest.mark.usefixtures("clean_database")


def config_for(connection: Connection) -> Config:
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "migrations"))
    config.attributes["connection"] = connection
    return config


def seed_moved_item(connection: Connection, *, date: str, time: str) -> str:
    trip_id, day_id, item_id = [str(uuid4()) for _ in range(3)]
    connection.execute(
        text(
            "INSERT INTO trips (id,owner_id,title,start_date,end_date,timezone) "
            "VALUES (:id,'local','Legacy',:date,:date,'America/New_York')"
        ),
        {"id": trip_id, "date": date},
    )
    connection.execute(
        text("INSERT INTO trip_days (id,trip_id,day_index,date) VALUES (:id,:trip,1,:date)"),
        {"id": day_id, "trip": trip_id, "date": date},
    )
    connection.execute(
        text(
            "INSERT INTO itinerary_items "
            "(id,trip_day_id,item_type,title,sort_order,status,starts_at) "
            "VALUES (:id,:day,'activity','Moved',4,'planned',:time)"
        ),
        {"id": item_id, "day": day_id, "time": time},
    )
    return item_id


def test_migration_repairs_legacy_moved_dates_and_order(database_engine: Engine) -> None:
    with database_engine.begin() as connection:
        config = config_for(connection)
        command.downgrade(config, "0004")
        item_id = seed_moved_item(connection, date="2026-03-09", time="2026-03-07T14:00:00Z")
        command.upgrade(config, "head")
        row = connection.execute(
            text("SELECT starts_at,sort_order FROM itinerary_items WHERE id=:id"), {"id": item_id}
        ).one()
        assert row.starts_at == datetime(2026, 3, 9, 13, tzinfo=UTC)
        assert row.sort_order == 0
        command.check(config)


def test_invalid_dst_repair_aborts_and_can_be_recovered(database_engine: Engine) -> None:
    # Roll back the whole failed upgrade, including data repairs and the revision.
    with (
        pytest.raises(RuntimeError, match="ambiguous local time"),
        database_engine.begin() as connection,
    ):
        config = config_for(connection)
        command.downgrade(config, "0004")
        seed_moved_item(connection, date="2026-03-08", time="2026-03-07T07:30:00Z")
        command.upgrade(config, "head")
    with database_engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0005"
        assert connection.scalar(text("SELECT count(*) FROM itinerary_items")) == 0


def test_clean_full_migration_round_trip(database_engine: Engine) -> None:
    with database_engine.begin() as connection:
        config = config_for(connection)
        command.downgrade(config, "base")
        command.upgrade(config, "head")
        command.check(config)
