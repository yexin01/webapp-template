"""Wiring for the LLM module."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.kernel.diagnostics import IntegrationInfo, IntegrationKey, IntegrationMode
from app.kernel.http.auth import AuthDeps
from app.kernel.infrastructure.config import Mode
from app.kernel.wiring import Contribution, Kernel, pick
from app.modules.llm.adapters import AnthropicProvider, MockLLMProvider, OpenAIProvider
from app.modules.llm.ports import LLMProvider
from app.modules.llm.probe import LLMProbe
from app.modules.llm.routes import build_router
from app.modules.llm.service import LLMService
from app.modules.llm.settings import LLMProviderName, LLMSettings


@dataclass
class LLMModule:
    service: LLMService
    contribution: Contribution


def build_llm(settings: LLMSettings, kernel: Kernel, auth: AuthDeps, overrides: Mapping[str, Any]) -> LLMModule:
    provider: LLMProvider | None = pick(overrides, "llm_provider", lambda: _provider(settings))
    service = LLMService(
        provider,
        kernel.rate_limiter,
        kernel.analytics,
        checks_per_user_per_minute=settings.llm_checks_per_user_per_minute,
        checks_global_per_day=settings.llm_checks_global_per_day,
        max_concurrent_checks=settings.llm_max_concurrent_checks,
    )
    contribution = Contribution(
        routers=[build_router(service, auth)],
        probes=[LLMProbe(describe(settings), service)],
    )
    return LLMModule(service, contribution)


def _provider(settings: LLMSettings) -> LLMProvider | None:
    if settings.llm_mode is Mode.DISABLED:
        return None
    if settings.llm_mode is Mode.MOCK or settings.llm_api_key is None or not settings.llm_model:
        return MockLLMProvider()
    cls = AnthropicProvider if settings.llm_provider is LLMProviderName.ANTHROPIC else OpenAIProvider
    return cls(
        api_key=settings.llm_api_key.get_secret_value(),
        model=settings.llm_model,
        timeout_seconds=settings.llm_timeout_seconds,
    )


def describe(settings: LLMSettings) -> IntegrationInfo:
    mode = IntegrationMode(settings.llm_mode.value)
    if mode is IntegrationMode.DISABLED:
        return IntegrationInfo(IntegrationKey.LLM, "LLM provider", mode, ("Disabled: no provider is called",))
    target = (
        ("Mock provider",)
        if mode is IntegrationMode.MOCK
        else (f"Provider: {settings.llm_provider.value}", f"Model: {settings.llm_model}")
    )
    limits = (
        f"Limits: {settings.llm_checks_per_user_per_minute}/min per user, "
        f"{settings.llm_checks_global_per_day}/day overall"
    )
    return IntegrationInfo(IntegrationKey.LLM, "LLM provider", mode, (*target, limits))
