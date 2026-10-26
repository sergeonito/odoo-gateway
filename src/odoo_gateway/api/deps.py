"""FastAPI dependencies: the container, the caller, and permission checks."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from odoo_gateway.audit.models import Outcome
from odoo_gateway.auth.rbac import Permission, Principal
from odoo_gateway.auth.tokens import InvalidTokenError
from odoo_gateway.container import Container

_bearer = HTTPBearer(auto_error=False)
_CHALLENGE = {"WWW-Authenticate": "Bearer"}


def get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


def get_request_id(request: Request) -> str:
    request_id: str = request.state.request_id
    return request_id


ContainerDep = Annotated[Container, Depends(get_container)]
RequestIdDep = Annotated[str, Depends(get_request_id)]


async def get_principal(
    container: ContainerDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token", _CHALLENGE)
    try:
        return await container.verifier.verify(credentials.credentials)
    except InvalidTokenError:
        # The reason stays in our logs; the client only learns the token was not accepted.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token", _CHALLENGE) from None


def require(permission: Permission) -> Callable[..., Awaitable[Principal]]:
    """Dependency factory: the route runs only if the caller holds `permission`.

    Every refusal is audited, so probing for rights leaves a trail.
    """

    async def check(
        container: ContainerDep,
        request_id: RequestIdDep,
        principal: Annotated[Principal, Depends(get_principal)],
    ) -> Principal:
        if not principal.can(permission):
            await container.audit.record(
                principal=principal,
                action=f"authz.{permission.value}",
                outcome=Outcome.DENIED,
                request_id=request_id,
            )
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")
        return principal

    return check
