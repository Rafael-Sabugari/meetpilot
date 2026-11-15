# syntax=docker/dockerfile:1
FROM python:3.13-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
WORKDIR /srv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./
RUN uv sync --frozen --no-dev && useradd --system --uid 10001 meetpilot
USER meetpilot
EXPOSE 8000
# Apply database migrations, then serve. (Run migrations as a separate release step when
# scaling to multiple replicas.)
CMD ["sh", "-c", "/srv/.venv/bin/alembic upgrade head && exec /srv/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers"]
