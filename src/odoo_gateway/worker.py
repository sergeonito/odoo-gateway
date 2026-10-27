"""Celery worker and beat schedule. Tasks are thin: they call a use case and log the outcome.

Run: `python -m odoo_gateway.worker worker --loglevel=info` and `... beat --loglevel=info`
(`make worker` runs both in one process for a laptop).
"""

from __future__ import annotations

import asyncio
import logging
import sys

from celery import Celery

from odoo_gateway.config import Settings
from odoo_gateway.odoo.errors import OdooUnavailableError
from odoo_gateway.reports import jobs

log = logging.getLogger("odoo_gateway.worker")

STOCK_SNAPSHOT_TASK = "reports.stock_snapshot"


def create_celery(settings: Settings | None = None) -> Celery:
    cfg = settings or Settings()
    app = Celery("odoo_gateway", broker=cfg.redis_url)
    app.conf.update(
        task_ignore_result=True,  # outcomes live in our database and logs, not in the broker
        task_acks_late=True,  # a task lost mid-run is redelivered, not dropped
        worker_prefetch_multiplier=1,
        broker_connection_retry_on_startup=True,
        timezone="UTC",
        beat_schedule={
            "stock-snapshot": {"task": STOCK_SNAPSHOT_TASK, "schedule": cfg.snapshot_interval_s}
        },
    )

    # A snapshot only reads Odoo, so retrying it is safe (unlike writes, see odoo/client.py).
    @app.task(
        name=STOCK_SNAPSHOT_TASK,
        shared=False,  # bound to this app's settings, not copied into every Celery instance
        autoretry_for=(OdooUnavailableError,),
        retry_backoff=True,
        max_retries=3,
    )
    def stock_snapshot() -> dict[str, int]:
        snapshot = asyncio.run(jobs.take_stock_snapshot(cfg))
        log.info("stock snapshot id=%s products=%s", snapshot.id, snapshot.product_count)
        return {"id": snapshot.id, "product_count": snapshot.product_count}

    return app


if __name__ == "__main__":
    sys.exit(create_celery().start(sys.argv[1:]))
