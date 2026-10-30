"""LLM_MODE=disabled: the integration is only a connectivity check, so an application that never calls a
provider runs without a key, in every environment (spec §10 LLM-01, LLM-09)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

import pytest

from app.kernel.diagnostics import Outcome
from app.kernel.tenancy import ContextKind, TenantContext
from app.modules.llm.adapters import MockLLMProvider
from app.modules.llm.module import _provider, describe
from app.modules.llm.probe import LLMProbe
from app.modules.llm.service import LLMService
from tests.conftest import Harness, make_settings
from tests.unit.test_platform_config import PREVIEW, PRODUCTION, refusal, settings

CHECK = "/api/v1/llm/check"
NOT_CONFIGURED = {"ok": False, "provider": "none", "model": "", "latency_ms": 0, "error_code": "not_configured"}


@pytest.fixture
def disabled(make_harness: Callable[..., Harness]) -> Callable[..., Harness]:
    """The composition of LLM_MODE=disabled: no provider (an explicit None overrides the harness's mock)."""

    def factory(settings: dict[str, Any] | None = None) -> Harness:
        return make_harness({"llm_mode": "disabled", **(settings or {})}, llm_provider=None)

    return factory


# --- Settings: valid in every environment, with no key -------------------------------------------------------


def test_disabled_is_valid_in_every_environment() -> None:
    assert make_settings(llm_mode="disabled").llm_mode.value == "disabled"  # test
    assert settings({**PREVIEW, "app_base_url": "http://localhost:3000"}, app_env="local", llm_mode="disabled")
    assert settings(PREVIEW, app_env="preview", llm_mode="disabled").llm_api_key is None
    production = settings(PRODUCTION, llm_mode="disabled", llm_api_key=..., llm_model=...)  # no key, no model
    assert production.llm_mode.value == "disabled"


def test_production_still_refuses_mock_and_sandbox() -> None:
    for mode in ("mock", "sandbox"):
        assert f"LLM_MODE={mode} is not allowed when APP_ENV=production" in refusal(PRODUCTION, llm_mode=mode)


def test_enabled_modes_still_need_a_key_and_a_model() -> None:
    message = refusal(PREVIEW, app_env="preview", llm_mode="sandbox")
    assert "LLM_API_KEY is required when LLM_MODE is sandbox or live" in message
    assert "LLM_MODEL is required when LLM_MODE is sandbox or live" in message


def test_a_deployment_must_still_name_the_mode() -> None:
    assert "LLM_MODE is required when APP_ENV=production" in refusal(PRODUCTION, llm_mode=...)


# --- Composition and the check ------------------------------------------------------------------------------


def test_no_provider_is_wired_while_disabled() -> None:
    assert _provider(make_settings(llm_mode="disabled")) is None
    assert isinstance(_provider(make_settings(llm_mode="mock")), MockLLMProvider)


@pytest.mark.db
def test_the_check_answers_a_stable_not_configured_state(disabled: Callable[..., Harness]) -> None:
    h = disabled()
    response = h.client.post(CHECK, headers=h.login("user_a"))
    assert response.status_code == 200
    assert response.json() == NOT_CONFIGURED


@pytest.mark.db
def test_the_check_still_needs_a_signed_in_user(disabled: Callable[..., Harness]) -> None:
    assert disabled().client.post(CHECK).status_code == 401


@pytest.mark.db
def test_a_disabled_check_uses_no_quota_and_sends_no_event(disabled: Callable[..., Harness]) -> None:
    h = disabled({"llm_checks_per_user_per_minute": 1, "llm_checks_global_per_day": 1})
    headers = h.login("user_a")
    assert [h.client.post(CHECK, headers=headers).json() for _ in range(3)] == [NOT_CONFIGURED] * 3
    assert h.analytics.events == []


@pytest.mark.db
def test_diagnostics_report_disabled_as_skipped_not_passed(disabled: Callable[..., Harness]) -> None:
    h = disabled({"diagnostics_operator_ids": "user_a"})
    headers = h.login("user_a")
    listing = h.client.get("/api/v1/diagnostics/integrations", headers=headers).json()
    [llm] = [item for item in listing["integrations"] if item["key"] == "llm"]
    assert llm["mode"] == "disabled"
    assert llm["details"] == ["Disabled: no provider is called"]
    result = h.client.post("/api/v1/diagnostics/integrations/llm/check", headers=headers).json()
    assert (result["outcome"], result["mode"]) == ("skipped", "disabled")


def test_the_description_names_no_provider_while_disabled() -> None:
    info = describe(make_settings(llm_mode="disabled"))
    assert info.mode.value == "disabled"
    assert info.details == ("Disabled: no provider is called",)


def test_the_probe_reports_skipped_should_it_ever_run() -> None:
    """The diagnostics service skips a disabled integration before it asks the probe; a probe asked anyway agrees."""
    service = LLMService(None, None, None, checks_per_user_per_minute=1, checks_global_per_day=1)  # type: ignore[arg-type]
    verdict = LLMProbe(describe(make_settings(llm_mode="disabled")), service).check(
        TenantContext(uuid.uuid4(), "user_a", ContextKind.PERSONAL)
    )
    assert verdict.outcome is Outcome.SKIPPED
