FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
COPY alembic.ini ./
COPY migrations ./migrations
RUN uv sync --frozen --no-dev

RUN useradd --system --uid 10001 gateway
USER gateway
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000
# Migrations on start keep the demo one command; a real deployment runs them as a separate job.
CMD ["sh", "-c", "alembic upgrade head && uvicorn odoo_gateway.main:create_app --factory --host 0.0.0.0 --port 8000"]
