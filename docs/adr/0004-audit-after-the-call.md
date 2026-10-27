# ADR-0004: The audit event is written after the Odoo call

**Status:** accepted · 2026-10-22, `attempted` event added 2026-10-27

## Context

A write touches two systems: Odoo (the record) and the gateway's PostgreSQL (the audit event).
They cannot share a transaction.

## Decision

For v0.1 the gateway calls Odoo first, then commits the audit event with the outcome
(`allowed` with the new record id, or `failed` with the error class). Refusals (403) are
audited before anything reaches Odoo. The table is append-only at the database level: triggers
reject UPDATE, DELETE and TRUNCATE. Events hold field names and error classes, never the
submitted values, so the log does not become a second copy of personal data.

## The gap, and the first half of the fix

If the gateway dies after Odoo commits but before the audit commit, the record would exist with
no audit event at all. Since 2026-10-27 every write is audited twice: an `attempted` event with
the field names is committed before the Odoo call, and the outcome event (`allowed` with the
record id, or `failed` with the error class) after it. A crash in between now leaves an
`attempted` row with no outcome, which is a reconciliation query, not a silent loss.

A client retry after such a crash would still create a second record. That is the remaining
gap: closed by `Idempotency-Key` (next), which lets the gateway recognise the retry and answer
it from the stored result instead of calling Odoo again.
