# ADR-0004: The audit event is written after the Odoo call

**Status:** accepted · 2026-10-22; gap closed 2026-10-27 (`attempted` event, `Idempotency-Key`)

## Context

A write touches two systems: Odoo (the record) and the gateway's PostgreSQL (the audit event).
They cannot share a transaction.

## Decision

For v0.1 the gateway calls Odoo first, then commits the audit event with the outcome
(`allowed` with the new record id, or `failed` with the error class). Refusals (403) are
audited before anything reaches Odoo. The table is append-only at the database level: triggers
reject UPDATE, DELETE and TRUNCATE. Events hold field names and error classes, never the
submitted values, so the log does not become a second copy of personal data.

## The gap and how it is closed

If the gateway dies after Odoo commits but before the audit commit, the record would exist with
no audit event at all, and a client retry would create it twice.

**Two audit events per write.** An `attempted` event with the field names is committed before
the Odoo call, the outcome event (`allowed` with the record id, or `failed` with the error
class) after it. A crash in between leaves an `attempted` row with no outcome: a reconciliation
query, not a silent loss.

**`Idempotency-Key` on writes** (optional header, 8–128 chars, scoped per caller). Before the
Odoo call the gateway inserts a row into `idempotency_keys`; the primary key makes a concurrent
duplicate lose cleanly. After the outcome event it stores the response on that row. A retry
with the same key gets the stored response with `Idempotency-Replayed: true` and Odoo is not
called; the same key with a different body is a 422; a key still in progress is a 409. If Odoo
answers with a known error the key is released so the client may retry; if the gateway dies
mid-flight the key stays claimed, so the retry gets 409 instead of a duplicate, and the
`attempted` event (which carries the key) tells an operator what to check in Odoo.

## Consequences

- No duplicate records from retries, no unaudited writes: the two failure modes of the
  two-system write are both visible and both bounded.
- Clients that want safe retries must send a key; without one the write behaves as before.
- Claims abandoned by a crash need a human (or a reconciliation job, not written yet) to
  compare the `attempted` event with Odoo and release or complete the row by hand.
- The row stores the response body (a record id) and a SHA-256 of the request, never the
  submitted values.
