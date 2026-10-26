"""Issue a local dev token: `uv run python -m odoo_gateway.dev_token operator --sub alice`."""

from __future__ import annotations

import argparse
import sys

from odoo_gateway.auth.rbac import Role
from odoo_gateway.auth.tokens import DevTokenVerifier
from odoo_gateway.config import Settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roles", nargs="+", choices=[role.value for role in Role])
    parser.add_argument("--sub", default="dev-user")
    parser.add_argument("--name", default=None)
    args = parser.parse_args(argv)

    settings = Settings()
    if settings.auth_mode != "dev":
        print("auth_mode is not 'dev'; dev tokens would be rejected", file=sys.stderr)
        return 1
    verifier = DevTokenVerifier(
        settings.dev_jwt_secret,
        issuer=settings.dev_jwt_issuer,
        audience=settings.jwt_audience,
    )
    print(verifier.issue(args.sub, [Role(r) for r in args.roles], name=args.name))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
