"""Idempotency keys for writes: claimed before the Odoo call, completed after (ADR-0004).

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "idempotency_keys",
        sa.Column("actor_subject", sa.String(length=128), nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column(
            "response_body",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("state IN ('in_progress', 'done')", name="idempotency_keys_state_valid"),
        sa.PrimaryKeyConstraint("actor_subject", "key"),
    )
    op.create_index(
        op.f("ix_idempotency_keys_created_at"), "idempotency_keys", ["created_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_idempotency_keys_created_at"), table_name="idempotency_keys")
    op.drop_table("idempotency_keys")
