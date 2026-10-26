# odoo-gateway

A FastAPI service between a company's internal products and Odoo: one role model for every
product, sign-in with Microsoft 365 accounts (Microsoft Entra ID), and an append-only audit
log of who did what.

> **Status:** demo project, in development since October 2026. Runs against Odoo 18 Community
> from the compose stack with a least-privileged integration user; Entra ID is verified with
> locally signed tokens so far (see Roadmap).

## Why a gateway

Internal tools that call Odoo directly each need their own Odoo credentials, repeat their own
access rules, and leave no common trail. The gateway puts three things in one place:

- **Access:** users sign in with their Microsoft 365 account; app roles from Entra ID map to
  internal roles; every route declares the permission it needs.
- **Integration:** one least-privileged Odoo integration user, a typed async client, reads
  retried, writes never retried.
- **Audit:** every write and every refused attempt lands in an append-only table, with the
  caller, the request id and the outcome, but without personal data values.

## What works

| Area | What is there |
| --- | --- |
| Odoo client | Async JSON-RPC (`/jsonrpc`), uid cached, typed errors, Odoo tracebacks never leave the client, backoff with jitter for reads only |
| Auth | Entra ID access tokens (RS256, tenant signing keys, `oid` as subject, app roles mapped by config); HS256 dev tokens, refused when `environment=production` |
| RBAC | Roles `viewer` / `operator` / `admin` → permissions; routes depend on permissions only; every 403 is audited |
| Audit | PostgreSQL table, append-only through triggers (UPDATE, DELETE, TRUNCATE rejected); field names, not values |
| API | `GET /products` (search by name or internal reference), `GET /products/{id}/stock`, `POST /partners`, `GET /audit`, `GET /health`; OpenAPI at `/docs` |
| Local Odoo | Compose stack with Odoo 18 (`stock`, `contacts`); `make odoo-setup` creates the integration user with two standard groups and seeds three products with stock; `make odoo-apikey` issues its API key |
| Quality | `mypy --strict`, ruff, pytest (client, auth, API), a PostgreSQL trigger test, live smoke tests through the real app against the compose stack, GitHub Actions |

## Not yet

- Entra ID tested with locally signed tokens, not yet with a real tenant.
- No Microsoft Graph sync, background jobs, idempotency keys, rate limiting or Kubernetes
  manifests.

## Roadmap

1. Microsoft Entra ID with a real tenant: app roles, device-code token, setup guide.
2. Background stock snapshot (Celery + Redis) and `GET /reports/stock` from the snapshot.
3. Idempotent writes: `Idempotency-Key`, an `attempted` audit event before the Odoo call.
4. Microsoft Graph: group membership sync, disabled users lose access.

## Run it

```bash
make env            # .env with a random dev token secret
make up             # Odoo 18 + 2x PostgreSQL + gateway (first start installs Odoo modules, ~2 min)
make odoo-setup     # integration user `gateway` + three seed products with stock (as admin, once)
make odoo-apikey    # prints an API key for `gateway`: put it into .env with GATEWAY_ODOO_LOGIN=gateway
make up             # restart the gateway with the integration user

TOKEN=$(make -s token ROLE=viewer)
curl -s -H "Authorization: Bearer $TOKEN" "http://localhost:8000/products?search=DRN"
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/products/<id>/stock
TOKEN=$(make -s token ROLE=operator)
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
     -d '{"name":"Acme","is_company":true}' http://localhost:8000/partners
TOKEN=$(make -s token ROLE=admin)
curl -s -H "Authorization: Bearer $TOKEN" "http://localhost:8000/audit?limit=5"
make test-live      # the same path as a test, through the real app and the real audit database
```

Odoo UI: http://localhost:8069 (admin / admin, local only). API docs: http://localhost:8000/docs

## Findings from the live run

What the mocked tests could not tell, and what changed because of it:

- **API keys cannot be issued over RPC.** `res.users.apikeys._generate` is private, so
  `make odoo-apikey` runs it inside `odoo shell`. The integration user has no password at all:
  it cannot sign in to the UI, only present its key.
- **Standard groups over-grant.** Inventory / User (`stock.group_stock_user`) is the smallest
  standard group that can read `stock.quant`, but it also allows writing quants. A custom group
  with read-only access would be tighter; for a demo the standard groups and ADR-0002 are the
  honest trade-off.
- **Inventory adjustments over RPC** need `context={"inventory_mode": true}` on
  `stock.quant.create` plus a call to `action_apply_inventory`. Odoo 18 products use
  `type="consu"` with `is_storable=true`; `detailed_type` is gone.
- **Search by name only was not enough.** The seed products are found by internal reference
  (`DRN-…`), so `GET /products?search=` now matches `default_code` too.
- **Odoo installs demo data** unless `--without-demo` is passed, so a fresh stack already has
  39 products. The seed adds three with known quantities that the live tests assert on.
- **`docker compose exec odoo odoo …` bypasses the image entrypoint**, so database flags must be
  passed explicitly (see the `odoo-apikey` target).

## Develop

```bash
uv sync
make check        # ruff + mypy --strict + pytest
make test-live    # against the running compose stack
make migration MSG="add x"   # autogenerated from the models and formatted by ruff; review it
```

Migrations are generated from the SQLAlchemy models and reviewed by hand: autogenerate does
not see triggers, renames or data moves, so those parts are written explicitly (as the
append-only triggers in migration 0001 are).

## Architecture and design decisions

[docs/architecture.md](docs/architecture.md): one request end to end, layers and dependency
rules, where each decision is made, what the audit log holds, failure behaviour.

- [ADR-0001](docs/adr/0001-gateway-in-front-of-odoo.md): a gateway instead of direct Odoo access
- [ADR-0002](docs/adr/0002-roles-in-gateway-one-odoo-user.md): roles live in the gateway; Odoo sees one integration user
- [ADR-0003](docs/adr/0003-json-rpc-now-json2-later.md): JSON-RPC now, JSON-2 when Odoo moves to 19+
- [ADR-0004](docs/adr/0004-audit-after-the-call.md): audit is written after the Odoo call, and the gap that leaves

## Layout

```
src/odoo_gateway/
  api/        routes, dependencies (auth, permissions), request/response models
  auth/       roles and permissions, token verification (dev, Entra ID)
  audit/      audit table and log
  odoo/       JSON-RPC client and error mapping
  ports.py    what routes need from Odoo (the client is one implementation)
  main.py     app factory, request ids, error mapping
migrations/   Alembic (audit table + append-only triggers)
scripts/      setup_odoo.py (integration user + seed), odoo_apikey.py (runs inside odoo shell)
tests/        unit and API tests; PostgreSQL and live Odoo tests skip unless enabled
```
