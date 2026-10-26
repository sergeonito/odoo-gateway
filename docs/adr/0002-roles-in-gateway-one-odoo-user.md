# ADR-0002: Roles live in the gateway; Odoo sees one integration user

**Status:** accepted · 2026-10-22

## Context

Two ways to enforce access to Odoo data from internal products:

1. Each person gets an Odoo account; the gateway calls Odoo as that person, and Odoo's groups
   and record rules decide.
2. The gateway decides with its own roles, and calls Odoo as one integration user.

## Decision

Option 2. Roles come from Entra ID app roles (`roles` claim), map to internal roles by
configuration, and internal roles map to permissions in code (`auth/rbac.py`). Routes depend on
permissions only.

The Odoo integration user gets the narrowest groups that cover the exposed endpoints (read on
products and stock, create on partners), and an API key, not a password.

## Consequences

- No Odoo licence or account per person; access is managed where the company already manages
  people (Microsoft 365).
- Odoo's own record rules do not see the real person, so per-person data restrictions must be
  enforced in the gateway, and the gateway's audit log, not Odoo's `create_uid`, says who did
  what. Odoo records show the integration user as author.
- If a future need requires Odoo-side per-person rules (approvals by named managers, say),
  option 1 can be added per endpoint without changing the role model.
- Unknown Entra roles map to nothing. A user with no mapped role can authenticate but can do
  nothing, and every attempt is audited.
