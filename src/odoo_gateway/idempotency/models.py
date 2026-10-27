"""One row per (caller, Idempotency-Key): claimed before the Odoo call, completed after it."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, DateTime, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from odoo_gateway.db import Base

IN_PROGRESS = "in_progress"
DONE = "done"

_JSON = JSON().with_variant(JSONB(), "postgresql")


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (
        CheckConstraint("state IN ('in_progress', 'done')", name="idempotency_keys_state_valid"),
    )

    # Keys are scoped per caller: two products may both start counting at "1".
    actor_subject: Mapped[str] = mapped_column(String(128), primary_key=True)
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    action: Mapped[str] = mapped_column(String(64))
    # SHA-256 of action + canonical request values: detects the same key reused for a different
    # request without storing the values themselves.
    request_hash: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(16))
    response_status: Mapped[int | None] = mapped_column(Integer())
    response_body: Mapped[dict[str, Any] | None] = mapped_column(_JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
