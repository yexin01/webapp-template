"""Platform routes: liveness, readiness (API-06), and scheduled jobs."""

from __future__ import annotations

import hmac
import logging
import time
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.kernel.errors import AuthenticationError, DependencyUnavailableError, NotFoundError
from app.kernel.http.schemas import Health, Readiness
from app.kernel.jobs import JobRegistry
from app.kernel.ports import KernelStore, Transactions

logger = logging.getLogger(__name__)


def build_health_router(tx: Transactions[KernelStore]) -> APIRouter:
    """Public; reveals no configuration."""
    router = APIRouter(tags=["health"])

    @router.get("/health", response_model=Health)
    def health() -> Health:
        return Health(status="ok")

    @router.get("/ready", response_model=Readiness, responses={503: {"model": Readiness}})
    def ready() -> JSONResponse:
        try:
            with tx.as_system() as t:
                t.repo.ping()
            body = Readiness(status="ready", checks={"database": "ok"})
            return JSONResponse(body.model_dump(), status_code=200)
        except DependencyUnavailableError:
            body = Readiness(status="not_ready", checks={"database": "unavailable"})
            return JSONResponse(body.model_dump(), status_code=503)

    return router


def build_jobs_router(registry: JobRegistry, cron_secret: str | None) -> APIRouter:
    """``GET /jobs/<name>``, called by Vercel Cron with ``Authorization: Bearer <CRON_SECRET>``.

    Off (404) unless ``CRON_SECRET`` is set. Not part of the public API
    contract, so it is excluded from the OpenAPI document.
    """
    router = APIRouter(tags=["jobs"], include_in_schema=False)

    @router.get("/jobs/{name}")
    def run_job(name: str, request: Request) -> dict[str, Any]:
        if not cron_secret:
            raise NotFoundError()
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(token.strip().encode(), cron_secret.encode()):
            raise AuthenticationError()
        job = registry.get(name)
        if job is None:
            raise NotFoundError()
        started = time.monotonic()
        summary = dict(job.run())
        duration_ms = int((time.monotonic() - started) * 1000)
        logger.info("job_completed", extra={"job": name, "duration_ms": duration_ms, "job_summary": summary})
        return {"job": name, "ok": True, "duration_ms": duration_ms, "summary": summary}

    return router
