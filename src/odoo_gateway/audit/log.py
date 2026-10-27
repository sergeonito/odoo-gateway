"""Writing and reading the audit log."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from odoo_gateway.audit.models import AuditEvent, Outcome
from odoo_gateway.auth.rbac import Principal


class AuditLog:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def record(
        self,
        *,
        principal: Principal,
        action: str,
        outcome: Outcome,
        request_id: str,
        resource_model: str | None = None,
        resource_id: int | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        """Commit one event. Failures propagate: an action we cannot audit is not hidden."""
        event = AuditEvent(
            occurred_at=datetime.now(UTC),
            request_id=request_id,
            actor_subject=principal.subject,
            actor_name=principal.display_name,
            actor_roles=sorted(role.value for role in principal.roles),
            action=action,
            outcome=outcome.value,
            resource_model=resource_model,
            resource_id=resource_id,
            details=dict(details or {}),
        )
        async with self._sessions.begin() as session:
            session.add(event)

    async def attempt_for_key(
        self, *, actor_subject: str, idempotency_key: str
    ) -> AuditEvent | None:
        """The `attempted` event of a claimed key: its request id leads to the application logs."""
        query = (
            select(AuditEvent)
            .where(
                AuditEvent.actor_subject == actor_subject,
                AuditEvent.outcome == Outcome.ATTEMPTED.value,
                AuditEvent.details["idempotency_key"].as_string() == idempotency_key,
            )
            .order_by(AuditEvent.id.desc())
            .limit(1)
        )
        async with self._sessions() as session:
            return await session.scalar(query)

    async def recent(
        self,
        *,
        limit: int,
        actor: str | None = None,
        action: str | None = None,
    ) -> Sequence[AuditEvent]:
        query = select(AuditEvent).order_by(AuditEvent.id.desc()).limit(limit)
        if actor:
            query = query.where(AuditEvent.actor_subject == actor)
        if action:
            query = query.where(AuditEvent.action == action)
        async with self._sessions() as session:
            return (await session.scalars(query)).all()
