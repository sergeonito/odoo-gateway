# Architecture

The gateway is one HTTP service that internal products call instead of Odoo. It answers three
questions in one place: who is calling, what they may do, and what they did.

## One request, end to end

```mermaid
sequenceDiagram
    autonumber
    participant P as Internal product
    participant G as Gateway (FastAPI)
    participant E as Entra ID (JWKS)
    participant O as Odoo 18 (/jsonrpc)
    participant A as Audit DB (PostgreSQL)

    P->>G: POST /partners with a bearer token
    G->>G: Validate body (Pydantic, extra=forbid)
    G->>E: Signing keys (cached 1 h)
    G->>G: Verify token, map app roles → roles → permissions
    alt lacks partners:write
        G->>A: event authz.partners:write = denied
        G-->>P: 403
    else allowed
        G->>A: event partners.create = attempted (field names, no values)
        G->>O: object.execute_kw res.partner create
        O-->>G: new id (or an Odoo exception)
        G->>A: event partners.create = allowed or failed<br/>(record id or error class)
        G-->>P: 201 with the new id, or 422 / 502 / 503
    end
```

Reads (`GET /products`, `GET /products/{id}/stock`) follow the same path without the audit
event on success: refusals are always audited, successful reads are not.

## Layers

```mermaid
flowchart LR
    subgraph api [api/]
        routes[routes.py<br/>names a Permission per route]
        deps["deps.py<br/>principal, require(permission)"]
        schemas["schemas.py<br/>input validation, Odoo false → null"]
    end
    subgraph auth [auth/]
        rbac["rbac.py<br/>Role → Permission"]
        tokens[tokens.py<br/>DevTokenVerifier, EntraTokenVerifier]
    end
    subgraph audit [audit/]
        log[log.py<br/>record, recent]
        models[models.py<br/>audit_events]
    end
    subgraph reports [reports/]
        snapshot["snapshot.py<br/>StockReports: snapshot, latest"]
        jobs["jobs.py<br/>take_stock_snapshot(settings)"]
    end
    worker["worker.py<br/>Celery beat + task"]
    ports[ports.py<br/>OdooPort protocol]
    subgraph odoo [odoo/]
        client["client.py<br/>JSON-RPC, retries for reads"]
        errors["errors.py<br/>Odoo exception → OdooError"]
    end
    container[container.py<br/>odoo, audit, verifier, reports]
    main["main.py<br/>create_app, error → HTTP"]

    routes --> deps --> tokens
    deps --> rbac
    deps --> log
    routes --> schemas
    routes --> ports
    routes --> log
    client -. implements .-> ports
    client --> errors
    main --> container
    container --> ports
    container --> log
    container --> tokens
    container --> snapshot
    routes --> snapshot
    snapshot --> ports
    worker --> jobs --> snapshot
    jobs --> client
```

Dependency rules, enforced by imports and by tests:

- `api/` depends on `OdooPort`, never on `odoo/client.py`. The API tests run against `FakeOdoo`
  (`tests/conftest.py`), which is why they need no Odoo.
- `odoo/` is the only package that knows JSON-RPC. Replacing the transport (Odoo 19 JSON-2,
  [ADR-0003](adr/0003-json-rpc-now-json2-later.md)) means a second `OdooPort` implementation.
- `auth/rbac.py` is the single source of roles → permissions. Routes name a `Permission`;
  nothing outside `rbac.py` mentions a `Role`.
- `main.py` wires the real implementations into a `Container` in the lifespan; tests pass their
  own `Container` to `create_app`.

## Where decisions are made

| Question | Where | Notes |
| --- | --- | --- |
| Is the token genuine? | `auth/tokens.py` | Dev: HS256 with the gateway's secret, refused in production. Entra: RS256 against the tenant's JWKS, issuer and audience pinned. |
| Who is this? | `auth/tokens.py` | Entra `oid` (stable across apps), not `sub`. |
| What may they do? | `auth/rbac.py` | App roles → internal roles by configuration; roles → permissions in code. Unknown roles grant nothing. |
| May this route run? | `api/deps.py` | `require(permission)`; every refusal is audited before the 403. |
| Is the input sane? | `api/schemas.py` | Lengths, patterns, `extra="forbid"`. A 422 here never reaches Odoo. |
| How does Odoo see us? | `odoo/client.py` | One integration user with an API key and two standard groups ([ADR-0002](adr/0002-roles-in-gateway-one-odoo-user.md)). |
| What happens on failure? | `main.py` | `OdooValidationError` → 422 with Odoo's user-facing message; `OdooUnavailableError` → 503; auth/access errors of the integration user → 502 "Upstream error", details in logs only. |

## What the audit log holds

Two rows per write (`attempted` before the Odoo call, the outcome after it) and one per refusal
in `audit_events`:

| Column | Example | Why |
| --- | --- | --- |
| `actor_subject`, `actor_name`, `actor_roles` | `oid`, "Olena K.", `["operator"]` | Who, as the identity provider knows them |
| `action`, `outcome` | `partners.create`, `attempted` / `allowed` / `denied` / `failed` | What was attempted and how it ended |
| `resource_model`, `resource_id` | `res.partner`, 44 | Which Odoo record |
| `request_id` | from `X-Request-ID` or generated | Correlates with application logs |
| `details` | `{"fields": ["email", "name"]}` or `{"error": "OdooValidationError"}` | Field names and error classes only, never submitted values |

The table is append-only at the database level: triggers reject `UPDATE`, `DELETE` and
`TRUNCATE` (migration 0001, tested against PostgreSQL in CI). A crash between the Odoo commit
and the outcome event leaves an `attempted` row with no `allowed` or `failed` after it, which is
what a reconciliation looks for ([ADR-0004](adr/0004-audit-after-the-call.md)).

## Failure behaviour

| Failure | Gateway does | Caller sees |
| --- | --- | --- |
| Odoo unreachable or HTTP 5xx | Retries reads with exponential backoff and jitter (2 retries by default); never retries writes | 503 |
| Odoo rejects the integration user's key | Drops the cached uid, re-authenticates on the next call | 502 |
| Integration user lacks a group | Logs the Odoo exception name | 502, no details |
| Odoo `ValidationError` / `UserError` | Audits `failed` with the error class | 422 with Odoo's message |
| Audit insert fails | Propagates: a write that cannot be audited is not reported as success | 500 |

## Local stack

```mermaid
flowchart LR
    dev["Developer<br/>make token, curl, make test-live"]
    dev --> gw[gateway :8000]
    gw --> odoo["odoo :8069<br/>Odoo 18, stock + contacts"]
    gw --> gwdb[(gateway-db :5433<br/>audit_events, stock_snapshots)]
    odoo --> odoodb[(odoo-db)]
    init["odoo-init<br/>one-shot: -i stock,contacts"] -.-> odoodb
    beat["beat<br/>every GATEWAY_SNAPSHOT_INTERVAL_S"] --> redis[(redis)]
    redis --> worker["worker<br/>reports.stock_snapshot"]
    worker --> odoo
    worker --> gwdb
```

The worker and beat run from the gateway image. Beat only enqueues; the worker reads Odoo with
the same integration user and writes the snapshot the API serves from `GET /reports/stock`.

`make odoo-setup` creates the integration user and seed products through the gateway's own
client; `make odoo-apikey` issues its API key inside `odoo shell`, the one step Odoo does not
expose over RPC.
