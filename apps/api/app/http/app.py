"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.composition import Container
from app.http.middleware import BodySizeLimitMiddleware, RequestContextMiddleware
from app.http.problem import install_error_handlers
from app.http.routes import account, billing, diagnostics, health, llm, projects, webhooks

API_PREFIX = "/api/v1"


def create_app(container: Container) -> FastAPI:
    settings = container.settings
    production = settings.app_env.value == "production"

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        container.close()

    app = FastAPI(
        title="SaaS Template API",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None if production else f"{API_PREFIX}/openapi.json",
        lifespan=lifespan,
    )
    app.state.container = container

    install_error_handlers(app)
    for module in (health, account, projects, billing, llm, diagnostics, webhooks):
        app.include_router(module.router, prefix=API_PREFIX)

    # Browsers reach the API same-origin through the web app's rewrites; CORS
    # is limited to the configured app origin for direct calls (API-04).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.app_base_url.rstrip("/")],
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-ID"],
        allow_credentials=False,
        max_age=600,
    )
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_request_body_bytes)
    app.add_middleware(RequestContextMiddleware)  # outermost: wraps every response
    return app
