"""Composition root: builds the kernel, then each module, then the HTTP app.

This is the only place that knows every module and which concrete adapter
backs each port. Tests build a ``Container`` with
``build_container(settings, **overrides)`` instead of activating any test-only
path in application code (ENV-08).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from functools import cached_property
from typing import Any

from fastapi import FastAPI
from starlette.middleware import Middleware

from app.kernel.application.outbox import OutboxDispatcher
from app.kernel.application.probes import AnalyticsProbe, DatabaseProbe, ErrorReportingProbe
from app.kernel.application.rate_limit import RateLimiter
from app.kernel.diagnostics import IntegrationProbe
from app.kernel.events import EventHandlers
from app.kernel.http.app import build_fastapi
from app.kernel.http.request_scope import AfterResponseDispatch, note_recorded_event, request_transaction
from app.kernel.http.routes import build_health_router, build_jobs_router
from app.kernel.infrastructure.clock import SystemClock
from app.kernel.infrastructure.config import AppEnv, Mode, requires_restricted_login_role
from app.kernel.infrastructure.db import REQUEST_THREADS, Database, PgTransactions
from app.kernel.infrastructure.integrations import describe_analytics, describe_database, describe_error_reporting
from app.kernel.infrastructure.migrations import with_tls_default
from app.kernel.infrastructure.observability import (
    AnalyticsEvents,
    NoopAnalytics,
    NoopErrorReporter,
    PostHogAnalytics,
    SentryErrorReporter,
    configure_logging,
    flush_telemetry,
    init_error_reporting,
)
from app.kernel.infrastructure.store import PgKernelStore
from app.kernel.jobs import TIME_BUDGET_SECONDS, Job, JobRegistry
from app.kernel.ports import Analytics, Clock, ErrorReporter, KernelStore
from app.kernel.wiring import Contribution, Kernel, Overrides, pick
from app.modules.billing.module import BillingModule, build_billing
from app.modules.diagnostics.module import DiagnosticsModule, build_diagnostics
from app.modules.identity.module import IdentityModule, build_identity
from app.modules.llm.module import LLMModule, build_llm
from app.modules.projects.module import ProjectsModule, build_projects

# new:module:imports (pnpm new:module adds imports above this line)
from app.settings import Settings, load_settings, reserved_request_threads

API_TITLE = "SaaS Template API"

logger = logging.getLogger(__name__)


@dataclass
class Container:
    settings: Settings
    kernel: Kernel
    identity: IdentityModule
    billing: BillingModule
    projects: ProjectsModule
    llm: LLMModule
    # new:module:fields
    closers: list[Callable[[], None]] = field(default_factory=list)

    def module_contributions(self) -> list[Contribution]:
        """The feature modules' contributions; the integration probes of diagnostics come from these."""
        return [
            self.identity.contribution,
            self.billing.contribution,
            self.projects.contribution,
            self.llm.contribution,
            # new:module:contributions
        ]

    @cached_property
    def probes(self) -> list[IntegrationProbe]:
        """The kernel's own integration probes and those of every module's contribution, so none is left out."""
        return [
            DatabaseProbe(
                describe_database(self.settings),
                self.kernel.store,
                restricted_login=requires_restricted_login_role(self.settings.app_env),
            ),
            *(probe for contribution in self.module_contributions() for probe in contribution.probes),
            AnalyticsProbe(describe_analytics(self.settings), self.kernel.analytics),
            ErrorReportingProbe(describe_error_reporting(self.settings), self.kernel.error_reporter),
        ]

    @cached_property
    def diagnostics(self) -> DiagnosticsModule:
        return build_diagnostics(self.settings, self.settings, self.kernel, self.identity.auth, self.probes)

    def contributions(self) -> list[Contribution]:
        return [*self.module_contributions(), self.diagnostics.contribution]

    def close(self) -> None:
        self.kernel.analytics.shutdown()
        for contribution in self.contributions():
            for closer in contribution.closers:
                closer()
        for closer in self.closers:
            closer()


def build_container(settings: Settings, **adapters: Any) -> Container:
    """Build the kernel and all modules. Keyword overrides replace individual adapters (tests only).

    An override that no builder looks up is a misspelled key: it raises ``TypeError``.
    """
    overrides = Overrides(adapters)
    closers: list[Callable[[], None]] = []
    clock: Clock = pick(overrides, "clock", SystemClock)
    database: Database = pick(overrides, "database", lambda: _database(settings))
    closers.append(database.close)
    store: PgTransactions[KernelStore] = PgTransactions(database, PgKernelStore, note_recorded_event, clock)
    analytics_events = AnalyticsEvents()
    analytics: Analytics = pick(overrides, "analytics", lambda: _analytics(settings, analytics_events))
    error_reporter: ErrorReporter = pick(overrides, "error_reporter", lambda: _error_reporter(settings))
    handlers = EventHandlers()
    jobs = JobRegistry()
    rate_limiter = RateLimiter(store, clock)
    outbox = OutboxDispatcher(
        store,
        handlers,
        clock,
        max_attempts=settings.outbox_max_attempts,
        lease_seconds=settings.outbox_lease_seconds,
    )
    kernel = Kernel(
        database=database,
        clock=clock,
        analytics=analytics,
        analytics_events=analytics_events,
        error_reporter=error_reporter,
        rate_limiter=rate_limiter,
        store=store,
        handlers=handlers,
        jobs=jobs,
        outbox=outbox,
        on_record=note_recorded_event,
        request_transaction=request_transaction(database),
    )
    # Daily schedules work on every Vercel plan; tighten drain-outbox where the plan allows.
    jobs.register(Job("drain-outbox", "Dispatch outbox events left after requests.", "15 2 * * *", _drain(outbox)))
    retention = timedelta(days=settings.outbox_retention_days)
    jobs.register(
        Job(
            "prune-expired",
            "Delete expired rate-limit windows and processed outbox events past retention.",
            "0 5 * * *",
            lambda: {
                "rate_limit_windows": rate_limiter.prune(TIME_BUDGET_SECONDS / 2),
                "outbox_events": outbox.prune(retention, TIME_BUDGET_SECONDS / 2),
            },
        )
    )

    identity = build_identity(settings, settings, kernel, overrides)
    billing = build_billing(settings, settings, kernel, identity.auth, overrides)
    projects = build_projects(kernel, identity.auth)
    # new:module:build
    llm = build_llm(settings, kernel, identity.auth, overrides)
    container = Container(
        settings=settings,
        kernel=kernel,
        identity=identity,
        billing=billing,
        projects=projects,
        llm=llm,
        # new:module:container
        closers=closers,
    )
    container.diagnostics  # noqa: B018 - built now, so a duplicate probe fails at startup
    try:
        for contribution in container.contributions():
            analytics_events.register(contribution.analytics_events)
    except ValueError:
        container.close()
        raise
    if unread := overrides.unread():
        container.close()
        raise TypeError(f"build_container() got unknown overrides: {', '.join(unread)}")
    return container


def create_app(container: Container) -> FastAPI:
    kernel = container.kernel
    cron_secret = container.settings.cron_secret
    routers = [build_health_router(kernel.store)]
    for contribution in container.contributions():
        routers += contribution.routers
    routers.append(build_jobs_router(kernel.jobs, cron_secret.get_secret_value() if cron_secret else None))
    if cron_secret is None and container.settings.app_env is AppEnv.PRODUCTION:
        # Not an error: jobs can also run from the CLI. But nothing schedules them otherwise.
        logger.warning(
            "jobs_endpoint_disabled",
            extra={"reason": "CRON_SECRET is not set", "jobs": ",".join(j.name for j in kernel.jobs.all())},
        )
    app = build_fastapi(
        settings=container.settings,
        title=API_TITLE,
        routers=routers,
        inner_middleware=[Middleware(AfterResponseDispatch, dispatcher=kernel.outbox, database=kernel.database)],
        worker_threads=REQUEST_THREADS,
        on_shutdown=container.close,
        flush_telemetry=lambda seconds: flush_telemetry(kernel.analytics, seconds),
    )
    app.state.container = container
    return app


def bootstrap(settings: Settings | None = None) -> Container:
    """Load configuration (unless given, for the telemetry wire test), configure telemetry, and build the container."""
    settings = settings or load_settings()
    configure_logging(settings.log_level)
    init_error_reporting(settings)
    return build_container(settings)


def _database(settings: Settings) -> Database:
    if settings.database_url is None:  # pragma: no cover - validated in Settings
        raise RuntimeError("DATABASE_URL is required")
    return Database(
        with_tls_default(settings.database_url.get_secret_value()),
        api_pool_size=settings.database_pool_max_size,
        worker_pool_size=settings.database_worker_pool_max_size,
        connect_timeout=settings.database_connect_timeout_seconds,
        statement_timeout_ms=settings.database_statement_timeout_ms,
        request_threads=REQUEST_THREADS,
        reserved_threads=reserved_request_threads(settings),
        enforce_login_role=requires_restricted_login_role(settings.app_env),
    )


def _analytics(settings: Settings, events: AnalyticsEvents) -> Analytics:
    if settings.analytics_mode in (Mode.SANDBOX, Mode.LIVE) and settings.posthog_project_api_key:
        return PostHogAnalytics(api_key=settings.posthog_project_api_key, host=settings.posthog_host, events=events)
    return NoopAnalytics(events)


def _error_reporter(settings: Settings) -> ErrorReporter:
    if settings.error_reporting_mode in (Mode.SANDBOX, Mode.LIVE):
        return SentryErrorReporter()
    return NoopErrorReporter()


def _drain(outbox: OutboxDispatcher) -> Callable[[], dict[str, int]]:
    return lambda: outbox.drain(time_budget_seconds=TIME_BUDGET_SECONDS).summary()
