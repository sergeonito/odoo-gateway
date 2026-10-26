"""Prepare the compose Odoo for the gateway: an integration user and a few products with stock.

Runs once as the Odoo admin (defaults match docker-compose.yml) through the gateway's own
JSON-RPC client. Safe to run again: existing records are reused.

    uv run python scripts/setup_odoo.py          # then: make odoo-apikey
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Final

from pydantic import SecretStr

from odoo_gateway.config import Settings
from odoo_gateway.odoo.client import OdooClient

INTEGRATION_LOGIN: Final = "gateway"

# The narrowest standard groups that cover the gateway's endpoints (ADR-0002):
# read products and quants, create partners. `base.group_user` is implied by the stock group.
INTEGRATION_GROUPS: Final = ("stock.group_stock_user", "base.group_partner_manager")

# (internal reference, name, on-hand quantity in the main warehouse)
SEED_PRODUCTS: Final = (
    ("DRN-FR7", "Frame 7in carbon", 120.0),
    ("DRN-M2807", "Motor 2807 1300KV", 40.0),
    ("DRN-BAT6S", "Battery 6S 1300mAh", 0.0),
)


async def xmlid_to_id(odoo: OdooClient, xmlid: str) -> int:
    module, name = xmlid.split(".", 1)
    rows = await odoo.search_read(
        "ir.model.data", [("module", "=", module), ("name", "=", name)], ["res_id"], limit=1
    )
    if not rows:
        raise SystemExit(f"{xmlid} not found: is the module installed?")
    return int(rows[0]["res_id"])


async def ensure_integration_user(odoo: OdooClient) -> int:
    group_ids = [await xmlid_to_id(odoo, xmlid) for xmlid in INTEGRATION_GROUPS]
    values: dict[str, Any] = {
        "name": "Gateway integration",
        "login": INTEGRATION_LOGIN,
        "groups_id": [(6, 0, group_ids)],  # replace, so re-running narrows rights again
    }
    users = await odoo.execute_kw(
        "res.users",
        "search_read",
        [[("login", "=", INTEGRATION_LOGIN)]],
        {"fields": ["id"], "limit": 1, "context": {"active_test": False}},
    )
    if users:
        user_id = int(users[0]["id"])
        await odoo.execute_kw("res.users", "write", [[user_id], {**values, "active": True}])
        return user_id
    # No password: the user can only act through an API key (make odoo-apikey).
    return await odoo.create("res.users", values)


async def ensure_products(odoo: OdooClient) -> dict[str, int]:
    codes = [code for code, _, _ in SEED_PRODUCTS]
    existing = await odoo.search_read(
        "product.product", [("default_code", "in", codes)], ["id", "default_code"], limit=10
    )
    ids = {str(row["default_code"]): int(row["id"]) for row in existing}
    for code, name, _ in SEED_PRODUCTS:
        if code not in ids:
            ids[code] = await odoo.create(
                "product.product",
                {"name": name, "default_code": code, "type": "consu", "is_storable": True},
            )
    return ids


async def ensure_stock(odoo: OdooClient, product_ids: dict[str, int]) -> None:
    warehouses = await odoo.search_read("stock.warehouse", [], ["lot_stock_id"], limit=1)
    location_id = int(warehouses[0]["lot_stock_id"][0])
    for code, _, quantity in SEED_PRODUCTS:
        if quantity <= 0:
            continue
        quants = await odoo.search_read(
            "stock.quant",
            [("product_id", "=", product_ids[code]), ("location_id", "=", location_id)],
            ["id"],
            limit=1,
        )
        if quants:
            continue
        # An inventory adjustment: a quant with a counted quantity, then "apply".
        quant_id = await odoo.execute_kw(
            "stock.quant",
            "create",
            [
                {
                    "product_id": product_ids[code],
                    "location_id": location_id,
                    "inventory_quantity": quantity,
                }
            ],
            {"context": {"inventory_mode": True}},
        )
        await odoo.execute_kw("stock.quant", "action_apply_inventory", [[quant_id]])


async def main() -> None:
    settings = Settings()
    odoo = OdooClient(
        base_url=settings.odoo_url,
        db=settings.odoo_db,
        login=os.environ.get("ODOO_ADMIN_LOGIN", "admin"),
        api_key=SecretStr(os.environ.get("ODOO_ADMIN_PASSWORD", "admin")),
    )
    try:
        user_id = await ensure_integration_user(odoo)
        product_ids = await ensure_products(odoo)
        await ensure_stock(odoo, product_ids)
    finally:
        await odoo.aclose()
    print(f"integration user '{INTEGRATION_LOGIN}' id={user_id} groups={INTEGRATION_GROUPS}")
    print(f"seed products: {product_ids}")
    print("next: make odoo-apikey, then put GATEWAY_ODOO_LOGIN=gateway and the key into .env")


if __name__ == "__main__":
    asyncio.run(main())
