"""Diagnostics use cases. Nothing returned here contains secrets."""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence

from app.kernel.application.rate_limit import RateLimiter
from app.kernel.diagnostics import (
    CheckResult,
    IntegrationInfo,
    IntegrationKey,
    IntegrationMode,
    IntegrationProbe,
    Outcome,
    Verdict,
)
from app.kernel.errors import DomainError, NotFoundError, RateLimitedError
from app.kernel.ports import AuditEntry, KernelStore, Transactions
from app.kernel.tenancy import ContextKind, TenantContext
from app.modules.diagnostics.domain import OperatorPolicy

_DAY = 86_400
_AUDIT_ACTION = "diagnostics:run"


class DiagnosticsService:
    def __init__(
        self,
        *,
        enabled: bool,
        operators: OperatorPolicy,
        probes: Sequence[IntegrationProbe],
        rate_limiter: RateLimiter,
        transactions: Transactions[KernelStore],
        runs_per_user_per_minute: int,
        runs_global_per_day: int,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._enabled = enabled
        self._operators = operators
        keys = [probe.info.key for probe in probes]
        if duplicates := sorted({key.value for key in keys if keys.count(key) > 1}):
            raise ValueError(f"Duplicate integration probe keys: {', '.join(duplicates)}")
        self._probes = {probe.info.key: probe for probe in probes}
        self._rate_limiter = rate_limiter
        self._tx = transactions
        self._per_user = runs_per_user_per_minute
        self._global = runs_global_per_day
        self._monotonic = monotonic

    def integrations(self, ctx: TenantContext) -> list[IntegrationInfo]:
        self._require_available(ctx)
        if not self._operators.permits(ctx.external_user_id):
            raise NotFoundError()
        return [self._probes[key].info for key in IntegrationKey if key in self._probes]

    def run(self, ctx: TenantContext, key: IntegrationKey, request_id: str | None) -> CheckResult:
        """Run one check (ADM-01). Each run is audited once it is over, in its own short
        transaction, so none is held open while a provider is called. Each attempt refused
        because the caller is not an operator is audited too, up to the per-user run limit."""
        self._require_available(ctx)
        if not self._operators.permits(ctx.external_user_id):
            self._audit_refusal(ctx, key, request_id)
            raise NotFoundError()
        probe = self._probes.get(key)
        if probe is None:
            raise NotFoundError()
        self._rate_limiter.hit(f"diagnostics:user:{ctx.user_id}", self._per_user, 60)
        self._rate_limiter.hit("diagnostics:global", self._global, _DAY)
        try:
            result = self._check(ctx, key, probe)
        except Exception:
            self._audit(ctx, key, "failed", request_id)
            raise
        self._audit(ctx, key, "failed" if result.outcome is Outcome.FAILED else "succeeded", request_id)
        return result

    def _check(self, ctx: TenantContext, key: IntegrationKey, probe: IntegrationProbe) -> CheckResult:
        info = probe.info
        if info.mode in (IntegrationMode.MOCK, IntegrationMode.DISABLED):
            return CheckResult(key, info.mode, Outcome.SKIPPED, _skip_reason(info.mode))

        started = self._monotonic()
        try:
            verdict = probe.check(ctx)
        except DomainError as exc:
            verdict = Verdict(Outcome.FAILED, exc.public_message, error_code=exc.code.lower())
        return CheckResult(
            key=key,
            mode=info.mode,
            outcome=verdict.outcome,
            summary=verdict.summary,
            latency_ms=int((self._monotonic() - started) * 1000),
            error_code=verdict.error_code,
            reference=verdict.reference,
        )

    def _require_available(self, ctx: TenantContext) -> None:
        # Disabled diagnostics, other contexts, a session someone else started (an impersonator
        # is no operator), and (checked next) non-operators all get the answer a missing route
        # gets (deny by default).
        if not self._enabled or ctx.kind is not ContextKind.PERSONAL or ctx.actor_external_id is not None:
            raise NotFoundError()

    def _audit_refusal(self, ctx: TenantContext, key: IntegrationKey, request_id: str | None) -> None:
        # Anyone signed in can be refused, and the audit log is kept indefinitely.
        try:
            self._rate_limiter.hit(f"diagnostics:denied:{ctx.user_id}", self._per_user, 60)
        except RateLimitedError:
            return  # still a 404: a 429 would reveal the route to a non-operator
        self._audit(ctx, key, "denied", request_id)

    def _audit(self, ctx: TenantContext, key: IntegrationKey, outcome: str, request_id: str | None) -> None:
        with self._tx.as_tenant(ctx) as t:
            t.audit(AuditEntry(ctx.user_id, None, _AUDIT_ACTION, "integration", key.value, outcome, request_id))


def _skip_reason(mode: IntegrationMode) -> str:
    if mode is IntegrationMode.MOCK:
        return "Mock mode: no network call was made. Switch this integration to sandbox to test it."
    return "Disabled: set its mode to sandbox in .env to test it."
