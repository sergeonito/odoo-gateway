# ADR-0005: The stock report reads a snapshot, not Odoo

**Status:** accepted · 2026-10-26

## Context

A stock report is read often, by several products, and each read would otherwise be a paged
`search_read` over every storable product in Odoo. Odoo computes `qty_available` on the fly,
so the cost lands on Odoo with every call, and an Odoo outage takes the report down with it.

The gateway's rule so far: Odoo is the system of record and the gateway stores no business
data (ADR-0001).

## Decision

A background job copies on-hand quantities of all storable products into the gateway's
PostgreSQL at a fixed interval: one `stock_snapshots` row per run, one `stock_snapshot_lines`
row per product. `GET /reports/stock` reads the latest run and says when it was taken.

The use case (`reports/snapshot.py`) is plain async code; the scheduler that calls it is a
separate concern.

## Consequences

- The report is as stale as the interval, and says so (`taken_at`). Callers who need live
  numbers use `GET /products`, which still reads Odoo.
- The snapshot is derived data: rebuilt by every run, never written back, droppable at any
  time. Odoo remains the system of record; this is a cache with history, not a second model.
- Runs are kept, so a trend over time is one query away; a retention job becomes necessary
  once the table grows.
- The job needs the same integration user and the same read rights as the API, nothing more.
