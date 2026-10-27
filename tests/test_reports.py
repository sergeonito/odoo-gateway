"""Stock snapshot use case against a fake Odoo and a SQLite database."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from odoo_gateway.reports.snapshot import latest_snapshot, snapshot_stock
from tests.conftest import FakeOdoo

PRODUCTS = [
    {
        "id": 1,
        "name": "Frame 7in",
        "default_code": "FR-7",
        "qty_available": 120.0,
        "uom_id": [1, "Units"],
    },
    {"id": 2, "name": "Motor 2807", "default_code": False, "qty_available": 0, "uom_id": False},
    {
        "id": 3,
        "name": "Battery 6S",
        "default_code": "BAT-6S",
        "qty_available": 7.5,
        "uom_id": [1, "Units"],
    },
]


async def test_snapshot_copies_every_storable_product_page_by_page(
    engine: AsyncEngine, odoo: FakeOdoo
) -> None:
    odoo.rows["product.product"] = PRODUCTS
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    snapshot = await snapshot_stock(odoo, sessions, page_size=2)

    assert snapshot.product_count == 3
    assert [line.product_id for line in snapshot.lines] == [1, 2, 3]
    assert (snapshot.lines[1].internal_ref, snapshot.lines[1].uom) == (None, None)  # Odoo false
    assert snapshot.lines[2].qty_available == 7.5
    assert len(odoo.calls) == 2  # two pages: 2 rows, then 1
    _, _, domain = odoo.calls[0]
    assert ("is_storable", "=", True) in domain


async def test_latest_snapshot_is_the_most_recent_run(engine: AsyncEngine, odoo: FakeOdoo) -> None:
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    assert await latest_snapshot(sessions) is None

    odoo.rows["product.product"] = PRODUCTS[:1]
    first = await snapshot_stock(odoo, sessions)
    odoo.rows["product.product"] = PRODUCTS
    second = await snapshot_stock(odoo, sessions)

    latest = await latest_snapshot(sessions)
    assert latest is not None
    assert (latest.id, latest.product_count) == (second.id, 3)
    assert latest.id != first.id
    assert [line.product_name for line in latest.lines] == ["Frame 7in", "Motor 2807", "Battery 6S"]
