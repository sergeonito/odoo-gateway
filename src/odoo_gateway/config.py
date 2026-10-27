"""Runtime configuration. Every secret comes from the environment, never from code."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from odoo_gateway.auth.rbac import Role

_MIN_DEV_SECRET_LENGTH = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GATEWAY_", env_file=".env", extra="ignore")

    environment: Literal["local", "test", "production"] = "local"

    # Odoo: one least-privileged integration user (ADR-0002).
    odoo_url: str = "http://localhost:8069"
    odoo_db: str = "odoo"
    odoo_login: str = "admin"
    odoo_api_key: SecretStr = SecretStr("")
    odoo_timeout_s: float = Field(default=10.0, gt=0, le=120)
    odoo_read_retries: int = Field(default=2, ge=0, le=5)

    # Gateway's own database: audit log and stock snapshots.
    database_url: str = "postgresql+asyncpg://gateway:gateway@localhost:5433/gateway"

    # Background jobs: Celery broker and how often the stock snapshot is taken.
    # 6380 is where the compose stack publishes its Redis (6379 is often taken on a laptop).
    redis_url: str = "redis://localhost:6380/0"
    snapshot_interval_s: int = Field(default=900, ge=30, le=86_400)

    # Authentication: "dev" = HS256 tokens signed here; "entra" = Microsoft Entra ID.
    auth_mode: Literal["dev", "entra"] = "dev"
    jwt_audience: str = "odoo-gateway"
    dev_jwt_secret: SecretStr = SecretStr("")
    dev_jwt_issuer: str = "odoo-gateway-dev"
    entra_tenant_id: str = ""
    entra_client_id: str = ""
    entra_role_map: dict[str, Role] = Field(
        default_factory=lambda: {
            "Gateway.Viewer": Role.VIEWER,
            "Gateway.Operator": Role.OPERATOR,
            "Gateway.Admin": Role.ADMIN,
        }
    )

    @field_validator("odoo_url")
    @classmethod
    def _http_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("odoo_url must start with http:// or https://")
        return value.rstrip("/")

    @model_validator(mode="after")
    def _auth_is_safe(self) -> Self:
        if self.auth_mode == "dev":
            if self.environment == "production":
                raise ValueError("dev tokens are not allowed in production; use auth_mode=entra")
            secret = self.dev_jwt_secret.get_secret_value()
            if len(secret) < _MIN_DEV_SECRET_LENGTH or secret.startswith("change-me"):
                raise ValueError(
                    "GATEWAY_DEV_JWT_SECRET must be a random string of 32+ characters "
                    "(run `make env` to generate one)"
                )
        elif not (self.entra_tenant_id and self.entra_client_id):
            raise ValueError("auth_mode=entra needs GATEWAY_ENTRA_TENANT_ID and _CLIENT_ID")
        return self
