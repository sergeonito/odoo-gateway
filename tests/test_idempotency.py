"""`Idempotency-Key` on writes: retries get the first result, never a second Odoo record."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import update

from odoo_gateway.audit.log import AuditLog
from odoo_gateway.auth.rbac import Role
from odoo_gateway.container import Container
from odoo_gateway.idempotency.models import IdempotencyKey
from odoo_gateway.idempotency.store import (
    Claim,
    IdempotencyInProgressError,
    IdempotencyStore,
    Replay,
    fingerprint,
)
from odoo_gateway.odoo.errors import OdooValidationError
from tests.conftest import CrashingOdoo, FakeOdoo, client_for

KEY = "order-2026-10-27-0001"
BODY = {"name": "Acme", "is_company": True}


def _headers(auth: Any, *, key: str = KEY, sub: str = "op-1") -> dict[str, str]:
    return {**auth(Role.OPERATOR, sub=sub), "Idempotency-Key": key}


async def test_retry_with_the_same_key_is_answered_from_the_stored_result(
    client: httpx.AsyncClient, odoo: FakeOdoo, audit: AuditLog, auth: Any
) -> None:
    first = await client.post("/partners", json=BODY, headers=_headers(auth))
    retry = await client.post("/partners", json=BODY, headers=_headers(auth))

    assert (first.status_code, retry.status_code) == (201, 201)
    assert retry.json() == first.json()
    assert "Idempotency-Replayed" not in first.headers
    assert retry.headers["Idempotency-Replayed"] == "true"
    assert len(odoo.calls) == 1  # Odoo saw one create
    events = await audit.recent(limit=10)
    assert [e.outcome for e in events] == ["allowed", "attempted"]  # the replay adds nothing
    assert events[0].details["idempotency_key"] == KEY


async def test_same_key_with_a_different_body_is_rejected(
    client: httpx.AsyncClient, odoo: FakeOdoo, auth: Any
) -> None:
    assert (await client.post("/partners", json=BODY, headers=_headers(auth))).status_code == 201
    other = await client.post("/partners", json={"name": "Other"}, headers=_headers(auth))
    assert other.status_code == 422
    assert "different request" in other.json()["detail"]
    assert len(odoo.calls) == 1


async def test_concurrent_request_with_the_same_key_gets_409(
    client: httpx.AsyncClient,
    odoo: FakeOdoo,
    idempotency: IdempotencyStore,
    audit: AuditLog,
    auth: Any,
) -> None:
    claimed = await idempotency.claim(
        actor_subject="op-1", key=KEY, action="partners.create", values=BODY
    )
    assert isinstance(claimed, Claim)

    response = await client.post("/partners", json=BODY, headers=_headers(auth))
    assert response.status_code == 409
    assert odoo.calls == []
    assert await audit.recent(limit=10) == []  # refused before anything was attempted


async def test_keys_are_scoped_per_caller(client: httpx.AsyncClient, auth: Any) -> None:
    one = await client.post("/partners", json=BODY, headers=_headers(auth, sub="op-1"))
    two = await client.post("/partners", json=BODY, headers=_headers(auth, sub="op-2"))
    assert one.json()["id"] != two.json()["id"]
    assert "Idempotency-Replayed" not in two.headers


async def test_odoo_failure_releases_the_key_for_a_retry(
    client: httpx.AsyncClient, odoo: FakeOdoo, auth: Any
) -> None:
    odoo.fail_with = OdooValidationError("Email already used")
    failed = await client.post("/partners", json=BODY, headers=_headers(auth))
    retry = await client.post("/partners", json=BODY, headers=_headers(auth))

    assert (failed.status_code, retry.status_code) == (422, 201)
    assert "Idempotency-Replayed" not in retry.headers  # a real second attempt, not a replay
    assert len(odoo.calls) == 2


async def test_crash_after_the_odoo_call_blocks_the_retry_instead_of_duplicating(
    container: Container, odoo: FakeOdoo, auth: Any
) -> None:
    """ADR-0004: Odoo committed, then the gateway died. A retry must not create a second record."""
    async with client_for(container, CrashingOdoo()) as crashing_client:
        with pytest.raises(RuntimeError):
            await crashing_client.post("/partners", json=BODY, headers=_headers(auth))

    async with client_for(container, odoo) as healthy_client:
        retry = await healthy_client.post("/partners", json=BODY, headers=_headers(auth))
    assert retry.status_code == 409  # the key stays claimed until an operator reconciles it
    assert odoo.calls == []


async def test_key_format_is_validated(
    client: httpx.AsyncClient, odoo: FakeOdoo, auth: Any
) -> None:
    for bad in ("short", "has space 123", "x" * 129):
        response = await client.post("/partners", json=BODY, headers=_headers(auth, key=bad))
        assert response.status_code == 422, bad
    assert odoo.calls == []


def test_fingerprint_ignores_key_order_but_not_values_or_action() -> None:
    a = fingerprint("partners.create", {"name": "Acme", "is_company": True})
    b = fingerprint("partners.create", {"is_company": True, "name": "Acme"})
    assert a == b
    assert a != fingerprint("partners.create", {"name": "Acme", "is_company": False})
    assert a != fingerprint("partners.update", {"name": "Acme", "is_company": True})


async def test_store_release_only_drops_claims_in_progress(idempotency: IdempotencyStore) -> None:
    claim = await idempotency.claim(actor_subject="a", key=KEY, action="x", values={})
    assert isinstance(claim, Claim)
    await idempotency.complete(claim, status=201, body={"id": 5})
    await idempotency.release(claim)  # no-op: the result is already stored

    replay = await idempotency.claim(actor_subject="a", key=KEY, action="x", values={})
    assert replay == Replay(201, {"id": 5})

    fresh = await idempotency.claim(actor_subject="a", key="another-key-1", action="x", values={})
    assert isinstance(fresh, Claim)
    with pytest.raises(IdempotencyInProgressError):
        await idempotency.claim(actor_subject="a", key="another-key-1", action="x", values={})


async def _age(idempotency: IdempotencyStore, key: str, seconds: int) -> None:
    """Backdate a claim: the request that made it died `seconds` ago."""
    sessions = idempotency._sessions
    async with sessions.begin() as session:
        await session.execute(
            update(IdempotencyKey)
            .where(IdempotencyKey.key == key)
            .values(created_at=datetime.now(UTC) - timedelta(seconds=seconds))
        )


async def test_stuck_lists_only_old_claims_in_progress(idempotency: IdempotencyStore) -> None:
    old = await idempotency.claim(actor_subject="a", key="old-key-0001", action="x", values={})
    done = await idempotency.claim(actor_subject="a", key="done-key-001", action="x", values={})
    await idempotency.claim(actor_subject="a", key="new-key-0001", action="x", values={})
    assert isinstance(old, Claim)
    assert isinstance(done, Claim)
    await idempotency.complete(done, status=201, body={"id": 1})
    await _age(idempotency, "old-key-0001", 600)
    await _age(idempotency, "done-key-001", 600)

    stuck = await idempotency.stuck(older_than=timedelta(seconds=300))

    assert [(c.key, c.action) for c in stuck] == [("old-key-0001", "x")]


async def test_admin_sees_stuck_keys_with_the_request_id_of_the_attempt(
    container: Container, idempotency: IdempotencyStore, odoo: FakeOdoo, auth: Any
) -> None:
    async with client_for(container, CrashingOdoo()) as crashing_client:
        with pytest.raises(RuntimeError):
            await crashing_client.post(
                "/partners",
                json=BODY,
                headers={**_headers(auth), "X-Request-ID": "req-crashed-01"},
            )
    await _age(idempotency, KEY, 600)

    async with client_for(container, odoo) as client:
        fresh = await client.get("/reconciliation/idempotency", headers=auth(Role.ADMIN))
        listed = await client.get(
            "/reconciliation/idempotency", params={"older_than_s": 60}, headers=auth(Role.ADMIN)
        )
    assert fresh.status_code == 200
    assert listed.status_code == 200
    [row] = listed.json()
    assert (row["actor_subject"], row["key"], row["action"]) == ("op-1", KEY, "partners.create")
    assert row["request_id"] == "req-crashed-01"
    assert row["age_s"] >= 600


@pytest.mark.parametrize(
    ("resolution", "resource_id", "retry_status", "retry_replayed"),
    [("release", None, 201, False), ("complete", 77, 201, True)],
)
async def test_reconcile_release_or_complete_then_the_retry_behaves(
    client: httpx.AsyncClient,
    idempotency: IdempotencyStore,
    odoo: FakeOdoo,
    audit: AuditLog,
    auth: Any,
    resolution: str,
    resource_id: int | None,
    retry_status: int,
    retry_replayed: bool,
) -> None:
    claimed = await idempotency.claim(
        actor_subject="op-1", key=KEY, action="partners.create", values=BODY
    )
    assert isinstance(claimed, Claim)
    body = {
        "actor_subject": "op-1",
        "key": KEY,
        "resolution": resolution,
        "resource_id": resource_id,
    }

    resolved = await client.post("/reconciliation/idempotency", json=body, headers=auth(Role.ADMIN))
    assert resolved.status_code == 200, resolved.text

    retry = await client.post("/partners", json=BODY, headers=_headers(auth))
    assert retry.status_code == retry_status
    assert ("Idempotency-Replayed" in retry.headers) is retry_replayed
    if retry_replayed:
        assert retry.json() == {"id": resource_id}
        assert odoo.calls == []  # Odoo already has the record
    else:
        assert len(odoo.calls) == 1  # a real second attempt
    event = next(e for e in await audit.recent(limit=10) if e.action == "idempotency.reconcile")
    assert (event.outcome, event.resource_id, event.details["resolution"]) == (
        "allowed",
        resource_id,
        resolution,
    )


async def test_reconcile_validates_body_and_needs_a_claim_in_progress(
    client: httpx.AsyncClient, auth: Any
) -> None:
    headers = auth(Role.ADMIN)
    bad = [
        {"actor_subject": "op-1", "key": KEY, "resolution": "complete"},  # no id
        {"actor_subject": "op-1", "key": KEY, "resolution": "release", "resource_id": 5},
        {"actor_subject": "op-1", "key": "short", "resolution": "release"},
    ]
    for body in bad:
        assert (
            await client.post("/reconciliation/idempotency", json=body, headers=headers)
        ).status_code == 422, body
    missing = {"actor_subject": "op-1", "key": KEY, "resolution": "release"}
    assert (
        await client.post("/reconciliation/idempotency", json=missing, headers=headers)
    ).status_code == 404


async def test_only_admin_reconciles(client: httpx.AsyncClient, audit: AuditLog, auth: Any) -> None:
    response = await client.get(
        "/reconciliation/idempotency", headers=auth(Role.OPERATOR, sub="op-9")
    )
    assert response.status_code == 403
    [event] = await audit.recent(limit=10)
    assert (event.actor_subject, event.action, event.outcome) == (
        "op-9",
        "authz.idempotency:reconcile",
        "denied",
    )
