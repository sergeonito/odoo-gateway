# Every target has a `## description`; `make` or `make help` lists them.
.DEFAULT_GOAL := help
.PHONY: help install check lint fmt type test test-live test-pg env up down logs token run odoo-setup odoo-apikey migration migrate

help: ## Show this help
	@awk 'BEGIN {FS = ":.*## "} \
	  /^##@/ {printf "\n%s\n", substr($$0, 5)} \
	  /^[a-zA-Z_-]+:.*## / {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@echo

##@ Develop
install: ## Install dependencies into .venv (dev group included)
	uv sync

check: lint type test ## Everything CI runs: ruff, mypy --strict, pytest. Must pass before a commit

lint: ## ruff check + ruff format --check (no changes)
	uv run ruff check .
	uv run ruff format --check .

fmt: ## ruff format + ruff check --fix (changes files)
	uv run ruff format .
	uv run ruff check --fix .

type: ## mypy --strict on src, tests and migrations
	uv run mypy

test: ## Unit and API tests (fake Odoo, SQLite); live tests skip
	uv run pytest

run: ## Gateway on :8000 with reload, against whatever .env points at
	uv run uvicorn odoo_gateway.main:create_app --factory --reload

migration: ## Generate a migration from the models: MSG="what changed" REV=0002; review it before committing
	uv run alembic revision --autogenerate -m "$(MSG)" $(if $(REV),--rev-id $(REV))

migrate: ## Apply migrations to the database alembic.ini / GATEWAY_DATABASE_URL points at
	uv run alembic upgrade head

##@ Local stack (Docker Compose: Odoo 18 + 2x PostgreSQL + gateway)
env: ## Create .env from .env.example with a random dev token secret (no-op if it exists)
	@test -f .env && echo ".env already exists" || \
	  sed "s|^GATEWAY_DEV_JWT_SECRET=.*|GATEWAY_DEV_JWT_SECRET=$$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')|" \
	  .env.example > .env && echo "wrote .env"

up: ## Build and start the stack; the first start installs Odoo modules (~2 min)
	docker compose up -d --build

down: ## Stop the stack (volumes with Odoo and audit data are kept)
	docker compose down

logs: ## Follow gateway and Odoo logs
	docker compose logs -f gateway odoo

odoo-setup: ## Create the integration user and seed products in Odoo (as admin, once per stack)
	uv run python scripts/setup_odoo.py

odoo-apikey: ## Print a new API key for the integration user; put it into .env
	@docker compose exec -T odoo odoo shell -d odoo --db_host=odoo-db --db_user=odoo \
	  --db_password=odoo --no-http --log-level=error < scripts/odoo_apikey.py

##@ Try it
ROLE ?= viewer
token: ## Print a dev bearer token; ROLE=viewer|operator|admin (default viewer)
	@uv run python -m odoo_gateway.dev_token $(ROLE)

test-live: ## Smoke tests through the real app against the running stack (uses .env)
	GATEWAY_ODOO_LIVE=1 uv run pytest -m odoo_live -v

test-pg: ## Migrate and run the append-only trigger test; needs GATEWAY_TEST_PG_URL
	uv run alembic upgrade head
	uv run pytest -m postgres -v
