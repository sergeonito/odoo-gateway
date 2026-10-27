"""Use case: copy on-hand quantities of every storable product from Odoo into a snapshot.

Pure async, no framework: the HTTP layer and the background worker both call this.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from odoo_gateway.api.schemas import ProductOut
from odoo_gateway.ports import OdooPort
from odoo_gateway.reports.models import StockSnapshot, StockSnapshotLine

DEFAULT_PAGE_SIZE = 500


async def snapshot_stock(
    odoo: OdooPort,
    sessions: async_sessionmaker[AsyncSession],
    *,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> StockSnapshot:
    """Read every storable product page by page, then commit one snapshot with its lines."""
    products = [ProductOut.from_odoo(row) for row in await _all_storable_products(odoo, page_size)]
    snapshot = StockSnapshot(
        taken_at=datetime.now(UTC),
        product_count=len(products),
        lines=[
            StockSnapshotLine(
                product_id=p.id,
                product_name=p.name,
                internal_ref=p.internal_ref,
                qty_available=p.qty_available,
                uom=p.uom,
            )
            for p in products
        ],
    )
    async with sessions.begin() as session:
        session.add(snapshot)
    return snapshot


async def latest_snapshot(sessions: async_sessionmaker[AsyncSession]) -> StockSnapshot | None:
    query = (
        select(StockSnapshot)
        .options(selectinload(StockSnapshot.lines))  # no lazy loads outside the session
        .order_by(StockSnapshot.taken_at.desc(), StockSnapshot.id.desc())
        .limit(1)
    )
    async with sessions() as session:
        return await session.scalar(query)


async def _all_storable_products(odoo: OdooPort, page_size: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = await odoo.search_read(
            "product.product",
            [("active", "=", True), ("is_storable", "=", True)],
            ProductOut.odoo_fields(),
            limit=page_size,
            offset=offset,
            order="id asc",
        )
        rows.extend(page)
        if len(page) < page_size:
            return rows
        offset += page_size
