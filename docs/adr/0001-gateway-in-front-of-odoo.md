# ADR-0001: A gateway in front of Odoo

**Status:** accepted · 2026-10-22

## Context

Several internal products need Odoo data (products, stock, partners). Calling Odoo directly from
each product means a set of Odoo credentials per product, access rules repeated in every
codebase, and no single place that answers "who changed this, from which tool".

## Decision

Internal products call one HTTP service, the gateway. It authenticates users with Microsoft
Entra ID, checks a single role model, calls Odoo with one integration user, and writes an audit
event for every write and every refused attempt.

## Consequences

- One place to change access rules and one audit trail across products.
- Odoo stays the system of record; the gateway owns no business data, only the audit log.
- The gateway is a new hop and a new failure point: it needs health checks, timeouts and clear
  error mapping (503 when Odoo is down, 502 when the integration user is misconfigured).
- Products lose access to Odoo features the gateway does not expose. New needs become new
  endpoints, which is the point: every capability is reviewed and permissioned.
