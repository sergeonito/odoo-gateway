"""The append-only trigger, against real PostgreSQL after `alembic upgrade head`.

Runs in CI (service container) or locally: GATEWAY_TEST_PG_URL=postgresql+asyncpg://...
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from odoo_gateway.audit.log import AuditLog
from odoo_gateway.audit.models import Outcome
from odoo_gateway.auth.rbac import Principal, Role

PG_URL = os.environ.get("GATEWAY_TEST_PG_URL", "")

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not PG_URL, reason="GATEWAY_TEST_PG_URL is not set"),
]


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE audit_events SET action = 'tampered'",
        "DELETE FROM audit_events",
        "TRUNCATE audit_events",
    ],
)
async def test_audit_rows_cannot_be_rewritten(statement: str) -> None:
    engine = create_async_engine(PG_URL)
    try:
        audit = AuditLog(async_sessionmaker(engine, expire_on_commit=False))
        await audit.record(
            principal=Principal("pg-test", "PG test", frozenset({Role.ADMIN})),
            action="test.append",
            outcome=Outcome.ALLOWED,
            request_id="pg-test-0001",
        )
        with pytest.raises(DBAPIError, match="append-only"):
            async with engine.begin() as connection:
                await connection.execute(text(statement))
    finally:
        await engine.dispose()
