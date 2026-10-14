"""Integration diagnostics: how each external service is configured, plus a
minimal check against it (spec §5.2, DEC-006).

A check describes one run in one environment. A pass in ``sandbox`` mode is
evidence of connectivity with test credentials only; ``mock`` and
``disabled`` integrations are reported as skipped, never as passed
(TEST-04). Nothing returned here contains secrets.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from app.application.llm import LLMService
from app.application.rate_limit import RateLimiter
from app.domain.catalog import PriceCatalog
from app.domain.entitlements import PlanKey
from app.domain.errors import DomainError, NotFoundError
from app.domain.tenancy import ContextKind, TenantContext
from app.ports import Analytics, BillingProvider, ErrorReporter, MembershipDirectory, UnitOfWork

_DAY = 86_400
_DATABASE_ROLE = "app_api"
_EXPECTED_INTERVALS = {PlanKey.PRO_MONTHLY: "month", PlanKey.PRO_YEARLY: "year"}
_PLAN_LABELS = {PlanKey.PRO_MONTHLY: "Monthly", PlanKey.PRO_YEARLY: "Yearly"}


class IntegrationKey(StrEnum):
    DATABASE = "database"
    IDENTITY = "identity"
    BILLING = "billing"
    LLM = "llm"
    ANALYTICS = "analytics"
    ERROR_REPORTING = "error_reporting"


class IntegrationMode(StrEnum):
    LOCAL = "local"
    MOCK = "mock"
    SANDBOX = "sandbox"
    LIVE = "live"
    DISABLED = "disabled"


class Outcome(StrEnum):
    PASSED = "passed"  # the provider confirmed the request
    SENT = "sent"  # handed to the provider's SDK, but delivery is not confirmed
    FAILED = "failed"
    SKIPPED = "skipped"  # mock or disabled: nothing was contacted


@dataclass(frozen=True, slots=True)
class IntegrationInfo:
    """Static, non-secret description of one integration's configuration."""

    key: IntegrationKey
    name: str
    mode: IntegrationMode
    details: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CheckResult:
    key: IntegrationKey
    mode: IntegrationMode
    outcome: Outcome
    summary: str
    latency_ms: int = 0
    error_code: str | None = None
    reference: str | None = None


@dataclass(frozen=True, slots=True)
class OperatorPolicy:
    """Who may use diagnostics. They expose configuration and call external
    services, so they are an operator tool, not a user feature."""

    operator_ids: frozenset[str]
    allow_any_signed_in_user: bool = False

    def permits(self, external_user_id: str) -> bool:
        return self.allow_any_signed_in_user or external_user_id in self.operator_ids


@dataclass(frozen=True, slots=True)
class _Verdict:
    outcome: Outcome
    summary: str
    error_code: str | None = None
    reference: str | None = None


class DiagnosticsService:
    def __init__(
        self,
        *,
        enabled: bool,
        operators: OperatorPolicy,
        integrations: Sequence[IntegrationInfo],
        uow: UnitOfWork,
        directory: MembershipDirectory,
        billing_provider: BillingProvider | None,
        catalog: PriceCatalog,
        analytics: Analytics,
        error_reporter: ErrorReporter,
        llm: LLMService,
        rate_limiter: RateLimiter,
        runs_per_user_per_minute: int,
        runs_global_per_day: int,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._enabled = enabled
        self._operators = operators
        self._integrations = {info.key: info for info in integrations}
        self._uow = uow
        self._directory = directory
        self._billing = billing_provider
        self._catalog = catalog
        self._analytics = analytics
        self._errors = error_reporter
        self._llm = llm
        self._rate_limiter = rate_limiter
        self._per_user = runs_per_user_per_minute
        self._global = runs_global_per_day
        self._monotonic = monotonic

    def integrations(self, ctx: TenantContext) -> list[IntegrationInfo]:
        self._guard(ctx)
        return [self._integrations[key] for key in IntegrationKey if key in self._integrations]

    def run(self, ctx: TenantContext, key: IntegrationKey) -> CheckResult:
        self._guard(ctx)
        info = self._integrations.get(key)
        if info is None:
            raise NotFoundError()
        self._rate_limiter.hit(f"diagnostics:user:{ctx.user_id}", self._per_user, 60)
        self._rate_limiter.hit("diagnostics:global", self._global, _DAY)

        if info.mode in (IntegrationMode.MOCK, IntegrationMode.DISABLED):
            return CheckResult(key, info.mode, Outcome.SKIPPED, _skip_reason(info.mode))

        started = self._monotonic()
        try:
            verdict = self._check(ctx, key)
        except DomainError as exc:
            verdict = _Verdict(Outcome.FAILED, exc.public_message, error_code=exc.code.lower())
        latency = int((self._monotonic() - started) * 1000)
        return CheckResult(
            key=key,
            mode=info.mode,
            outcome=verdict.outcome,
            summary=verdict.summary,
            latency_ms=latency,
            error_code=verdict.error_code,
            reference=verdict.reference,
        )

    def _guard(self, ctx: TenantContext) -> None:
        # Disabled diagnostics, other contexts, and non-operators are all
        # indistinguishable from a missing route (deny by default).
        if not self._enabled or ctx.kind is not ContextKind.PERSONAL:
            raise NotFoundError()
        if not self._operators.permits(ctx.external_user_id):
            raise NotFoundError()

    def _check(self, ctx: TenantContext, key: IntegrationKey) -> _Verdict:
        checks: dict[IntegrationKey, Callable[[TenantContext], _Verdict]] = {
            IntegrationKey.DATABASE: self._check_database,
            IntegrationKey.IDENTITY: self._check_identity,
            IntegrationKey.BILLING: self._check_billing,
            IntegrationKey.LLM: self._check_llm,
            IntegrationKey.ANALYTICS: self._check_analytics,
            IntegrationKey.ERROR_REPORTING: self._check_error_reporting,
        }
        return checks[key](ctx)

    def _check_database(self, ctx: TenantContext) -> _Verdict:
        with self._uow.as_tenant(ctx) as repo:
            conn = repo.describe_connection()
        problems = []
        if conn.role != _DATABASE_ROLE:
            problems.append(f"queries run as {conn.role}, expected {_DATABASE_ROLE}")
        if not conn.row_security:
            problems.append("row-level security is switched off for this session")
        if not conn.tenant_context_set:
            problems.append("the tenant context was not applied to the transaction")
        if problems:
            return _Verdict(Outcome.FAILED, "Connected, but " + "; ".join(problems) + ".", "unsafe_session")
        return _Verdict(
            Outcome.PASSED,
            f"Connected to PostgreSQL {conn.server_version} as {conn.role}, "
            "with row-level security and the tenant context applied.",
        )

    def _check_identity(self, ctx: TenantContext) -> _Verdict:
        memberships = self._directory.list_user_memberships(ctx.external_user_id)
        count = len(memberships)
        noun = "membership" if count == 1 else "memberships"
        return _Verdict(
            Outcome.PASSED,
            f"Session token verified, and the Clerk Backend API returned {count} organization {noun}.",
        )

    def _check_billing(self, _ctx: TenantContext) -> _Verdict:
        if self._billing is None:
            return _Verdict(Outcome.FAILED, "Billing is not configured.", "not_configured")
        info = self._integrations[IntegrationKey.BILLING]
        expect_live = info.mode is IntegrationMode.LIVE
        problems: list[str] = []
        found: list[str] = []
        currencies: set[str] = set()
        for plan, interval in _EXPECTED_INTERVALS.items():
            label = _PLAN_LABELS[plan]
            price_id = self._catalog.price_for(plan)
            if price_id is None:
                problems.append(f"{label} price is not configured")
                continue
            price = self._billing.describe_price(price_id)
            if not price.active:
                problems.append(f"{label} price is archived")
            if price.recurring_interval != interval:
                problems.append(
                    f"{label} price bills every {price.recurring_interval or 'one-time'}, expected every {interval}"
                )
            if price.livemode != expect_live:
                problems.append(f"{label} price belongs to {'live' if price.livemode else 'test'} mode")
            if price.currency:
                currencies.add(price.currency.upper())
            found.append(f"{label.lower()} ({price.recurring_interval or 'one-time'}, {price.currency or '?'})")
        if len(currencies) > 1:
            problems.append("the monthly and yearly prices use different currencies")
        if problems:
            return _Verdict(Outcome.FAILED, "Stripe is reachable, but " + "; ".join(problems) + ".", "price_mismatch")
        return _Verdict(Outcome.PASSED, "API key accepted; Pro prices verified: " + ", ".join(found) + ".")

    def _check_llm(self, ctx: TenantContext) -> _Verdict:
        result = self._llm.check(ctx)
        if result.ok:
            return _Verdict(Outcome.PASSED, f"{result.provider} accepted the key, and model {result.model} exists.")
        return _Verdict(
            Outcome.FAILED,
            f"{result.provider} check failed for model {result.model}.",
            result.error_code or "unavailable",
        )

    def _check_analytics(self, ctx: TenantContext) -> _Verdict:
        self._analytics.capture(ctx.user_id, "integration_test_sent", {"source": "api"})
        if not self._analytics.flush():
            return _Verdict(Outcome.FAILED, "PostHog did not accept the test event.", "export_failed")
        return _Verdict(
            Outcome.PASSED,
            "PostHog accepted the event integration_test_sent. Confirm it under Activity in PostHog.",
        )

    def _check_error_reporting(self, _ctx: TenantContext) -> _Verdict:
        event_id = self._errors.send_test_event()
        if event_id is None:
            return _Verdict(Outcome.FAILED, "Sentry did not queue the test error.", "not_sent")
        return _Verdict(
            Outcome.SENT,
            "Queued and flushed a synthetic IntegrationTestError. The SDK does not confirm delivery: "
            "find the event ID in Sentry and confirm that no personal data was captured.",
            reference=event_id,
        )


def _skip_reason(mode: IntegrationMode) -> str:
    if mode is IntegrationMode.MOCK:
        return "Mock mode: no network call was made. Switch this integration to sandbox to test it."
    return "Disabled: set its mode to sandbox in .env to test it."
