"""Claim, complete or release an idempotency key. The unique key does the locking.

Flow for a write: `claim` → (Odoo call) → `complete` on success, `release` on an Odoo error.
A second request with the same key gets the stored response once the first completed, a 409
while it is still running, and a 422 if it carries a different request body.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from odoo_gateway.idempotency.models import DONE, IN_PROGRESS, IdempotencyKey


class IdempotencyMismatchError(Exception):
    """The key was already used by this caller for a different request."""


class IdempotencyInProgressError(Exception):
    """A request with this key is running (or died before completing); try again later."""


@dataclass(frozen=True, slots=True)
class Claim:
    actor_subject: str
    key: str


@dataclass(frozen=True, slots=True)
class Replay:
    status: int
    body: dict[str, Any]


@dataclass(frozen=True, slots=True)
class StuckClaim:
    """A claim still in progress long after it was made: the request died mid-flight."""

    actor_subject: str
    key: str
    action: str
    created_at: datetime


def _aware(moment: datetime) -> datetime:
    """SQLite hands back naive datetimes; everything stored here is UTC."""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def fingerprint(action: str, values: Mapping[str, Any]) -> str:
    canonical = json.dumps({"action": action, "values": values}, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


class IdempotencyStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def claim(
        self, *, actor_subject: str, key: str, action: str, values: Mapping[str, Any]
    ) -> Claim | Replay:
        """Insert first: the primary key makes concurrent claims of one key lose cleanly."""
        digest = fingerprint(action, values)
        row = IdempotencyKey(
            actor_subject=actor_subject,
            key=key,
            action=action,
            request_hash=digest,
            state=IN_PROGRESS,
            created_at=datetime.now(UTC),
        )
        try:
            async with self._sessions.begin() as session:
                session.add(row)
        except IntegrityError:
            return await self._existing(actor_subject, key, digest)
        return Claim(actor_subject, key)

    async def _existing(self, actor_subject: str, key: str, digest: str) -> Replay:
        async with self._sessions() as session:
            existing = await session.get(
                IdempotencyKey, {"actor_subject": actor_subject, "key": key}
            )
        if existing is None:  # released between our insert and this read: the caller retries
            raise IdempotencyInProgressError
        if existing.request_hash != digest:
            raise IdempotencyMismatchError
        if existing.state != DONE or existing.response_status is None:
            raise IdempotencyInProgressError
        return Replay(existing.response_status, existing.response_body or {})

    async def complete(self, claim: Claim, *, status: int, body: Mapping[str, Any]) -> None:
        statement = (
            update(IdempotencyKey)
            .where(
                IdempotencyKey.actor_subject == claim.actor_subject,
                IdempotencyKey.key == claim.key,
            )
            .values(
                state=DONE,
                response_status=status,
                response_body=dict(body),
                completed_at=datetime.now(UTC),
            )
        )
        async with self._sessions.begin() as session:
            await session.execute(statement)

    async def stuck(self, *, older_than: timedelta) -> list[StuckClaim]:
        """Claims in progress since before `now - older_than`, oldest first."""
        cutoff = datetime.now(UTC) - older_than
        query = (
            select(IdempotencyKey)
            .where(IdempotencyKey.state == IN_PROGRESS, IdempotencyKey.created_at < cutoff)
            .order_by(IdempotencyKey.created_at)
        )
        async with self._sessions() as session:
            rows = (await session.scalars(query)).all()
        return [
            StuckClaim(
                actor_subject=row.actor_subject,
                key=row.key,
                action=row.action,
                created_at=_aware(row.created_at),
            )
            for row in rows
        ]

    async def in_progress(self, *, actor_subject: str, key: str) -> Claim | None:
        async with self._sessions() as session:
            row = await session.get(IdempotencyKey, {"actor_subject": actor_subject, "key": key})
        if row is None or row.state != IN_PROGRESS:
            return None
        return Claim(actor_subject, key)

    async def release(self, claim: Claim) -> None:
        """The Odoo call failed with a known error: let the caller retry with the same key."""
        statement = delete(IdempotencyKey).where(
            IdempotencyKey.actor_subject == claim.actor_subject,
            IdempotencyKey.key == claim.key,
            IdempotencyKey.state == IN_PROGRESS,
        )
        async with self._sessions.begin() as session:
            await session.execute(statement)
