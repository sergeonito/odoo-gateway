"""Allow the `attempted` audit outcome, written before the Odoo call (ADR-0004).

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-27
"""

from __future__ import annotations

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | None = None
depends_on: str | None = None

_TABLE = "audit_events"
_NAME = "audit_events_outcome_valid"


def upgrade() -> None:
    # Alembic autogenerate does not compare CHECK constraints, so this one is written by hand.
    op.drop_constraint(_NAME, _TABLE, type_="check")
    op.create_check_constraint(
        _NAME, _TABLE, "outcome IN ('attempted', 'allowed', 'denied', 'failed')"
    )


def downgrade() -> None:
    # Existing `attempted` rows would violate the old constraint; the audit table is append-only,
    # so they cannot be removed. Downgrade is therefore refused once such rows exist.
    op.drop_constraint(_NAME, _TABLE, type_="check")
    op.create_check_constraint(_NAME, _TABLE, "outcome IN ('allowed', 'denied', 'failed')")
