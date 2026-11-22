"""Keep bindings without an authenticated create receipt quarantined."""

import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None

BINDINGS = "personal_ai_private_plan_bindings"


def upgrade() -> None:
    op.add_column(BINDINGS, sa.Column("create_receipt_digest", sa.String(64)))
    op.create_check_constraint(
        op.f("ck_personal_ai_private_plan_bindings_create_receipt"),
        BINDINGS,
        "create_receipt_digest IS NULL OR "
        "(conversation_id IS NOT NULL AND create_receipt_digest ~ '^[0-9a-f]{64}$')",
    )
    op.execute(f"DROP TRIGGER trg_{BINDINGS}_immutable ON {BINDINGS}")
    op.execute("""CREATE FUNCTION enforce_travel_private_plan_create_receipt() RETURNS trigger AS $$
    BEGIN
      IF (to_jsonb(NEW) - ARRAY['conversation_id', 'create_receipt_digest'])
         IS DISTINCT FROM (to_jsonb(OLD) - ARRAY['conversation_id', 'create_receipt_digest'])
         OR (OLD.conversation_id IS NOT NULL AND
             (NEW.conversation_id, NEW.create_receipt_digest)
             IS DISTINCT FROM (OLD.conversation_id, OLD.create_receipt_digest))
         OR (OLD.conversation_id IS NULL AND NEW.conversation_id IS NOT NULL
             AND NEW.create_receipt_digest IS NULL)
      THEN
        RAISE EXCEPTION 'private plan identity is immutable' USING ERRCODE = '23514',
          CONSTRAINT = 'ck_travel_private_plan_identity_immutable';
      END IF;
      RETURN NEW;
    END; $$ LANGUAGE plpgsql""")
    op.execute(
        f"CREATE TRIGGER trg_{BINDINGS}_immutable BEFORE UPDATE ON {BINDINGS} "
        "FOR EACH ROW EXECUTE FUNCTION enforce_travel_private_plan_create_receipt()"
    )


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text("SELECT 1 FROM personal_ai_sidecar_anchors WHERE lane = 'user_owned_plan' LIMIT 1")
    ) or op.get_bind().scalar(sa.text(f"SELECT 1 FROM {BINDINGS} LIMIT 1")):
        raise RuntimeError("Cannot downgrade retained private plan identities")
    op.execute(f"DROP TRIGGER trg_{BINDINGS}_immutable ON {BINDINGS}")
    op.execute("DROP FUNCTION enforce_travel_private_plan_create_receipt()")
    op.drop_constraint(
        op.f("ck_personal_ai_private_plan_bindings_create_receipt"), BINDINGS, type_="check"
    )
    op.drop_column(BINDINGS, "create_receipt_digest")
    op.execute(
        f"CREATE TRIGGER trg_{BINDINGS}_immutable BEFORE UPDATE ON {BINDINGS} "
        "FOR EACH ROW EXECUTE FUNCTION enforce_travel_private_plan_identity()"
    )
