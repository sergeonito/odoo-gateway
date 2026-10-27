"""HTTP routes. Each route names the permission it needs; nothing else decides access."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status

from odoo_gateway.api.deps import ContainerDep, RequestIdDep, require
from odoo_gateway.api.schemas import (
    AuditEventOut,
    CreatedOut,
    PartnerIn,
    ProductOut,
    ReconcileIn,
    ReconcileOut,
    StockLineOut,
    StockReportOut,
    StuckClaimOut,
)
from odoo_gateway.audit.models import Outcome
from odoo_gateway.auth.rbac import Permission, Principal
from odoo_gateway.idempotency.store import Claim, Replay
from odoo_gateway.odoo.errors import OdooError
from odoo_gateway.ports import Domain

router = APIRouter()

CanReadInventory = Annotated[Principal, Depends(require(Permission.INVENTORY_READ))]
CanReadReports = Annotated[Principal, Depends(require(Permission.REPORTS_READ))]
CanWritePartners = Annotated[Principal, Depends(require(Permission.PARTNERS_WRITE))]
CanReadAudit = Annotated[Principal, Depends(require(Permission.AUDIT_READ))]
CanReconcile = Annotated[Principal, Depends(require(Permission.IDEMPOTENCY_RECONCILE))]

# Optional on writes. A retry with the same key gets the first result, never a second record.
IdempotencyKeyDep = Annotated[
    str | None,
    Header(alias="Idempotency-Key", min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$"),
]


@router.get("/health", tags=["ops"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/products", tags=["inventory"])
async def list_products(
    container: ContainerDep,
    _: CanReadInventory,
    search: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> list[ProductOut]:
    domain: Domain = [("active", "=", True)]
    if search:
        # Odoo evaluates domains server-side as data, not code: no injection surface here.
        domain += ["|", ("name", "ilike", search), ("default_code", "ilike", search)]
    rows = await container.odoo.search_read(
        "product.product",
        domain,
        ProductOut.odoo_fields(),
        limit=limit,
        offset=offset,
        order="name asc, id asc",
    )
    return [ProductOut.from_odoo(row) for row in rows]


@router.get("/products/{product_id}/stock", tags=["inventory"])
async def product_stock(
    container: ContainerDep,
    _: CanReadInventory,
    product_id: int,
) -> list[StockLineOut]:
    rows = await container.odoo.search_read(
        "stock.quant",
        [("product_id", "=", product_id), ("location_id.usage", "=", "internal")],
        StockLineOut.odoo_fields(),
        limit=200,
    )
    return [StockLineOut.from_odoo(row) for row in rows]


@router.get("/reports/stock", tags=["reports"])
async def stock_report(container: ContainerDep, _: CanReadReports) -> StockReportOut:
    """The last stock snapshot. Served from our database; Odoo is not called (ADR-0005)."""
    snapshot = await container.reports.latest()
    if snapshot is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No stock snapshot has been taken yet")
    return StockReportOut.model_validate(snapshot)


@router.post("/partners", status_code=status.HTTP_201_CREATED, tags=["partners"])
async def create_partner(
    body: PartnerIn,
    container: ContainerDep,
    request_id: RequestIdDep,
    principal: CanWritePartners,
    response: Response,
    idempotency_key: IdempotencyKeyDep = None,
) -> CreatedOut:
    action = "partners.create"
    values = body.to_odoo()
    audit_fields: dict[str, object] = {"fields": sorted(values)}  # names only, never the values

    claim: Claim | None = None
    if idempotency_key is not None:
        claimed = await container.idempotency.claim(
            actor_subject=principal.subject, key=idempotency_key, action=action, values=values
        )
        if isinstance(claimed, Replay):
            response.headers["Idempotency-Replayed"] = "true"
            response.status_code = claimed.status
            return CreatedOut.model_validate(claimed.body)
        claim = claimed
        audit_fields["idempotency_key"] = idempotency_key

    # Written before the call: a crash after Odoo commits still leaves a trace (ADR-0004).
    await container.audit.record(
        principal=principal,
        action=action,
        outcome=Outcome.ATTEMPTED,
        request_id=request_id,
        resource_model="res.partner",
        details=audit_fields,
    )
    try:
        partner_id = await container.odoo.create("res.partner", values)
    except OdooError as exc:
        await container.audit.record(
            principal=principal,
            action=action,
            outcome=Outcome.FAILED,
            request_id=request_id,
            resource_model="res.partner",
            details={**audit_fields, "error": type(exc).__name__},
        )
        if claim is not None:
            await container.idempotency.release(claim)  # a known failure: the key may be retried
        raise
    await container.audit.record(
        principal=principal,
        action=action,
        outcome=Outcome.ALLOWED,
        request_id=request_id,
        resource_model="res.partner",
        resource_id=partner_id,
        details=audit_fields,
    )
    result = CreatedOut(id=partner_id)
    if claim is not None:
        await container.idempotency.complete(
            claim, status=status.HTTP_201_CREATED, body=result.model_dump()
        )
    return result


@router.get("/audit", tags=["audit"])
async def list_audit(
    container: ContainerDep,
    _: CanReadAudit,
    actor: Annotated[str | None, Query(max_length=128)] = None,
    action: Annotated[str | None, Query(max_length=64)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[AuditEventOut]:
    events = await container.audit.recent(limit=limit, actor=actor, action=action)
    return [AuditEventOut.model_validate(event) for event in events]


@router.get("/reconciliation/idempotency", tags=["reconciliation"])
async def stuck_idempotency_keys(
    container: ContainerDep,
    _: CanReconcile,
    older_than_s: Annotated[int, Query(ge=10, le=86_400)] = 300,
) -> list[StuckClaimOut]:
    """Keys claimed before a request died: the Odoo call may or may not have happened."""
    now = datetime.now(UTC)
    out: list[StuckClaimOut] = []
    for claim in await container.idempotency.stuck(older_than=timedelta(seconds=older_than_s)):
        attempt = await container.audit.attempt_for_key(
            actor_subject=claim.actor_subject, idempotency_key=claim.key
        )
        out.append(
            StuckClaimOut(
                actor_subject=claim.actor_subject,
                key=claim.key,
                action=claim.action,
                created_at=claim.created_at,
                age_s=int((now - claim.created_at).total_seconds()),
                request_id=attempt.request_id if attempt else None,
            )
        )
    return out


@router.post("/reconciliation/idempotency", tags=["reconciliation"])
async def reconcile_idempotency_key(
    body: ReconcileIn,
    container: ContainerDep,
    request_id: RequestIdDep,
    principal: CanReconcile,
) -> ReconcileOut:
    """Resolve a stuck key after checking Odoo by hand. Audited like any other write."""
    claim = await container.idempotency.in_progress(actor_subject=body.actor_subject, key=body.key)
    if claim is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No idempotency key in progress")
    if body.resolution == "release":
        await container.idempotency.release(claim)
    else:
        await container.idempotency.complete(
            claim, status=status.HTTP_201_CREATED, body={"id": body.resource_id}
        )
    await container.audit.record(
        principal=principal,
        action="idempotency.reconcile",
        outcome=Outcome.ALLOWED,
        request_id=request_id,
        resource_model="res.partner" if body.resource_id else None,
        resource_id=body.resource_id,
        details={
            "key": body.key,
            "actor_subject": body.actor_subject,
            "resolution": body.resolution,
        },
    )
    return ReconcileOut(actor_subject=body.actor_subject, key=body.key, resolution=body.resolution)
