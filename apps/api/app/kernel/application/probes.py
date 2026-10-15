"""Diagnostics probes for the kernel's own integrations: PostgreSQL, PostHog, and Sentry."""

from __future__ import annotations

from app.kernel.diagnostics import IntegrationInfo, Outcome, Verdict
from app.kernel.ports import Analytics, ErrorReporter, KernelStore, Transactions
from app.kernel.tenancy import TenantContext

_DATABASE_ROLE = "app_api"


class DatabaseProbe:
    """Confirms requests run as the non-privileged role, bound by row-level security on every
    app table, with the tenant context applied (DB-06)."""

    def __init__(self, info: IntegrationInfo, tx: Transactions[KernelStore]) -> None:
        self._info = info
        self._tx = tx

    @property
    def info(self) -> IntegrationInfo:
        return self._info

    def check(self, ctx: TenantContext) -> Verdict:
        with self._tx.as_tenant(ctx) as t:
            conn = t.repo.describe_connection()
        problems = []
        if conn.role != _DATABASE_ROLE:
            problems.append(f"queries run as {conn.role}, expected {_DATABASE_ROLE}")
        if conn.superuser:
            problems.append(f"{conn.role} is a superuser, which bypasses row-level security")
        if conn.bypass_rls:
            problems.append(f"{conn.role} has BYPASSRLS, which bypasses row-level security")
        if not conn.row_security:
            problems.append("row-level security is switched off for this session")
        if conn.unprotected_tables:
            problems.append("row-level security is not enabled and forced on " + ", ".join(conn.unprotected_tables))
        if conn.owned_tables:
            problems.append(
                f"{conn.role} owns {', '.join(conn.owned_tables)}, so it could switch their row-level security off"
            )
        if not conn.tenant_context_set:
            problems.append("the tenant context was not applied to the transaction")
        if problems:
            return Verdict(Outcome.FAILED, "Connected, but " + "; ".join(problems) + ".", "unsafe_session")
        return Verdict(
            Outcome.PASSED,
            f"Connected to PostgreSQL {conn.server_version} as {conn.role}, with row-level security "
            "enforced on every app table and the tenant context applied.",
        )


class AnalyticsProbe:
    def __init__(self, info: IntegrationInfo, analytics: Analytics) -> None:
        self._info = info
        self._analytics = analytics

    @property
    def info(self) -> IntegrationInfo:
        return self._info

    def check(self, ctx: TenantContext) -> Verdict:
        self._analytics.capture(ctx.user_id, "integration_test_sent", {"source": "api"})
        if not self._analytics.flush():
            # flush() returns False for either case; only the API logs tell them apart.
            return Verdict(
                Outcome.FAILED,
                "Could not confirm that PostHog accepted the test event: an export failed, "
                "or this API instance's analytics queue did not drain within the flush timeout.",
                "export_failed",
            )
        return Verdict(
            Outcome.PASSED,
            "PostHog accepted the event integration_test_sent. Confirm it under Activity in PostHog.",
        )


class ErrorReportingProbe:
    def __init__(self, info: IntegrationInfo, reporter: ErrorReporter) -> None:
        self._info = info
        self._reporter = reporter

    @property
    def info(self) -> IntegrationInfo:
        return self._info

    def check(self, ctx: TenantContext) -> Verdict:
        event_id = self._reporter.send_test_event()
        if event_id is None:
            return Verdict(Outcome.FAILED, "Sentry did not queue the test error.", "not_sent")
        return Verdict(
            Outcome.SENT,
            "Queued and flushed a synthetic IntegrationTestError. The SDK does not confirm delivery: "
            "find the event ID in Sentry and confirm that no personal data was captured.",
            reference=event_id,
        )
