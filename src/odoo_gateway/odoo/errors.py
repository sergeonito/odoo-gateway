"""Errors from Odoo, mapped to what the gateway can act on.

The Odoo traceback (`error.data.debug`) is never kept: it stays out of logs and responses.
"""

from __future__ import annotations

from typing import Any


class OdooError(Exception):
    """Any failure from Odoo or the link to it."""

    def __init__(self, message: str, *, odoo_exception: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.odoo_exception = odoo_exception


class OdooUnavailableError(OdooError):
    """Transport error, timeout or HTTP 5xx. Safe to retry for read-only calls."""


class OdooAuthError(OdooError):
    """The integration user could not authenticate (wrong key, disabled user)."""


class OdooAccessDeniedError(OdooError):
    """Odoo's AccessError: the integration user lacks rights. A gateway misconfiguration."""


class OdooValidationError(OdooError):
    """ValidationError / UserError: the data broke an Odoo rule. Message is user-facing."""


class OdooNotFoundError(OdooError):
    """MissingError: the record does not exist (or was deleted)."""


_EXCEPTIONS: dict[str, type[OdooError]] = {
    "odoo.exceptions.AccessDenied": OdooAuthError,
    "odoo.exceptions.AccessError": OdooAccessDeniedError,
    "odoo.exceptions.ValidationError": OdooValidationError,
    "odoo.exceptions.UserError": OdooValidationError,
    "odoo.exceptions.MissingError": OdooNotFoundError,
}

_MAX_MESSAGE = 500


def from_jsonrpc_error(error: Any) -> OdooError:
    """Turn a JSON-RPC `error` object into a typed exception."""
    data = error.get("data") if isinstance(error, dict) else None
    name = data.get("name") if isinstance(data, dict) else None
    message = data.get("message") if isinstance(data, dict) else None
    if not message and isinstance(error, dict):
        message = error.get("message")
    error_class = _EXCEPTIONS.get(name, OdooError) if isinstance(name, str) else OdooError
    return error_class(
        str(message or "Odoo error")[:_MAX_MESSAGE],
        odoo_exception=name if isinstance(name, str) else None,
    )
