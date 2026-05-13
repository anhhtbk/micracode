"""FastAPI application entrypoint.

Composition root: wires CORS, routers, and top-level exception handling.
Business logic lives in the ``routers`` and ``agents`` packages.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import storage
from .config import settings
from .routers import deploy, generate, health, models, projects

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(settings.app_name)


@asynccontextmanager
async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
    storage.get_storage().ensure_root()
    yield


def create_app() -> FastAPI:
    """Application factory so tests can build isolated instances."""
    app = FastAPI(
        title="Micracode API",
        version="0.1.0",
        description=(
            "Streaming code-generation backend for Micracode. "
            "Custom codegen orchestrator + Gemini 2.5 Flash behind SSE."
        ),
        lifespan=_lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Accept"],
        expose_headers=["X-Request-ID"],
        max_age=3600,
    )

    app.include_router(health.router, prefix="/v1", tags=["health"])
    app.include_router(models.router, prefix="/v1", tags=["models"])
    app.include_router(projects.router, prefix="/v1", tags=["projects"])
    app.include_router(generate.router, prefix="/v1", tags=["generate"])
    app.include_router(deploy.router, prefix="/v1", tags=["deploy"])

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled exception: %s", exc)
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal Server Error"},
        )

    logger.info(
        "micracode-api ready env=%s provider=%s model=%s origins=%s data_dir=%s",
        settings.environment,
        settings.llm_provider,
        settings.active_model,
        settings.cors_allow_origins,
        settings.opener_apps_dir,
    )
    return app


app = create_app()
