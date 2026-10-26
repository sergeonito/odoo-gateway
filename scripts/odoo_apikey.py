# Runs inside `odoo shell` (make odoo-apikey) and prints a new API key for the integration user.
# Odoo generates API keys with a private method that cannot be called over RPC, so this is the
# one step that happens inside Odoo rather than through the gateway's client.
from datetime import date, timedelta
from typing import Any


def main(env: Any) -> None:
    user = env["res.users"].search([("login", "=", "gateway")], limit=1)
    if not user:
        raise SystemExit("integration user not found: run `make odoo-setup` first")
    expires = date.today() + timedelta(days=365)
    keys = env["res.users.apikeys"].with_user(user).sudo()
    key = keys._generate(None, "odoo-gateway", expires)
    env.cr.commit()
    print(key)


main(env)  # type: ignore[name-defined]  # noqa: F821 - `env` is injected by `odoo shell`
