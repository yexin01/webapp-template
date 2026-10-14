"""LLM configuration (DEC-006: the provider is chosen by the adopter)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, SecretStr

from app.kernel.infrastructure.config import CoreSettings, Mode, mode_problems


class LLMProviderName(StrEnum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"


class LLMSettings(BaseModel):
    llm_mode: Mode = Mode.MOCK
    llm_provider: LLMProviderName = LLMProviderName.ANTHROPIC
    llm_api_key: SecretStr | None = None
    llm_model: str | None = None
    llm_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    llm_checks_per_user_per_minute: int = Field(default=5, ge=1, le=600)
    llm_checks_global_per_day: int = Field(default=200, ge=1, le=100_000)
    llm_max_concurrent_checks: int = Field(default=4, ge=1, le=100)


def llm_problems(s: LLMSettings, core: CoreSettings) -> list[str]:
    problems = mode_problems("LLM_MODE", s.llm_mode, core.app_env)
    if s.llm_mode is Mode.DISABLED:
        problems.append("LLM_MODE must be mock, sandbox, or live")
    if s.llm_mode in (Mode.SANDBOX, Mode.LIVE):
        if s.llm_api_key is None:
            problems.append("LLM_API_KEY is required when LLM_MODE is sandbox or live")
        if not s.llm_model:
            problems.append("LLM_MODEL is required when LLM_MODE is sandbox or live")
    return problems
