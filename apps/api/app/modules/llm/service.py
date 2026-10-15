"""LLM connectivity check with per-user and global cost bounds (spec §10)."""

from __future__ import annotations

import threading

from app.kernel.application.rate_limit import RateLimiter
from app.kernel.authorization import authorize
from app.kernel.errors import BudgetExceededError, RateLimitedError
from app.kernel.ports import Analytics
from app.kernel.tenancy import TenantContext
from app.modules.llm.domain import LLM_CHECK, LLMCheckResult
from app.modules.llm.ports import LLMProvider

_DAY = 86_400


class LLMService:
    def __init__(
        self,
        provider: LLMProvider,
        rate_limiter: RateLimiter,
        analytics: Analytics,
        *,
        checks_per_user_per_minute: int,
        checks_global_per_day: int,
        max_concurrent_checks: int = 4,
    ) -> None:
        self._provider = provider
        self._rate_limiter = rate_limiter
        self._analytics = analytics
        self._per_user = checks_per_user_per_minute
        self._global = checks_global_per_day
        # LLM-05: a per-instance bound on provider calls in flight.
        self._in_flight = threading.BoundedSemaphore(max_concurrent_checks)

    def check(self, ctx: TenantContext) -> LLMCheckResult:
        authorize(ctx, LLM_CHECK)
        # The concurrency bound comes first, so a request it refuses uses none of the caller's or the global quota.
        if not self._in_flight.acquire(blocking=False):
            raise RateLimitedError(retry_after_seconds=1)
        try:
            self._rate_limiter.hit(f"llm:user:{ctx.user_id}", self._per_user, 60)
            try:
                self._rate_limiter.hit("llm:global", self._global, _DAY)
            except RateLimitedError as exc:
                raise BudgetExceededError(exc.retry_after_seconds) from exc
            result = self._provider.check()
        finally:
            self._in_flight.release()
        self._analytics.capture(ctx.user_id, "llm_check_run", {"ok": result.ok, "provider": result.provider})
        return result
