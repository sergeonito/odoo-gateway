"""JSON-RPC client against a mocked Odoo: payloads, retries, error mapping."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx
from pydantic import SecretStr

from odoo_gateway.odoo.client import OdooClient
from odoo_gateway.odoo.errors import (
    OdooAccessDeniedError,
    OdooAuthError,
    OdooUnavailableError,
    OdooValidationError,
)

URL = "http://odoo.test"


async def _no_sleep(_: float) -> None:
    return None


def _client() -> OdooClient:
    return OdooClient(
        base_url=URL,
        db="odoo",
        login="integration",
        api_key=SecretStr("key-123"),
        read_retries=2,
        sleep=_no_sleep,
    )


def _ok(result: Any) -> httpx.Response:
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": result})


def _odoo_error(name: str, message: str) -> httpx.Response:
    error = {
        "code": 200,
        "message": "Odoo Server Error",
        "data": {"name": name, "message": message, "debug": "Traceback (secret internals)"},
    }
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "error": error})


def _params(request: httpx.Request) -> dict[str, Any]:
    params: dict[str, Any] = json.loads(request.content)["params"]
    return params


@respx.mock
async def test_authenticates_once_and_builds_execute_kw_payload() -> None:
    route = respx.post(f"{URL}/jsonrpc").mock(
        side_effect=[_ok(7), _ok([{"id": 1, "name": "Prop"}]), _ok([])]
    )
    odoo = _client()

    rows = await odoo.search_read("product.product", [("active", "=", True)], ["name"], limit=5)
    await odoo.search_read("product.product", [], ["name"], limit=5)

    assert rows == [{"id": 1, "name": "Prop"}]
    assert route.call_count == 3  # one authenticate, two calls
    auth = _params(route.calls[0].request)
    assert auth["service"] == "common"
    assert auth["args"] == ["odoo", "integration", "key-123", {}]
    call = _params(route.calls[1].request)
    assert call["service"] == "object"
    assert call["method"] == "execute_kw"
    assert call["args"][:5] == ["odoo", 7, "key-123", "product.product", "search_read"]
    assert call["args"][6] == {"fields": ["name"], "limit": 5, "offset": 0}


@respx.mock
async def test_bad_credentials_raise_auth_error() -> None:
    respx.post(f"{URL}/jsonrpc").mock(return_value=_ok(False))
    with pytest.raises(OdooAuthError):
        await _client().search_read("res.partner", [], ["name"], limit=1)


@respx.mock
async def test_read_is_retried_on_transport_error() -> None:
    route = respx.post(f"{URL}/jsonrpc").mock(
        side_effect=[_ok(7), httpx.ConnectError("refused"), httpx.Response(502), _ok([])]
    )
    assert await _client().search_read("res.partner", [], ["name"], limit=1) == []
    assert route.call_count == 4


@respx.mock
async def test_read_gives_up_after_retries() -> None:
    route = respx.post(f"{URL}/jsonrpc").mock(
        side_effect=[_ok(7)] + [httpx.ReadTimeout("slow")] * 3
    )
    with pytest.raises(OdooUnavailableError):
        await _client().search_read("res.partner", [], ["name"], limit=1)
    assert route.call_count == 4  # authenticate + 1 try + 2 retries


@respx.mock
async def test_create_is_never_retried() -> None:
    route = respx.post(f"{URL}/jsonrpc").mock(
        side_effect=[_ok(7), httpx.ReadTimeout("lost response"), _ok(42)]
    )
    with pytest.raises(OdooUnavailableError):
        await _client().create("res.partner", {"name": "Acme"})
    assert route.call_count == 2  # a retry could have created a duplicate


@respx.mock
async def test_create_returns_new_id() -> None:
    respx.post(f"{URL}/jsonrpc").mock(side_effect=[_ok(7), _ok(42)])
    assert await _client().create("res.partner", {"name": "Acme"}) == 42


@pytest.mark.parametrize(
    ("odoo_exception", "expected"),
    [
        ("odoo.exceptions.AccessError", OdooAccessDeniedError),
        ("odoo.exceptions.ValidationError", OdooValidationError),
        ("odoo.exceptions.UserError", OdooValidationError),
    ],
)
@respx.mock
async def test_odoo_exceptions_are_mapped_without_traceback(
    odoo_exception: str, expected: type[Exception]
) -> None:
    respx.post(f"{URL}/jsonrpc").mock(
        side_effect=[_ok(7), _odoo_error(odoo_exception, "Not allowed here")]
    )
    with pytest.raises(expected) as caught:
        await _client().create("res.partner", {"name": "Acme"})
    assert "Traceback" not in str(caught.value)
    assert str(caught.value) == "Not allowed here"


@respx.mock
async def test_revoked_key_forces_reauthentication() -> None:
    route = respx.post(f"{URL}/jsonrpc").mock(
        side_effect=[
            _ok(7),
            _odoo_error("odoo.exceptions.AccessDenied", "Access Denied"),
            _ok(8),
            _ok([]),
        ]
    )
    odoo = _client()
    with pytest.raises(OdooAuthError):
        await odoo.search_read("res.partner", [], ["name"], limit=1)
    await odoo.search_read("res.partner", [], ["name"], limit=1)
    assert _params(route.calls[2].request)["method"] == "authenticate"
