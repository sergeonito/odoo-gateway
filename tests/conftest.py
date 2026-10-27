"""Shared fixtures: an in-memory audit log, a fake Odoo and an app wired to both."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

import odoo_gateway.reports.models  # noqa: F401 - registers the snapshot tables on Base
from odoo_gateway.audit.log import AuditLog
from odoo_gateway.auth.rbac import Role
from odoo_gateway.auth.tokens import DevTokenVerifier
from odoo_gateway.container import Container
from odoo_gateway.db import Base
from odoo_gateway.main import create_app
from odoo_gateway.odoo.errors import OdooError
from odoo_gateway.ports import Domain

DEV_SECRET = SecretStr("x" * 48)


@dataclass
class FakeOdoo:
    """Records calls and answers with canned rows; `fail_with` makes the next call raise."""

    rows: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    next_id: int = 100
    fail_with: OdooError | None = None
    calls: list[tuple[str, str, Any]] = field(default_factory=list)

    async def search_read(
        self,
        model: str,
        domain: Domain,
        fields: list[str],
        *,
        limit: int,
        offset: int = 0,
        order: str | None = None,
    ) -> list[dict[str, Any]]:
        self.calls.append((model, "search_read", domain))
        self._maybe_fail()
        return self.rows.get(model, [])[offset : offset + limit]

    async def create(self, model: str, values: dict[str, Any]) -> int:
        self.calls.append((model, "create", values))
        self._maybe_fail()
        self.next_id += 1
        return self.next_id

    def _maybe_fail(self) -> None:
        if self.fail_with is not None:
            error, self.fail_with = self.fail_with, None
            raise error


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
def audit(engine: AsyncEngine) -> AuditLog:
    return AuditLog(async_sessionmaker(engine, expire_on_commit=False))


@pytest.fixture
def odoo() -> FakeOdoo:
    return FakeOdoo()


@pytest.fixture
def verifier() -> DevTokenVerifier:
    return DevTokenVerifier(DEV_SECRET, issuer="test", audience="odoo-gateway")


@pytest.fixture
async def client(
    odoo: FakeOdoo, audit: AuditLog, verifier: DevTokenVerifier
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(container=Container(odoo=odoo, audit=audit, verifier=verifier))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://gateway") as http:
        yield http


@pytest.fixture
def auth(verifier: DevTokenVerifier) -> Any:
    """`auth(Role.VIEWER)` → headers with a bearer token for that role."""

    def headers(*roles: Role, sub: str = "user-1") -> dict[str, str]:
        return {"Authorization": f"Bearer {verifier.issue(sub, roles)}"}

    return headers
