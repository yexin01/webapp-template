"""Diagnostics probe for the LLM provider. Uses the same rate-limited check as users."""

from __future__ import annotations

from app.kernel.diagnostics import IntegrationInfo, Outcome, Verdict
from app.kernel.tenancy import TenantContext
from app.modules.llm.service import LLMService


class LLMProbe:
    def __init__(self, info: IntegrationInfo, llm: LLMService) -> None:
        self._info = info
        self._llm = llm

    @property
    def info(self) -> IntegrationInfo:
        return self._info

    def check(self, ctx: TenantContext) -> Verdict:
        result = self._llm.check(ctx)
        if result.ok:
            return Verdict(Outcome.PASSED, f"{result.provider} accepted the key, and model {result.model} exists.")
        return Verdict(
            Outcome.FAILED,
            f"{result.provider} check failed for model {result.model}.",
            result.error_code or "unavailable",
        )
