"""Async client for Odoo's JSON-RPC endpoint (`/jsonrpc`).

Odoo 18 exposes JSON-RPC and XML-RPC. Odoo 19 deprecates both in favour of the JSON-2 API,
so this transport sits behind `OdooPort` (ports.py) and can be swapped without touching routes
(ADR-0003).
"""

from __future__ import annotations

import asyncio
import itertools
import random
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, Final

import httpx
from pydantic import SecretStr

from odoo_gateway.odoo.errors import (
    OdooAuthError,
    OdooError,
    OdooUnavailableError,
    from_jsonrpc_error,
)
from odoo_gateway.ports import Domain

if TYPE_CHECKING:
    from odoo_gateway.config import Settings

# Retried on transport errors. Writes are never retried: Odoo has no idempotency keys,
# so a retried `create` after a lost response would create a duplicate.
READ_ONLY_METHODS: Final = frozenset(
    {"search_read", "read", "search", "search_count", "fields_get", "name_search"}
)

_BACKOFF_BASE_S: Final = 0.2
_BACKOFF_CAP_S: Final = 2.0

Sleep = Callable[[float], Awaitable[None]]


class OdooClient:
    """One integration user, authenticated lazily; the uid is cached until Odoo rejects it."""

    def __init__(
        self,
        *,
        base_url: str,
        db: str,
        login: str,
        api_key: SecretStr,
        timeout_s: float = 10.0,
        read_retries: int = 2,
        http: httpx.AsyncClient | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._http = http or httpx.AsyncClient(base_url=base_url, timeout=timeout_s)
        self._owns_http = http is None
        self._db = db
        self._login = login
        self._api_key = api_key
        self._read_retries = read_retries
        self._sleep = sleep
        self._uid: int | None = None
        self._uid_lock = asyncio.Lock()
        self._request_ids = itertools.count(1)

    @classmethod
    def from_settings(cls, settings: Settings) -> OdooClient:
        return cls(
            base_url=settings.odoo_url,
            db=settings.odoo_db,
            login=settings.odoo_login,
            api_key=settings.odoo_api_key,
            timeout_s=settings.odoo_timeout_s,
            read_retries=settings.odoo_read_retries,
        )

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def search_read(
        self,
        model: str,
        domain: Domain,
        fields: list[str],
        *,
        limit: int,
        offset: int = 0,
        order: str | None = None,
    ) -> list[dict[str, Any]]:
        kwargs: dict[str, Any] = {"fields": fields, "limit": limit, "offset": offset}
        if order:
            kwargs["order"] = order
        result = await self.execute_kw(model, "search_read", [domain], kwargs)
        if not isinstance(result, list) or not all(isinstance(row, dict) for row in result):
            raise OdooError(f"{model}.search_read returned an unexpected shape")
        return result

    async def create(self, model: str, values: dict[str, Any]) -> int:
        result = await self.execute_kw(model, "create", [values])
        if isinstance(result, list) and len(result) == 1:
            result = result[0]
        if not isinstance(result, int) or isinstance(result, bool):
            raise OdooError(f"{model}.create returned an unexpected value")
        return result

    async def execute_kw(
        self,
        model: str,
        method: str,
        args: list[Any],
        kwargs: dict[str, Any] | None = None,
    ) -> Any:
        uid = await self._authenticated_uid()
        call_args = [
            self._db,
            uid,
            self._api_key.get_secret_value(),
            model,
            method,
            args,
            kwargs or {},
        ]
        attempts = 1 + (self._read_retries if method in READ_ONLY_METHODS else 0)
        for attempt in range(attempts):
            try:
                return await self._call("object", "execute_kw", call_args)
            except OdooUnavailableError:
                if attempt == attempts - 1:
                    raise
                await self._sleep(self._backoff(attempt))
            except OdooAuthError:
                self._uid = None  # key revoked or user disabled: re-authenticate next time
                raise
        raise AssertionError("unreachable")

    async def _authenticated_uid(self) -> int:
        if self._uid is not None:
            return self._uid
        async with self._uid_lock:
            if self._uid is None:
                result = await self._call(
                    "common",
                    "authenticate",
                    [self._db, self._login, self._api_key.get_secret_value(), {}],
                )
                # Odoo answers `false` (not an error) to bad credentials.
                if not isinstance(result, int) or isinstance(result, bool) or result <= 0:
                    raise OdooAuthError("Odoo rejected the integration user's credentials")
                self._uid = result
            return self._uid

    async def _call(self, service: str, method: str, args: list[Any]) -> Any:
        payload = {
            "jsonrpc": "2.0",
            "method": "call",
            "params": {"service": service, "method": method, "args": args},
            "id": next(self._request_ids),
        }
        try:
            response = await self._http.post("/jsonrpc", json=payload)
        except httpx.TransportError as exc:  # connect errors and timeouts
            raise OdooUnavailableError(f"Odoo is unreachable ({type(exc).__name__})") from exc
        if response.status_code >= 500:
            raise OdooUnavailableError(f"Odoo returned HTTP {response.status_code}")
        if response.status_code != 200:
            raise OdooError(f"Odoo returned HTTP {response.status_code}")
        try:
            body = response.json()
        except ValueError as exc:
            raise OdooError("Odoo returned a non-JSON response") from exc
        if not isinstance(body, dict):
            raise OdooError("Odoo returned an unexpected JSON-RPC envelope")
        if body.get("error") is not None:
            raise from_jsonrpc_error(body["error"])
        return body.get("result")

    @staticmethod
    def _backoff(attempt: int) -> float:
        """Exponential backoff with full jitter."""
        ceiling = min(_BACKOFF_CAP_S, _BACKOFF_BASE_S * 2**attempt)
        return random.uniform(0, ceiling)  # noqa: S311 - jitter, not cryptography
