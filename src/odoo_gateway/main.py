"""Application factory. `uvicorn odoo_gateway.main:create_app --factory`."""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from odoo_gateway.api.routes import router
from odoo_gateway.audit.log import AuditLog
from odoo_gateway.auth.tokens import DevTokenVerifier, EntraTokenVerifier, TokenVerifier
from odoo_gateway.config import Settings
from odoo_gateway.container import Container
from odoo_gateway.odoo.client import OdooClient
from odoo_gateway.odoo.errors import (
    OdooError,
    OdooNotFoundError,
    OdooUnavailableError,
    OdooValidationError,
)
from odoo_gateway.reports.snapshot import StockReports

log = logging.getLogger("odoo_gateway")

_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{8,64}")


def build_verifier(settings: Settings) -> TokenVerifier:
    if settings.auth_mode == "entra":
        return EntraTokenVerifier(
            tenant_id=settings.entra_tenant_id,
            client_id=settings.entra_client_id,
            role_map=settings.entra_role_map,
        )
    return DevTokenVerifier(
        settings.dev_jwt_secret,
        issuer=settings.dev_jwt_issuer,
        audience=settings.jwt_audience,
    )


def create_app(settings: Settings | None = None, *, container: Container | None = None) -> FastAPI:
    """Build the app. Tests pass a ready `container`; production builds one from settings."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if container is not None:
            app.state.container = container
            yield
            return
        cfg = settings or Settings()
        engine = create_async_engine(cfg.database_url, pool_pre_ping=True)
        odoo = OdooClient.from_settings(cfg)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        app.state.container = Container(
            odoo=odoo,
            audit=AuditLog(sessions),
            verifier=build_verifier(cfg),
            reports=StockReports(sessions),
        )
        try:
            yield
        finally:
            await odoo.aclose()
            await engine.dispose()

    app = FastAPI(
        title="Odoo Gateway",
        version="0.1.0",
        description="One RBAC and one audit log in front of Odoo for internal products.",
        lifespan=lifespan,
    )
    if container is not None:  # ASGI test transports do not run the lifespan
        app.state.container = container

    @app.middleware("http")
    async def request_id(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get("X-Request-ID", "")
        rid = incoming if _REQUEST_ID.fullmatch(incoming) else uuid.uuid4().hex
        request.state.request_id = rid
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        return response

    _install_error_handlers(app)
    app.include_router(router)
    return app


def _install_error_handlers(app: FastAPI) -> None:
    def problem(code: int, detail: str) -> JSONResponse:
        return JSONResponse(status_code=code, content={"detail": detail})

    @app.exception_handler(OdooError)
    async def odoo_error(request: Request, exc: OdooError) -> JSONResponse:
        rid = getattr(request.state, "request_id", "-")
        if isinstance(exc, OdooNotFoundError):
            return problem(status.HTTP_404_NOT_FOUND, "Not found")
        if isinstance(exc, OdooValidationError):
            # Odoo's UserError/ValidationError messages are written for end users.
            return problem(422, exc.message)  # constant was renamed across Starlette versions
        if isinstance(exc, OdooUnavailableError):
            log.warning("odoo unavailable rid=%s: %s", rid, exc.message)
            return problem(status.HTTP_503_SERVICE_UNAVAILABLE, "Odoo is unavailable")
        # Auth/access errors mean the integration user is misconfigured: a server fault,
        # and its details are not the caller's business.
        log.error("odoo error rid=%s %s: %s", rid, exc.odoo_exception, exc.message)
        return problem(status.HTTP_502_BAD_GATEWAY, "Upstream error")
