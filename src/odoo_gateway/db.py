"""Declarative base shared by every table the gateway owns (audit log, report snapshots)."""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
