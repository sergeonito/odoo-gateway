"""Everything a request needs, built once at startup and stored on `app.state`."""

from __future__ import annotations

from dataclasses import dataclass

from odoo_gateway.audit.log import AuditLog
from odoo_gateway.auth.tokens import TokenVerifier
from odoo_gateway.idempotency.store import IdempotencyStore
from odoo_gateway.ports import OdooPort
from odoo_gateway.reports.snapshot import StockReports


@dataclass(frozen=True, slots=True)
class Container:
    odoo: OdooPort
    audit: AuditLog
    verifier: TokenVerifier
    reports: StockReports
    idempotency: IdempotencyStore
