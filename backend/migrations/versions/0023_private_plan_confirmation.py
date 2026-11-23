"""Add an immutable original-key submission marker to private Travel plans."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None

TABLE = "personal_ai_private_plan_confirmations"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column(
            "binding_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("personal_ai_private_plan_intents.binding_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("original_key", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("root", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("binding_digest", sa.String(64), nullable=False),
        sa.Column("body", sa.LargeBinary(), nullable=False),
        sa.Column("body_digest", sa.String(64), nullable=False),
        sa.Column("confirmation_digest", sa.String(64), nullable=False),
        sa.Column("create_receipt_digest", sa.String(64), nullable=False),
        sa.Column("preview_receipt_digest", sa.String(64), nullable=False),
        sa.Column(
            "submitted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("quarantined_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("attempt", name="uq_private_plan_confirmation_attempt"),
        sa.UniqueConstraint("root", name="uq_private_plan_confirmation_root"),
        sa.CheckConstraint(
            "octet_length(body) BETWEEN 1 AND 512 AND "
            "binding_digest ~ '^[0-9a-f]{64}$' AND body_digest ~ '^[0-9a-f]{64}$' AND "
            "confirmation_digest ~ '^[0-9a-f]{64}$' AND "
            "create_receipt_digest ~ '^[0-9a-f]{64}$' AND "
            "preview_receipt_digest ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_personal_ai_private_plan_confirmations_wire"),
        ),
    )
    op.execute("""
        CREATE FUNCTION enforce_travel_private_plan_confirmation() RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'UPDATE' THEN
            IF (to_jsonb(NEW) - 'quarantined_at') IS DISTINCT FROM
               (to_jsonb(OLD) - 'quarantined_at')
               OR OLD.quarantined_at IS NOT NULL
               AND NEW.quarantined_at IS DISTINCT FROM OLD.quarantined_at
            THEN
              RAISE EXCEPTION 'private plan submission is immutable' USING ERRCODE = '23514';
            END IF;
          ELSIF NOT EXISTS (
            SELECT 1 FROM personal_ai_private_plan_intents i
            JOIN personal_ai_private_plan_bindings b ON b.id = i.binding_id
            WHERE i.binding_id = NEW.binding_id AND i.owner_id = NEW.owner_id
              AND i.original_key = NEW.original_key AND i.state = 'preview_ready'
              AND b.create_receipt_digest IS NOT NULL
          ) THEN
            RAISE EXCEPTION 'private plan preview is unavailable' USING ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END; $$ LANGUAGE plpgsql
    """)
    op.execute(
        f"CREATE TRIGGER trg_{TABLE}_immutable BEFORE INSERT OR UPDATE ON {TABLE} "
        "FOR EACH ROW EXECUTE FUNCTION enforce_travel_private_plan_confirmation()"
    )
    op.execute(
        f"CREATE TRIGGER trg_{TABLE}_owner_erasure BEFORE INSERT OR UPDATE ON {TABLE} "
        "FOR EACH ROW EXECUTE FUNCTION enforce_travel_owner_write()"
    )


def downgrade() -> None:
    if op.get_bind().scalar(sa.text(f"SELECT 1 FROM {TABLE} LIMIT 1")):
        raise RuntimeError("Cannot downgrade retained private plan submissions")
    op.drop_table(TABLE)
    op.execute("DROP FUNCTION enforce_travel_private_plan_confirmation()")
