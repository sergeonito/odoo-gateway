# ADR-0004: The audit event is written after the Odoo call

**Status:** accepted for v0.1, with a known gap · 2026-10-22

## Context

A write touches two systems: Odoo (the record) and the gateway's PostgreSQL (the audit event).
They cannot share a transaction.

## Decision

For v0.1 the gateway calls Odoo first, then commits the audit event with the outcome
(`allowed` with the new record id, or `failed` with the error class). Refusals (403) are
audited before anything reaches Odoo. The table is append-only at the database level: triggers
reject UPDATE, DELETE and TRUNCATE. Events hold field names and error classes, never the
submitted values, so the log does not become a second copy of personal data.

## Known gap

If the gateway dies after Odoo commits but before the audit commit, the record exists with no
audit event. If the audit commit fails, the caller gets a 500 although the record was created,
and a retry would create a duplicate.

## Way out (planned)

Write an `attempted` event with an idempotency key before calling Odoo, then a second event
with the outcome. A reconciliation job flags `attempted` events with no outcome. The same key,
sent by clients as `Idempotency-Key`, lets the gateway answer retries without a second create.
