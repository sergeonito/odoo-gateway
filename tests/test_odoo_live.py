"""Smoke tests against the compose stack: `make up`, `make odoo-setup`, `make odoo-apikey`, then
`make test-live`.

They build everything from `.env`, so they exercise the same integration user, API key and
audit database the gateway itself uses.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import httpx
import pytest

from odoo_gateway.auth.rbac import Role
from odoo_gateway.auth.tokens import DevTokenVerifier
from odoo_gateway.config import Settings
from odoo_gateway.main import create_app
from odoo_gateway.odoo.client import OdooClient
from odoo_gateway.reports.jobs import take_stock_snapshot

pytestmark = [
    pytest.mark.odoo_live,
    pytest.mark.skipif(os.environ.get("GATEWAY_ODOO_LIVE") != "1", reason="no live Odoo"),
]

SEEDED_REF = "DRN-FR7"  # scripts/setup_odoo.py
SEEDED_QTY = 120.0


@pytest.fixture
def settings() -> Settings:
    return Settings()


@pytest.fixture
async def live(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    """The real app, container built by the lifespan from `.env`, served in-process."""
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://gateway") as http:
            yield http


@pytest.fixture
def token(settings: Settings) -> DevTokenVerifier:
    return DevTokenVerifier(
        settings.dev_jwt_secret, issuer=settings.dev_jwt_issuer, audience=settings.jwt_audience
    )


def _bearer(verifier: DevTokenVerifier, role: Role) -> dict[str, str]:
    return {"Authorization": f"Bearer {verifier.issue('live-test', [role])}"}


async def test_client_reads_seeded_product_with_the_integration_user(settings: Settings) -> None:
    client = OdooClient(
        base_url=settings.odoo_url,
        db=settings.odoo_db,
        login=settings.odoo_login,
        api_key=settings.odoo_api_key,
    )
    try:
        [product] = await client.search_read(
            "product.product",
            [("default_code", "=", SEEDED_REF)],
            ["name", "qty_available"],
            limit=1,
        )
    finally:
        await client.aclose()
    assert product["qty_available"] == SEEDED_QTY


async def test_app_reads_stock_and_creates_partner_end_to_end(
    live: httpx.AsyncClient, token: DevTokenVerifier
) -> None:
    products = await live.get(
        "/products", params={"search": "Frame"}, headers=_bearer(token, Role.VIEWER)
    )
    assert products.status_code == 200, products.text
    [frame] = [p for p in products.json() if p["internal_ref"] == SEEDED_REF]
    assert frame["qty_available"] == SEEDED_QTY

    stock = await live.get(f"/products/{frame['id']}/stock", headers=_bearer(token, Role.VIEWER))
    assert stock.status_code == 200, stock.text
    assert stock.json() == [{"location": "WH/Stock", "quantity": SEEDED_QTY, "reserved": 0.0}]

    created = await live.post(
        "/partners",
        json={"name": "Live smoke partner", "is_company": True},
        headers={**_bearer(token, Role.OPERATOR), "X-Request-ID": "live-smoke-0001"},
    )
    assert created.status_code == 201, created.text

    audit = await live.get("/audit", params={"limit": 1}, headers=_bearer(token, Role.ADMIN))
    assert audit.status_code == 200, audit.text
    [event] = audit.json()
    assert (event["action"], event["outcome"], event["resource_id"]) == (
        "partners.create",
        "allowed",
        created.json()["id"],
    )
    assert event["request_id"] == "live-smoke-0001"


async def test_stock_report_serves_the_snapshot_just_taken(
    settings: Settings, live: httpx.AsyncClient, token: DevTokenVerifier
) -> None:
    snapshot = await take_stock_snapshot(settings)

    report = await live.get("/reports/stock", headers=_bearer(token, Role.VIEWER))
    assert report.status_code == 200, report.text
    body = report.json()
    assert body["product_count"] == snapshot.product_count
    [frame] = [line for line in body["lines"] if line["internal_ref"] == SEEDED_REF]
    assert frame["qty_available"] == SEEDED_QTY
