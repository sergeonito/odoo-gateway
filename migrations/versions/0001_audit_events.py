"""Append-only audit log.

Revision ID: 0001
Revises:
Create Date: 2026-10-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            primary_key=True,
        ),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column("actor_subject", sa.String(128), nullable=False),
        sa.Column("actor_name", sa.String(256), nullable=False),
        sa.Column("actor_roles", _JSON, nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("resource_model", sa.String(64), nullable=True),
        sa.Column("resource_id", sa.BigInteger(), nullable=True),
        sa.Column("details", _JSON, nullable=False),
        sa.CheckConstraint(
            "outcome IN ('allowed', 'denied', 'failed')", name="audit_events_outcome_valid"
        ),
    )
    op.create_index("ix_audit_events_occurred_at", "audit_events", ["occurred_at"])
    op.create_index("ix_audit_events_actor_subject", "audit_events", ["actor_subject"])
    op.create_index("ix_audit_events_action", "audit_events", ["action"])

    if op.get_bind().dialect.name == "postgresql":
        # Append-only at the database level: the application role cannot rewrite history.
        op.execute(
            """
            CREATE FUNCTION audit_events_reject_change() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'audit_events is append-only (% rejected)', TG_OP;
            END
            $$;
            """
        )
        op.execute(
            "CREATE TRIGGER audit_events_no_update_delete "
            "BEFORE UPDATE OR DELETE ON audit_events "
            "FOR EACH ROW EXECUTE FUNCTION audit_events_reject_change();"
        )
        op.execute(
            "CREATE TRIGGER audit_events_no_truncate "
            "BEFORE TRUNCATE ON audit_events "
            "FOR EACH STATEMENT EXECUTE FUNCTION audit_events_reject_change();"
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS audit_events_no_truncate ON audit_events;")
        op.execute("DROP TRIGGER IF EXISTS audit_events_no_update_delete ON audit_events;")
        op.execute("DROP FUNCTION IF EXISTS audit_events_reject_change();")
    op.drop_index("ix_audit_events_action", table_name="audit_events")
    op.drop_index("ix_audit_events_actor_subject", table_name="audit_events")
    op.drop_index("ix_audit_events_occurred_at", table_name="audit_events")
    op.drop_table("audit_events")
