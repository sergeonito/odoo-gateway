"""Bearer token verification: local dev tokens and Microsoft Entra ID access tokens."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import jwt
from pydantic import SecretStr

from odoo_gateway.auth.rbac import Principal, Role

_REQUIRED_CLAIMS = ["exp", "iat", "iss", "aud", "sub"]


class InvalidTokenError(Exception):
    """The token is missing, malformed, expired or not issued for this gateway."""


class TokenVerifier(Protocol):
    async def verify(self, token: str) -> Principal: ...


class SigningKeySource(Protocol):
    """What PyJWKClient offers; a protocol so tests can pass a local key."""

    def get_signing_key_from_jwt(self, token: str) -> jwt.PyJWK: ...


def _map_roles(raw: Any, role_map: Mapping[str, Role]) -> frozenset[Role]:
    """Unknown roles are dropped, never guessed: no mapping means no access."""
    if not isinstance(raw, list):
        return frozenset()
    return frozenset(role_map[name] for name in raw if isinstance(name, str) and name in role_map)


class DevTokenVerifier:
    """HS256 tokens signed by the gateway itself. Local development and tests only."""

    _role_map: Mapping[str, Role] = {role.value: role for role in Role}

    def __init__(self, secret: SecretStr, *, issuer: str, audience: str) -> None:
        self._secret = secret
        self._issuer = issuer
        self._audience = audience

    def issue(
        self,
        subject: str,
        roles: Iterable[Role],
        *,
        name: str | None = None,
        ttl: timedelta = timedelta(hours=1),
    ) -> str:
        now = datetime.now(UTC)
        claims = {
            "sub": subject,
            "name": name or subject,
            "roles": [role.value for role in roles],
            "iss": self._issuer,
            "aud": self._audience,
            "iat": now,
            "exp": now + ttl,
        }
        return jwt.encode(claims, self._secret.get_secret_value(), algorithm="HS256")

    async def verify(self, token: str) -> Principal:
        try:
            claims = jwt.decode(
                token,
                self._secret.get_secret_value(),
                algorithms=["HS256"],
                audience=self._audience,
                issuer=self._issuer,
                options={"require": _REQUIRED_CLAIMS},
            )
        except jwt.PyJWTError as exc:
            raise InvalidTokenError(str(exc)) from exc
        subject = str(claims["sub"])
        return Principal(
            subject=subject,
            display_name=str(claims.get("name") or subject),
            roles=_map_roles(claims.get("roles"), self._role_map),
        )


class EntraTokenVerifier:
    """Access tokens from Microsoft Entra ID (v2.0), checked against the tenant's signing keys.

    App roles arrive in the `roles` claim and are mapped to internal roles by configuration.
    The app registration must set `accessTokenAcceptedVersion: 2`, or the issuer will not match.
    """

    def __init__(
        self,
        *,
        tenant_id: str,
        client_id: str,
        role_map: Mapping[str, Role],
        keys: SigningKeySource | None = None,
    ) -> None:
        self._issuer = f"https://login.microsoftonline.com/{tenant_id}/v2.0"
        self._audience = client_id
        self._role_map = dict(role_map)
        self._keys: SigningKeySource = keys or jwt.PyJWKClient(
            f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys",
            cache_keys=True,
            lifespan=3600,
        )

    async def verify(self, token: str) -> Principal:
        try:
            # PyJWKClient fetches keys over blocking HTTP: keep it off the event loop.
            signing_key = await asyncio.to_thread(self._keys.get_signing_key_from_jwt, token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self._audience,
                issuer=self._issuer,
                options={"require": _REQUIRED_CLAIMS},
            )
        except jwt.PyJWTError as exc:
            raise InvalidTokenError(str(exc)) from exc
        # `oid` is the user's id across all apps in the tenant; `sub` differs per app.
        subject = str(claims.get("oid") or claims["sub"])
        name = claims.get("name") or claims.get("preferred_username") or subject
        return Principal(
            subject=subject,
            display_name=str(name),
            roles=_map_roles(claims.get("roles"), self._role_map),
        )
