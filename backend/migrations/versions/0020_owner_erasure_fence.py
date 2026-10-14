"""Retain an owner erasure marker and serialize canonical writes with its freeze."""

import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None
FENCED = (
    "trips",
    "trip_days",
    "itinerary_items",
    "places",
    "reservations",
    "saved_places",
    "source_attachments",
    "booking_imports",
    "itinerary_proposals",
    "personal_ai_sidecar_anchors",
    "delegated_source_commands",
)


def upgrade() -> None:
    op.create_table(
        "owner_erasure_ledger",
        sa.Column("owner_id", sa.String(128), primary_key=True),
        sa.Column(
            "requested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "length(owner_id) BETWEEN 1 AND 128", name=op.f("ck_owner_erasure_ledger_owner_length")
        ),
    )
    op.execute("""CREATE FUNCTION enforce_travel_owner_write() RETURNS trigger AS $$
    DECLARE target_owner text; old_owner text; identity_status text;
    BEGIN
      IF TG_TABLE_NAME = 'trip_days' THEN
        SELECT owner_id INTO target_owner FROM trips WHERE id = NEW.trip_id;
        IF TG_OP = 'UPDATE' THEN
          SELECT owner_id INTO old_owner FROM trips WHERE id = OLD.trip_id;
        END IF;
      ELSIF TG_TABLE_NAME = 'itinerary_items' THEN
        SELECT t.owner_id INTO target_owner FROM trips t JOIN trip_days d ON d.trip_id = t.id
          WHERE d.id = NEW.trip_day_id;
        IF TG_OP = 'UPDATE' THEN
          SELECT t.owner_id INTO old_owner FROM trips t
            JOIN trip_days d ON d.trip_id = t.id WHERE d.id = OLD.trip_day_id;
        END IF;
      ELSE
        target_owner := NEW.owner_id;
        IF TG_OP = 'UPDATE' THEN old_owner := OLD.owner_id; END IF;
      END IF;
      IF EXISTS (SELECT 1 FROM owner_erasure_ledger
                   WHERE owner_id IN (target_owner, old_owner)) THEN
        RAISE EXCEPTION 'owner is frozen' USING ERRCODE = '23514',
          CONSTRAINT = 'ck_travel_owner_writable';
      END IF;
      PERFORM owner_id FROM auth_identities
        WHERE owner_id IN (target_owner, old_owner) ORDER BY owner_id FOR SHARE;
      SELECT 'disabled' INTO identity_status FROM auth_identities
        WHERE owner_id IN (target_owner, old_owner) AND status = 'disabled' LIMIT 1;
      IF identity_status = 'disabled' OR EXISTS (SELECT 1 FROM owner_erasure_ledger
                       WHERE owner_id IN (target_owner, old_owner)) THEN
        RAISE EXCEPTION 'owner is frozen' USING ERRCODE = '23514',
          CONSTRAINT = 'ck_travel_owner_writable';
      END IF;
      RETURN NEW;
    END; $$ LANGUAGE plpgsql""")
    for table in FENCED:
        op.execute(
            f"CREATE TRIGGER trg_{table}_owner_erasure BEFORE INSERT OR UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION enforce_travel_owner_write()"
        )


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT 1 FROM owner_erasure_ledger LIMIT 1")):
        raise RuntimeError("Cannot remove a retained erasure fence")
    for table in FENCED:
        op.execute(f"DROP TRIGGER trg_{table}_owner_erasure ON {table}")
    op.execute("DROP FUNCTION enforce_travel_owner_write()")
    op.drop_table("owner_erasure_ledger")
