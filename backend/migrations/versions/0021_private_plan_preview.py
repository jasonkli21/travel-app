"""Reserve private PAS conversation bindings and original preview keys."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None

BINDINGS = "personal_ai_private_plan_bindings"
INTENTS = "personal_ai_private_plan_intents"
ANCHORS = "personal_ai_sidecar_anchors"


def upgrade() -> None:
    op.add_column(
        ANCHORS, sa.Column("lane", sa.String(24), server_default="automatic", nullable=False)
    )
    op.create_check_constraint(
        op.f("ck_personal_ai_sidecar_anchors_lane"),
        ANCHORS,
        "lane IN ('automatic', 'user_owned_plan')",
    )
    op.create_table(
        BINDINGS,
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("create_key", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column(
            "trip_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("trips.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("issuer", sa.String(255), nullable=False),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("target_id", sa.String(200), nullable=False),
        sa.Column("client_namespace", sa.String(200), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{ANCHORS}.conversation_id", ondelete="CASCADE"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("conversation_id", name="uq_private_plan_binding_conversation"),
        sa.UniqueConstraint(
            "owner_id", "target_id", "client_namespace", "create_key",
            name="uq_private_plan_create_scope",
        ),
        sa.UniqueConstraint(
            "owner_id", "trip_id", "target_id", "client_namespace",
            name="uq_private_plan_binding_scope",
        ),
    )
    op.create_table(
        INTENTS,
        sa.Column(
            "binding_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{BINDINGS}.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("original_key", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("source_revision", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("preview_digest", sa.String(64)),
        sa.Column("preview_expires_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "source_revision >= 0", name=op.f("ck_personal_ai_private_plan_intents_source_revision")
        ),
        sa.CheckConstraint(
            "(state = 'preview_ready' AND preview_digest IS NOT NULL "
            "AND preview_expires_at IS NOT NULL) OR "
            "(state IN ('preview_pending', 'preview_unknown') AND preview_digest IS NULL "
            "AND preview_expires_at IS NULL)",
            name=op.f("ck_personal_ai_private_plan_intents_state"),
        ),
    )
    op.execute("""CREATE FUNCTION enforce_travel_private_plan_identity() RETURNS trigger AS $$
    DECLARE identity_changed boolean;
    BEGIN
      IF TG_TABLE_NAME = 'personal_ai_sidecar_anchors' THEN
        identity_changed := NEW.lane IS DISTINCT FROM OLD.lane;
      ELSIF TG_TABLE_NAME = 'personal_ai_private_plan_bindings' THEN
        identity_changed := (to_jsonb(NEW) - 'conversation_id')
                             IS DISTINCT FROM (to_jsonb(OLD) - 'conversation_id')
          OR (OLD.conversation_id IS NOT NULL
              AND NEW.conversation_id IS DISTINCT FROM OLD.conversation_id);
      ELSE
        identity_changed := (to_jsonb(NEW) - ARRAY['state','preview_digest','preview_expires_at'])
          IS DISTINCT FROM (to_jsonb(OLD) - ARRAY['state','preview_digest','preview_expires_at']);
      END IF;
      IF identity_changed THEN
        RAISE EXCEPTION 'private plan identity is immutable' USING ERRCODE = '23514',
          CONSTRAINT = 'ck_travel_private_plan_identity_immutable';
      END IF;
      RETURN NEW;
    END; $$ LANGUAGE plpgsql""")
    for table in (ANCHORS, BINDINGS, INTENTS):
        op.execute(
            f"CREATE TRIGGER trg_{table}_immutable BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION enforce_travel_private_plan_identity()"
        )
    for table in (BINDINGS, INTENTS):
        op.execute(
            f"CREATE TRIGGER trg_{table}_owner_erasure BEFORE INSERT OR UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION enforce_travel_owner_write()"
        )


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text("SELECT 1 FROM personal_ai_sidecar_anchors WHERE lane = 'user_owned_plan' LIMIT 1")
    ) or op.get_bind().scalar(sa.text(f"SELECT 1 FROM {BINDINGS} LIMIT 1")):
        raise RuntimeError("Cannot downgrade retained private plan identities")
    op.drop_table(INTENTS)
    op.drop_table(BINDINGS)
    op.execute(f"DROP TRIGGER trg_{ANCHORS}_immutable ON {ANCHORS}")
    op.execute("DROP FUNCTION enforce_travel_private_plan_identity()")
    op.drop_constraint(op.f("ck_personal_ai_sidecar_anchors_lane"), ANCHORS, type_="check")
    op.drop_column(ANCHORS, "lane")
