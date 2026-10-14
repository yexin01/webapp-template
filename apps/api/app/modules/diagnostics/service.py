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
from app.kernel.errors import DomainError, NotFoundError
from app.kernel.tenancy import ContextKind, TenantContext
from app.modules.diagnostics.domain import OperatorPolicy

_DAY = 86_400


class DiagnosticsService:
    def __init__(
        self,
        *,
        enabled: bool,
        operators: OperatorPolicy,
        probes: Sequence[IntegrationProbe],
        rate_limiter: RateLimiter,
        runs_per_user_per_minute: int,
        runs_global_per_day: int,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._enabled = enabled
        self._operators = operators
        self._probes = {probe.info.key: probe for probe in probes}
        self._rate_limiter = rate_limiter
        self._per_user = runs_per_user_per_minute
        self._global = runs_global_per_day
        self._monotonic = monotonic

    def integrations(self, ctx: TenantContext) -> list[IntegrationInfo]:
        self._guard(ctx)
        return [self._probes[key].info for key in IntegrationKey if key in self._probes]

    def run(self, ctx: TenantContext, key: IntegrationKey) -> CheckResult:
        self._guard(ctx)
        probe = self._probes.get(key)
        if probe is None:
            raise NotFoundError()
        self._rate_limiter.hit(f"diagnostics:user:{ctx.user_id}", self._per_user, 60)
        self._rate_limiter.hit("diagnostics:global", self._global, _DAY)

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

    def _guard(self, ctx: TenantContext) -> None:
        # Disabled diagnostics, other contexts, and non-operators are all
        # indistinguishable from a missing route (deny by default).
        if not self._enabled or ctx.kind is not ContextKind.PERSONAL:
            raise NotFoundError()
        if not self._operators.permits(ctx.external_user_id):
            raise NotFoundError()


def _skip_reason(mode: IntegrationMode) -> str:
    if mode is IntegrationMode.MOCK:
        return "Mock mode: no network call was made. Switch this integration to sandbox to test it."
    return "Disabled: set its mode to sandbox in .env to test it."
