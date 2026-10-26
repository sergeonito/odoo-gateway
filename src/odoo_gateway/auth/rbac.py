"""Roles, permissions and the authenticated principal.

Roles are coarse (who you are); permissions are fine (what an endpoint needs).
Endpoints depend on permissions only, so a role can change without touching routes.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final


class Role(StrEnum):
    VIEWER = "viewer"
    OPERATOR = "operator"
    ADMIN = "admin"


class Permission(StrEnum):
    INVENTORY_READ = "inventory:read"
    PARTNERS_WRITE = "partners:write"
    AUDIT_READ = "audit:read"


ROLE_PERMISSIONS: Final[Mapping[Role, frozenset[Permission]]] = {
    Role.VIEWER: frozenset({Permission.INVENTORY_READ}),
    Role.OPERATOR: frozenset({Permission.INVENTORY_READ, Permission.PARTNERS_WRITE}),
    Role.ADMIN: frozenset(Permission),
}


@dataclass(frozen=True, slots=True)
class Principal:
    """Who is calling. `subject` is stable (Entra `oid`), `display_name` is for humans."""

    subject: str
    display_name: str
    roles: frozenset[Role]

    @property
    def permissions(self) -> frozenset[Permission]:
        granted: set[Permission] = set()
        for role in self.roles:
            granted |= ROLE_PERMISSIONS[role]
        return frozenset(granted)

    def can(self, permission: Permission) -> bool:
        return permission in self.permissions
