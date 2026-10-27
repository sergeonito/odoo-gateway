"""HTTP behaviour end to end, with a fake Odoo and a real (SQLite) audit log."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from odoo_gateway.audit.log import AuditLog
from odoo_gateway.auth.rbac import Role
from odoo_gateway.container import Container
from odoo_gateway.odoo.errors import OdooAccessDeniedError, OdooValidationError
from odoo_gateway.reports.snapshot import StockReports
from tests.conftest import CrashingOdoo, FakeOdoo, client_for

PRODUCTS = [
    {
        "id": 1,
        "name": "Frame 7in",
        "default_code": "FR-7",
        "qty_available": 120.0,
        "uom_id": [1, "Units"],
    },
    {"id": 2, "name": "Motor 2807", "default_code": False, "qty_available": 0, "uom_id": False},
]


async def test_health_needs_no_token(client: httpx.AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200


async def test_missing_or_bad_token_is_401(client: httpx.AsyncClient) -> None:
    assert (await client.get("/products")).status_code == 401
    bad = await client.get("/products", headers={"Authorization": "Bearer not-a-jwt"})
    assert bad.status_code == 401
    assert bad.headers["WWW-Authenticate"] == "Bearer"


async def test_viewer_lists_products(client: httpx.AsyncClient, odoo: FakeOdoo, auth: Any) -> None:
    odoo.rows["product.product"] = PRODUCTS
    response = await client.get("/products", params={"search": "Frame"}, headers=auth(Role.VIEWER))
    assert response.status_code == 200
    assert response.json()[0] == {
        "id": 1,
        "name": "Frame 7in",
        "internal_ref": "FR-7",
        "qty_available": 120.0,
        "uom": "Units",
    }
    assert response.json()[1]["internal_ref"] is None  # Odoo `false` → null
    model, _, domain = odoo.calls[0]
    assert model == "product.product"
    assert ("name", "ilike", "Frame") in domain
    assert ("default_code", "ilike", "Frame") in domain  # internal references are searchable too


async def test_query_parameters_are_validated(client: httpx.AsyncClient, auth: Any) -> None:
    response = await client.get("/products", params={"limit": 5000}, headers=auth(Role.VIEWER))
    assert response.status_code == 422


async def test_viewer_cannot_create_partner_and_denial_is_audited(
    client: httpx.AsyncClient, odoo: FakeOdoo, audit: AuditLog, auth: Any
) -> None:
    response = await client.post(
        "/partners", json={"name": "Acme"}, headers=auth(Role.VIEWER, sub="viewer-1")
    )
    assert response.status_code == 403
    assert odoo.calls == []  # Odoo never saw the request
    [event] = await audit.recent(limit=10)
    assert (event.actor_subject, event.action, event.outcome) == (
        "viewer-1",
        "authz.partners:write",
        "denied",
    )


async def test_operator_creates_partner_and_audit_holds_no_values(
    client: httpx.AsyncClient, audit: AuditLog, auth: Any
) -> None:
    response = await client.post(
        "/partners",
        json={"name": "Acme", "email": "ops@acme.example", "is_company": True},
        headers={**auth(Role.OPERATOR, sub="op-1"), "X-Request-ID": "req-12345678"},
    )
    assert response.status_code == 201
    assert response.headers["X-Request-ID"] == "req-12345678"
    [event, attempted] = await audit.recent(limit=10)  # newest first
    assert (attempted.outcome, attempted.resource_id) == ("attempted", None)
    assert event.outcome == "allowed"
    assert event.resource_model == "res.partner"
    assert event.resource_id == response.json()["id"]
    assert event.request_id == attempted.request_id == "req-12345678"
    assert event.details == attempted.details == {"fields": ["email", "is_company", "name"]}
    assert "ops@acme.example" not in str(event.details)


async def test_invalid_partner_body_never_reaches_odoo(
    client: httpx.AsyncClient, odoo: FakeOdoo, auth: Any
) -> None:
    for body in ({"name": ""}, {"name": "A", "email": "nope"}, {"name": "A", "admin": True}):
        response = await client.post("/partners", json=body, headers=auth(Role.OPERATOR))
        assert response.status_code == 422, body
    assert odoo.calls == []


async def test_odoo_validation_error_is_422_and_audited_as_failed(
    client: httpx.AsyncClient, odoo: FakeOdoo, audit: AuditLog, auth: Any
) -> None:
    odoo.fail_with = OdooValidationError("Email already used")
    response = await client.post("/partners", json={"name": "Acme"}, headers=auth(Role.OPERATOR))
    assert response.status_code == 422
    assert response.json() == {"detail": "Email already used"}
    [event, attempted] = await audit.recent(limit=10)
    assert attempted.outcome == "attempted"
    assert (event.outcome, event.details["error"]) == ("failed", "OdooValidationError")


async def test_crash_after_the_odoo_call_still_leaves_an_attempted_event(
    container: Container, audit: AuditLog, auth: Any
) -> None:
    """The gap ADR-0004 describes: Odoo commits, then the gateway dies before the outcome event."""
    crashing = CrashingOdoo()
    async with client_for(container, crashing) as client:
        with pytest.raises(RuntimeError):
            await client.post("/partners", json={"name": "Acme"}, headers=auth(Role.OPERATOR))

    assert len(crashing.calls) == 1  # Odoo did create the record
    [event] = await audit.recent(limit=10)
    assert (event.action, event.outcome, event.details) == (
        "partners.create",
        "attempted",
        {"fields": ["is_company", "name"]},  # is_company has a default
    )


async def test_integration_user_misconfiguration_is_hidden_from_caller(
    client: httpx.AsyncClient, odoo: FakeOdoo, auth: Any
) -> None:
    odoo.fail_with = OdooAccessDeniedError("You are not allowed to access 'Product' records")
    response = await client.get("/products", headers=auth(Role.VIEWER))
    assert response.status_code == 502
    assert response.json() == {"detail": "Upstream error"}


async def test_only_admin_reads_audit(client: httpx.AsyncClient, auth: Any) -> None:
    assert (await client.get("/audit", headers=auth(Role.OPERATOR))).status_code == 403
    response = await client.get("/audit", headers=auth(Role.ADMIN))
    assert response.status_code == 200
    # The operator's refused attempt is already in the log.
    assert response.json()[0]["action"] == "authz.audit:read"


async def test_stock_report_is_served_from_the_snapshot_not_odoo(
    client: httpx.AsyncClient, odoo: FakeOdoo, reports: StockReports, auth: Any
) -> None:
    odoo.rows["product.product"] = PRODUCTS
    await reports.snapshot(odoo)
    odoo.calls.clear()

    response = await client.get("/reports/stock", headers=auth(Role.VIEWER))
    assert response.status_code == 200
    body = response.json()
    assert body["product_count"] == 2
    assert body["lines"][0] == {
        "product_id": 1,
        "product_name": "Frame 7in",
        "internal_ref": "FR-7",
        "qty_available": 120.0,
        "uom": "Units",
    }
    assert odoo.calls == []  # the report never touches Odoo


async def test_stock_report_is_404_before_the_first_snapshot(
    client: httpx.AsyncClient, auth: Any
) -> None:
    response = await client.get("/reports/stock", headers=auth(Role.OPERATOR))
    assert response.status_code == 404


async def test_token_without_roles_cannot_read_reports_and_denial_is_audited(
    client: httpx.AsyncClient, audit: AuditLog, auth: Any
) -> None:
    response = await client.get("/reports/stock", headers=auth(sub="nobody-1"))
    assert response.status_code == 403
    [event] = await audit.recent(limit=10)
    assert (event.actor_subject, event.action, event.outcome) == (
        "nobody-1",
        "authz.reports:read",
        "denied",
    )
