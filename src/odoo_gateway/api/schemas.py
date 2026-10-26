"""Request and response models. Input is validated here, before anything reaches Odoo."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def _text(value: Any) -> str | None:
    """Odoo sends `false` for empty char fields."""
    return value if isinstance(value, str) and value else None


def _many2one_name(value: Any) -> str | None:
    """Odoo sends many2one fields as `[id, "display name"]` or `false`."""
    if isinstance(value, list) and len(value) == 2 and isinstance(value[1], str):
        return value[1]
    return None


def _number(value: Any) -> float:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0.0


class ProductOut(BaseModel):
    id: int
    name: str
    internal_ref: str | None
    qty_available: float
    uom: str | None

    @classmethod
    def odoo_fields(cls) -> list[str]:
        return ["id", "name", "default_code", "qty_available", "uom_id"]

    @classmethod
    def from_odoo(cls, row: dict[str, Any]) -> ProductOut:
        return cls(
            id=int(row["id"]),
            name=str(row.get("name") or ""),
            internal_ref=_text(row.get("default_code")),
            qty_available=_number(row.get("qty_available")),
            uom=_many2one_name(row.get("uom_id")),
        )


class StockLineOut(BaseModel):
    location: str | None
    quantity: float
    reserved: float

    @classmethod
    def odoo_fields(cls) -> list[str]:
        return ["location_id", "quantity", "reserved_quantity"]

    @classmethod
    def from_odoo(cls, row: dict[str, Any]) -> StockLineOut:
        return cls(
            location=_many2one_name(row.get("location_id")),
            quantity=_number(row.get("quantity")),
            reserved=_number(row.get("reserved_quantity")),
        )


class PartnerIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=128)
    email: str | None = Field(default=None, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    phone: str | None = Field(default=None, max_length=32, pattern=r"^\+?[0-9 ()\-]{5,32}$")
    is_company: bool = False

    def to_odoo(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


class CreatedOut(BaseModel):
    id: int


class AuditEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    occurred_at: datetime
    request_id: str
    actor_subject: str
    actor_name: str
    actor_roles: list[str]
    action: str
    outcome: str
    resource_model: str | None
    resource_id: int | None
    details: dict[str, Any]
