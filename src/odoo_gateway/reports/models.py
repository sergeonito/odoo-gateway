"""Stock snapshots: a periodic copy of on-hand quantities, read by reports instead of Odoo.

Derived data, rebuilt by every run; Odoo stays the system of record (ADR-0005).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from odoo_gateway.db import Base


class StockSnapshot(Base):
    """One run of the snapshot job."""

    __tablename__ = "stock_snapshots"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"), primary_key=True
    )
    taken_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    product_count: Mapped[int] = mapped_column(Integer())

    lines: Mapped[list[StockSnapshotLine]] = relationship(
        back_populates="snapshot", cascade="all, delete-orphan", order_by="StockSnapshotLine.id"
    )


class StockSnapshotLine(Base):
    """On-hand quantity of one product at the time of the run."""

    __tablename__ = "stock_snapshot_lines"
    __table_args__ = (UniqueConstraint("snapshot_id", "product_id", name="uq_snapshot_product"),)

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"), primary_key=True
    )
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("stock_snapshots.id", ondelete="CASCADE"), index=True
    )
    product_id: Mapped[int] = mapped_column(Integer())
    product_name: Mapped[str] = mapped_column(String(256))
    internal_ref: Mapped[str | None] = mapped_column(String(64))
    qty_available: Mapped[float] = mapped_column(Float())
    uom: Mapped[str | None] = mapped_column(String(64))

    snapshot: Mapped[StockSnapshot] = relationship(back_populates="lines")
