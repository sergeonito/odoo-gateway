"""What the API layer needs from Odoo. Routes depend on this, never on the transport."""

from __future__ import annotations

from typing import Any, Protocol

# An Odoo domain: a list of (field, operator, value) triples and '&' / '|' / '!' operators.
type Domain = list[Any]


class OdooPort(Protocol):
    async def search_read(
        self,
        model: str,
        domain: Domain,
        fields: list[str],
        *,
        limit: int,
        offset: int = 0,
        order: str | None = None,
    ) -> list[dict[str, Any]]: ...

    async def create(self, model: str, values: dict[str, Any]) -> int: ...
