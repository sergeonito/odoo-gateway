"""The Celery app is configured from settings and its task is a thin wrapper around the use case."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from odoo_gateway import worker
from odoo_gateway.config import Settings
from odoo_gateway.reports import jobs
from odoo_gateway.reports.models import StockSnapshot
from tests.conftest import DEV_SECRET


def _settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, dev_jwt_secret=DEV_SECRET, **overrides)


def test_beat_takes_the_snapshot_at_the_configured_interval() -> None:
    app = worker.create_celery(_settings(snapshot_interval_s=120, redis_url="redis://broker/3"))

    assert app.conf.broker_url == "redis://broker/3"
    assert app.conf.beat_schedule["stock-snapshot"] == {
        "task": worker.STOCK_SNAPSHOT_TASK,
        "schedule": 120,
    }
    assert app.conf.task_acks_late is True
    assert worker.STOCK_SNAPSHOT_TASK in app.tasks


def test_snapshot_task_calls_the_use_case_with_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings()
    seen: list[Settings] = []

    async def fake_take_stock_snapshot(cfg: Settings) -> StockSnapshot:
        seen.append(cfg)
        return StockSnapshot(id=7, taken_at=datetime.now(UTC), product_count=3, lines=[])

    monkeypatch.setattr(jobs, "take_stock_snapshot", fake_take_stock_snapshot)
    app = worker.create_celery(settings)
    app.conf.task_always_eager = True

    result = app.tasks[worker.STOCK_SNAPSHOT_TASK].apply().get()

    assert result == {"id": 7, "product_count": 3}
    assert seen == [settings]


def test_snapshot_interval_has_sane_bounds() -> None:
    with pytest.raises(ValueError, match="snapshot_interval_s"):
        _settings(snapshot_interval_s=5)
