"""Roles → permissions, dev tokens, and Entra ID tokens checked against a local key."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm
from pydantic import SecretStr

from odoo_gateway.auth.rbac import Permission, Principal, Role
from odoo_gateway.auth.tokens import DevTokenVerifier, EntraTokenVerifier, InvalidTokenError

TENANT = "test-tenant"
CLIENT_ID = "test-client-id"


@pytest.mark.parametrize(
    ("roles", "permission", "allowed"),
    [
        ({Role.VIEWER}, Permission.INVENTORY_READ, True),
        ({Role.VIEWER}, Permission.PARTNERS_WRITE, False),
        ({Role.OPERATOR}, Permission.PARTNERS_WRITE, True),
        ({Role.OPERATOR}, Permission.AUDIT_READ, False),
        ({Role.ADMIN}, Permission.AUDIT_READ, True),
        (set(), Permission.INVENTORY_READ, False),
    ],
)
def test_role_permissions(roles: set[Role], permission: Permission, allowed: bool) -> None:
    principal = Principal(subject="u", display_name="U", roles=frozenset(roles))
    assert principal.can(permission) is allowed


async def test_dev_token_round_trip(verifier: DevTokenVerifier) -> None:
    principal = await verifier.verify(verifier.issue("alice", [Role.OPERATOR], name="Alice"))
    assert principal == Principal("alice", "Alice", frozenset({Role.OPERATOR}))


async def test_dev_token_from_another_secret_is_rejected(verifier: DevTokenVerifier) -> None:
    other = DevTokenVerifier(SecretStr("y" * 48), issuer="test", audience="odoo-gateway")
    with pytest.raises(InvalidTokenError):
        await verifier.verify(other.issue("mallory", [Role.ADMIN]))


async def test_expired_dev_token_is_rejected(verifier: DevTokenVerifier) -> None:
    token = verifier.issue("alice", [Role.VIEWER], ttl=timedelta(seconds=-1))
    with pytest.raises(InvalidTokenError):
        await verifier.verify(token)


class _LocalKeys:
    """Stands in for PyJWKClient: serves one public key."""

    def __init__(self, public_jwk: dict[str, Any]) -> None:
        self._jwk = jwt.PyJWK.from_dict(public_jwk)

    def get_signing_key_from_jwt(self, token: str) -> jwt.PyJWK:
        return self._jwk


@pytest.fixture
def entra() -> tuple[EntraTokenVerifier, rsa.RSAPrivateKey]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    public_jwk |= {"kid": "test-key", "alg": "RS256", "use": "sig"}
    verifier = EntraTokenVerifier(
        tenant_id=TENANT,
        client_id=CLIENT_ID,
        role_map={"Gateway.Viewer": Role.VIEWER, "Gateway.Admin": Role.ADMIN},
        keys=_LocalKeys(public_jwk),
    )
    return verifier, private_key


def _entra_token(private_key: rsa.RSAPrivateKey, **overrides: Any) -> str:
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0",
        "aud": CLIENT_ID,
        "sub": "per-app-subject",
        "oid": "user-object-id",
        "name": "Olena K.",
        "roles": ["Gateway.Viewer", "Some.Other.Role"],
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    claims.update(overrides)
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "test-key"})


async def test_entra_token_maps_app_roles_and_uses_oid(
    entra: tuple[EntraTokenVerifier, rsa.RSAPrivateKey],
) -> None:
    verifier, key = entra
    principal = await verifier.verify(_entra_token(key))
    assert principal.subject == "user-object-id"
    assert principal.display_name == "Olena K."
    assert principal.roles == frozenset({Role.VIEWER})  # unknown roles are dropped


@pytest.mark.parametrize(
    "overrides",
    [
        {"aud": "some-other-app"},
        {"iss": "https://sts.windows.net/someone-else/"},
        {"exp": datetime.now(UTC) - timedelta(minutes=1)},
    ],
)
async def test_entra_token_for_wrong_audience_issuer_or_time_is_rejected(
    entra: tuple[EntraTokenVerifier, rsa.RSAPrivateKey], overrides: dict[str, Any]
) -> None:
    verifier, key = entra
    with pytest.raises(InvalidTokenError):
        await verifier.verify(_entra_token(key, **overrides))


async def test_entra_token_signed_by_another_key_is_rejected(
    entra: tuple[EntraTokenVerifier, rsa.RSAPrivateKey],
) -> None:
    verifier, _ = entra
    stranger = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(InvalidTokenError):
        await verifier.verify(_entra_token(stranger))
