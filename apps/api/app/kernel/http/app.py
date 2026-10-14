"""FastAPI application factory. Modules contribute routers; the factory adds the
platform: error contract, middleware, and CORS."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.kernel.http.middleware import BodySizeLimitMiddleware, RequestContextMiddleware
from app.kernel.http.problem import install_error_handlers
from app.kernel.infrastructure.config import AppEnv, CoreSettings

API_PREFIX = "/api/v1"


def build_fastapi(
    *,
    settings: CoreSettings,
    title: str,
    routers: Sequence[APIRouter],
    dependencies: Sequence[Any] = (),
    on_shutdown: Callable[[], None] = lambda: None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        on_shutdown()

    app = FastAPI(
        title=title,
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None if settings.app_env is AppEnv.PRODUCTION else f"{API_PREFIX}/openapi.json",
        lifespan=lifespan,
        dependencies=list(dependencies),
    )

    install_error_handlers(app)
    for router in routers:
        app.include_router(router, prefix=API_PREFIX)

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


def bearer_token(authorization: str) -> str | None:
    """The token from an ``Authorization: Bearer`` header. Cookies are never accepted (API-04)."""
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()
