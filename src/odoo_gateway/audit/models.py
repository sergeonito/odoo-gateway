"""Audit log table. Append-only: on PostgreSQL a trigger rejects UPDATE, DELETE and TRUNCATE."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, BigInteger, CheckConstraint, DateTime, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from odoo_gateway.db import Base


class Outcome(StrEnum):
    ALLOWED = "allowed"  # authorised and done
    DENIED = "denied"  # authorisation refused
    FAILED = "failed"  # authorised, but the call failed


_JSON = JSON().with_variant(JSONB(), "postgresql")


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        CheckConstraint(
            "outcome IN ('allowed', 'denied', 'failed')", name="audit_events_outcome_valid"
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"), primary_key=True
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    request_id: Mapped[str] = mapped_column(String(64))
    actor_subject: Mapped[str] = mapped_column(String(128), index=True)
    actor_name: Mapped[str] = mapped_column(String(256))
    actor_roles: Mapped[list[str]] = mapped_column(_JSON)
    action: Mapped[str] = mapped_column(String(64), index=True)
    outcome: Mapped[str] = mapped_column(String(16))
    resource_model: Mapped[str | None] = mapped_column(String(64))
    resource_id: Mapped[int | None] = mapped_column(BigInteger())
    # Field names and error classes only: no personal data values (OWASP logging guidance).
    details: Mapped[dict[str, Any]] = mapped_column(_JSON)
