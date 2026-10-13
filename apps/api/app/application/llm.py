"""LLM connectivity check with per-user and global cost bounds (spec §10)."""

from __future__ import annotations

from app.application.rate_limit import RateLimiter
from app.domain.authorization import Action, authorize
from app.domain.tenancy import TenantContext
from app.ports import Analytics, LLMCheckResult, LLMProvider

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
    ) -> None:
        self._provider = provider
        self._rate_limiter = rate_limiter
        self._analytics = analytics
        self._per_user = checks_per_user_per_minute
        self._global = checks_global_per_day

    def check(self, ctx: TenantContext) -> LLMCheckResult:
        authorize(ctx, Action.LLM_CHECK)
        self._rate_limiter.hit(f"llm:user:{ctx.user_id}", self._per_user, 60)
        self._rate_limiter.hit("llm:global", self._global, _DAY)
        result = self._provider.check()
        self._analytics.capture(ctx.user_id, "llm_check_run", {"ok": result.ok, "provider": result.provider})
        return result
