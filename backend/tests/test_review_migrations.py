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
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0012"
        assert connection.scalar(text("SELECT count(*) FROM itinerary_items")) == 0


def test_clean_full_migration_round_trip(database_engine: Engine) -> None:
    with database_engine.begin() as connection:
        config = config_for(connection)
        command.downgrade(config, "base")
        command.upgrade(config, "head")
        command.check(config)


def test_source_lifecycle_downgrade_preserves_detached_import_outcomes(
    database_engine: Engine,
) -> None:
    trip_id, source_id, import_id = [str(uuid4()) for _ in range(3)]
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO trips (id,owner_id,title,start_date,end_date,timezone) "
                "VALUES (:id,'local','Source lifecycle','2026-10-04','2026-10-04','UTC')"
            ),
            {"id": trip_id},
        )
        connection.execute(
            text(
                "INSERT INTO source_attachments "
                "(id,owner_id,trip_id,object_key,sha256,media_type,byte_size,state,expires_at) "
                "VALUES (:id,'local',:trip,:key,:hash,'text/plain',1,'ready',"
                "now() + interval '1 day')"
            ),
            {"id": source_id, "trip": trip_id, "key": "a" * 32, "hash": "1" * 64},
        )
        connection.execute(
            text(
                "INSERT INTO booking_imports "
                "(id,owner_id,trip_id,source_id,request_key,request_fingerprint,source_sha256,"
                "source_media_type,source_byte_size,state,parser_version,review_revision) "
                "VALUES (:id,'local',:trip,:source,'request_001',:fingerprint,:hash,"
                "'text/plain',1,'received','source-v1',0)"
            ),
            {
                "id": import_id,
                "trip": trip_id,
                "source": source_id,
                "fingerprint": "2" * 64,
                "hash": "1" * 64,
            },
        )
        connection.execute(text("DELETE FROM source_attachments WHERE id=:id"), {"id": source_id})
        config = config_for(connection)
        with pytest.raises(RuntimeError, match="imports are detached from deleted sources"):
            command.downgrade(config, "0010")
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0011"
        row = connection.execute(
            text(
                "SELECT source_id,request_key,source_sha256,source_media_type,source_byte_size "
                "FROM booking_imports WHERE id=:id"
            ),
            {"id": import_id},
        ).one()
        assert row.source_id is None
        assert row.request_key == "request_001"
        assert row.source_sha256 == "1" * 64
        assert row.source_media_type == "text/plain" and row.source_byte_size == 1


def test_revision_migration_initializes_populated_legacy_rows(database_engine: Engine) -> None:
    trip_id, place_id = str(uuid4()), str(uuid4())
    with database_engine.begin() as connection:
        config = config_for(connection)
        command.downgrade(config, "0005")
        connection.execute(
            text(
                "INSERT INTO trips (id,owner_id,title,start_date,end_date,timezone) "
                "VALUES (:id,'local','Existing trip','2026-10-03','2026-10-03','UTC')"
            ),
            {"id": trip_id},
        )
        connection.execute(
            text("INSERT INTO places (id,owner_id,name) VALUES (:id,'local','Existing place')"),
            {"id": place_id},
        )
        command.upgrade(config, "head")
        trip_revision = connection.scalar(
            text("SELECT revision FROM trips WHERE id=:id"), {"id": trip_id}
        )
        place_revision = connection.scalar(
            text("SELECT revision FROM places WHERE id=:id"), {"id": place_id}
        )
        assert trip_revision == 0
        assert place_revision == 0
        command.check(config)


def test_proposal_provenance_migration_backfills_applied_0007_rows(database_engine: Engine) -> None:
    trip_id, proposal_id = str(uuid4()), str(uuid4())
    created_at = datetime(2026, 10, 4, 12, tzinfo=UTC)
    with database_engine.begin() as connection:
        config = config_for(connection)
        command.downgrade(config, "0006")
        connection.execute(
            text(
                "INSERT INTO trips (id,owner_id,title,start_date,end_date,timezone) "
                "VALUES (:id,'local','Legacy proposal trip','2026-10-04','2026-10-04','UTC')"
            ),
            {"id": trip_id},
        )
        command.upgrade(config, "0007")
        connection.execute(
            text(
                "INSERT INTO itinerary_proposals "
                "(id,owner_id,trip_id,idempotency_key,downstream_key,request_fingerprint,"
                "state,schema_version,policy_version,support_mode,trip_handle,generation_deadline,"
                "base_trip_revision,base_place_revisions,base_snapshot,citations,"
                "created_at,updated_at) "
                "VALUES (:id,'local',:trip,:key,:downstream,:fingerprint,'failed',"
                "'itinerary-proposal-v1','itinerary-proposal-policy-v2','context_only',"
                "'h_triphandle000000001',:deadline,0,'[]','{}','[]',:created,:created)"
            ),
            {
                "id": proposal_id,
                "trip": trip_id,
                "key": str(uuid4()),
                "downstream": str(uuid4()),
                "fingerprint": "0" * 64,
                "deadline": created_at,
                "created": created_at,
            },
        )
        command.upgrade(config, "head")
        row = connection.execute(
            text(
                "SELECT upstream_revision,operation_support FROM itinerary_proposals WHERE id=:id"
            ),
            {"id": proposal_id},
        ).one()
        assert row.upstream_revision == "8535cad3a146b1a19cab0958c439f170d19b8095"
        assert row.operation_support == []
        command.check(config)
