# ADR-0003: JSON-RPC now, JSON-2 later

**Status:** accepted · 2026-10-22

## Context

Odoo 18 offers XML-RPC (`/xmlrpc/2`) and JSON-RPC (`/jsonrpc`) for external calls. Odoo 19
introduced the External JSON-2 API (`/json/2/<model>/<method>`, API key as a bearer token) and
deprecated both RPC endpoints. Per Odoo's documentation the RPC `object` service is scheduled for
removal in Odoo 22 (fall 2028) and in Odoo Online 21.1 (winter 2027).

The target Odoo is 18.

## Decision

Use JSON-RPC: it is native JSON (no XML marshalling), works with `httpx` async, and carries the
same `execute_kw` semantics as XML-RPC. Routes never touch the transport: they depend on
`OdooPort` (`ports.py`), and `OdooClient` is one implementation.

## Consequences

- Moving to Odoo 19+ means a second `OdooPort` implementation for JSON-2 and a settings switch,
  not a rewrite of routes or tests (the API tests already run against a fake port).
- Error mapping (`odoo/errors.py`) is keyed by Odoo exception names, which JSON-2 keeps, so it
  can be shared.
