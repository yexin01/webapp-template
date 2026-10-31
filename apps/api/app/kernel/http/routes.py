"""Platform routes: liveness, readiness (API-06), and scheduled jobs."""

from __future__ import annotations

import hmac
import logging
import time
from typing import Any

import anyio
from anyio.lowlevel import RunVar
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.kernel.application.job_guard import JobGuard
from app.kernel.errors import AuthenticationError, DependencyUnavailableError, NotFoundError
from app.kernel.http.schemas import Health, Readiness, errors
from app.kernel.jobs import JobRegistry
from app.kernel.ports import KernelStore, Transactions

logger = logging.getLogger(__name__)


class _Check:
    """A database check in progress; callers that arrive meanwhile share its outcome."""

    def __init__(self) -> None:
        self.done = anyio.Event()
        self.ready = False


def build_health_router(tx: Transactions[KernelStore]) -> APIRouter:
    """Public; reveals no configuration.

    ``/health`` runs on the event loop: it needs no thread, so a saturated
    threadpool never delays it. ``/ready`` runs one database check at a time
    per event loop, on a thread of its own: concurrent callers wait for the
    check in progress and share its outcome, so a flood of them holds at
    most one thread and one worker connection. Nothing is cached once a
    check ends; the next caller starts a new one.
    """
    router = APIRouter(tags=["health"])
    checks: RunVar[_Check | None] = RunVar(f"readiness_check_{id(tx)}", default=None)
    limiters: RunVar[anyio.CapacityLimiter] = RunVar(f"readiness_threads_{id(tx)}")

    def database_ready() -> bool:
        try:
            with tx.as_system() as t:
                t.repo.ping()
        except DependencyUnavailableError:
            return False
        return True

    def limiter() -> anyio.CapacityLimiter:
        try:
            return limiters.get()
        except LookupError:
            created = anyio.CapacityLimiter(1)
            limiters.set(created)
            return created

    async def check_database() -> bool:
        current = checks.get()
        if current is not None:
            await current.done.wait()
            return current.ready
        current = _Check()
        checks.set(current)
        try:
            current.ready = await anyio.to_thread.run_sync(database_ready, limiter=limiter())
        finally:
            checks.set(None)
            current.done.set()
        return current.ready

    @router.get("/health", response_model=Health)
    async def health() -> Health:
        return Health(status="ok")

    @router.get("/ready", response_model=Readiness, responses={503: {"model": Readiness}})
    async def ready() -> JSONResponse:
        if await check_database():
            body = Readiness(status="ready", checks={"database": "ok"})
            return JSONResponse(body.model_dump(), status_code=200)
        body = Readiness(status="not_ready", checks={"database": "unavailable"})
        return JSONResponse(body.model_dump(), status_code=503)

    return router


def build_jobs_router(registry: JobRegistry, cron_secret: str | None, guard: JobGuard) -> APIRouter:
    """``GET /jobs/<name>``, called by Vercel Cron with ``Authorization: Bearer <CRON_SECRET>``.

    Off (404) unless ``CRON_SECRET`` is set. Not part of the public API
    contract, so it is excluded from the OpenAPI document.

    A job registered here runs once at a time across all instances and no sooner than
    ``JOB_MIN_SPACING_SECONDS`` after the previous start (SEC-09): a call that finds it running is
    refused with 409, one that comes too soon with 429 and ``Retry-After``. Only a known job name
    reaches the guard, after the secret is checked.
    """
    router = APIRouter(tags=["jobs"], include_in_schema=False)

    @router.get("/jobs/{name}", responses=errors(401, 404, 409, 429, 503))
    def run_job(name: str, request: Request) -> dict[str, Any]:
        if not cron_secret:
            raise NotFoundError()
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(token.strip().encode(), cron_secret.encode()):
            raise AuthenticationError()
        job = registry.get(name)
        if job is None:
            raise NotFoundError()
        with guard.exclusive(name):
            started = time.monotonic()
            summary = dict(job.run())
            duration_ms = int((time.monotonic() - started) * 1000)
        logger.info("job_completed", extra={"job": name, "duration_ms": duration_ms, "job_summary": summary})
        return {"job": name, "ok": True, "duration_ms": duration_ms, "summary": summary}

    return router
