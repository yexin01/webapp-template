"""LLM rules and results."""

from __future__ import annotations

from dataclasses import dataclass

from app.kernel.authorization import ALL_ROLES, Permission

LLM_CHECK = Permission("llm:check", personal=True, org_roles=ALL_ROLES)


@dataclass(frozen=True, slots=True)
class LLMCheckResult:
    ok: bool
    provider: str
    model: str
    latency_ms: int
    error_code: str | None = None
