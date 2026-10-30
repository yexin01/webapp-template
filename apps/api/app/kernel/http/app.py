"""FastAPI application factory. Modules contribute routers; the factory adds the
platform: error contract, middleware, and CORS."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from typing import Any

import anyio
from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware import Middleware

from app.kernel.http.middleware import BodySizeLimitMiddleware, RequestContextMiddleware
from app.kernel.http.problem import install_error_handlers
from app.kernel.http.schemas import ValidationProblem
from app.kernel.infrastructure.config import AppEnv, CoreSettings

API_PREFIX = "/api/v1"
_SCHEMAS = "#/components/schemas/"


class _Application(FastAPI):
    """Documents the 422 body the API returns (``ValidationProblem``, see ``problem.py``)
    in place of FastAPI's default ``HTTPValidationError``, for every route that can fail validation."""

    def openapi(self) -> dict[str, Any]:
        if not self.openapi_schema:
            self.openapi_schema = _document_validation_problems(super().openapi())
        return self.openapi_schema


def _document_validation_problems(document: dict[str, Any]) -> dict[str, Any]:
    default = {"$ref": f"{_SCHEMAS}HTTPValidationError"}
    responses = [
        operation["responses"]["422"]
        for path_item in document.get("paths", {}).values()
        for operation in path_item.values()
        if operation.get("responses", {}).get("422", {}).get("content", {}).get("application/json", {}).get("schema")
        == default
    ]
    if not responses:
        return document
    for response in responses:
        response["content"] = {"application/json": {"schema": {"$ref": f"{_SCHEMAS}ValidationProblem"}}}
    schemas = document["components"]["schemas"]
    for unused in ("HTTPValidationError", "ValidationError"):  # FastAPI's, referenced only by its default 422
        schemas.pop(unused, None)
    definition = ValidationProblem.model_json_schema(ref_template=f"{_SCHEMAS}{{model}}", mode="serialization")
    schemas.update(definition.pop("$defs", {}), ValidationProblem=definition)
    document["components"]["schemas"] = dict(sorted(schemas.items()))
    return document


def build_fastapi(
    *,
    settings: CoreSettings,
    title: str,
    routers: Sequence[APIRouter],
    dependencies: Sequence[Any] = (),
    inner_middleware: Sequence[Middleware] = (),
    worker_threads: int,
    on_shutdown: Callable[[], None] = lambda: None,
    flush_telemetry: Callable[[float], None] | None = None,
) -> FastAPI:
    """``inner_middleware`` wraps the routes, inside the platform middleware (request
    context, body-size limit, CORS), in Starlette's order: the first entry is the outermost.

    ``flush_telemetry(seconds)`` sends the telemetry queued in the background; the request
    context middleware calls it after the response of a request that queued some."""

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        # Sync dependencies and handlers run on AnyIO's default thread limiter.
        # Set it explicitly: the database bounds its waiting threads against it.
        anyio.to_thread.current_default_thread_limiter().total_tokens = worker_threads
        yield
        on_shutdown()

    app = _Application(
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

    for cls, args, kwargs in reversed(inner_middleware):
        app.add_middleware(cls, *args, **kwargs)
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
    app.add_middleware(RequestContextMiddleware, flush_telemetry=flush_telemetry)  # outermost: wraps every response
    return app


def bearer_token(authorization: str) -> str | None:
    """The token from an ``Authorization: Bearer`` header. Cookies are never accepted (API-04)."""
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()
