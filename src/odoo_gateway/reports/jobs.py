"""Taking a snapshot outside a request: `python -m odoo_gateway.reports.jobs` (`make snapshot`).

The scheduled worker calls the same `take_stock_snapshot`; this module stays free of any
web framework so a worker process does not have to import one.
"""

from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from odoo_gateway.config import Settings
from odoo_gateway.odoo.client import OdooClient
from odoo_gateway.reports.models import StockSnapshot
from odoo_gateway.reports.snapshot import StockReports


async def take_stock_snapshot(settings: Settings) -> StockSnapshot:
    """Build the Odoo client and the database session factory from settings, take one snapshot."""
    engine = create_async_engine(settings.database_url)
    odoo = OdooClient.from_settings(settings)
    try:
        return await StockReports(async_sessionmaker(engine, expire_on_commit=False)).snapshot(odoo)
    finally:
        await odoo.aclose()
        await engine.dispose()


def main() -> None:
    snapshot = asyncio.run(take_stock_snapshot(Settings()))
    taken = snapshot.taken_at.strftime("%Y-%m-%d %H:%M:%S %Z")
    print(f"snapshot {snapshot.id}: {snapshot.product_count} products at {taken}")


if __name__ == "__main__":
    main()
