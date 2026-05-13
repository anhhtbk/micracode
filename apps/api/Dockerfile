# syntax=docker/dockerfile:1.7
# FastAPI backend image using uv for fast, reproducible installs.
# Build context = repo root.

ARG PYTHON_VERSION=3.12-slim

############################
# 1) builder: resolve deps with uv into /app/.venv
############################
FROM python:${PYTHON_VERSION} AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv

# Install uv (single static binary).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Install deps first (cacheable) using lockfile only.
COPY apps/api/pyproject.toml apps/api/uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# Now copy source + install the project itself.
COPY apps/api/src ./src
COPY apps/api/README.md ./README.md
RUN uv sync --frozen --no-dev

############################
# 2) runtime: slim image with the prebuilt venv
############################
FROM python:${PYTHON_VERSION} AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    OPENER_APPS_DIR=/app/data

RUN groupadd --system --gid 1001 app \
 && useradd  --system --uid 1001 --gid app app

WORKDIR /app

COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --from=builder --chown=app:app /app/src   /app/src
COPY --chown=app:app apps/api/pyproject.toml /app/pyproject.toml

RUN mkdir -p /app/data && chown -R app:app /app/data
VOLUME ["/app/data"]

USER app
EXPOSE 8000

CMD ["uvicorn", "micracode_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
