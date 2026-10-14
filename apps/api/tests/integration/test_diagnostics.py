"""Integration diagnostics: configuration report, checks, and safety rules."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from typing import Any

import pytest

from app.kernel.infrastructure.config import AppEnv
from app.kernel.tenancy import Role
from app.modules.billing.ports import PriceDescription
from app.modules.identity.domain import ExternalMembership
from app.modules.llm.adapters import MockLLMProvider
from app.settings import Settings
from tests.conftest import PRICE_MONTHLY, PRICE_YEARLY, Harness

pytestmark = pytest.mark.db

BASE = "/api/v1/diagnostics/integrations"
PUBLISHABLE = "pk_test_" + base64.b64encode(b"example.clerk.accounts.dev$").decode()
# Test-only placeholders; assembled at runtime so secret scanners do not flag them.
SECRETS = {
    "clerk_secret_key": "sk_test_" + "c" * 30,
    "clerk_webhook_signing_secret": "whsec_" + "w" * 30,
    "stripe_secret_key": "sk_test_" + "s" * 30,
    "llm_api_key": "llm-" + "k" * 30,
    "sentry_dsn": "https://" + "d" * 32 + "@o1.ingest.sentry.invalid/2",
}
OPERATORS = {"diagnostics_operator_ids": "user_a,user_b"}
SANDBOX = {
    **OPERATORS,
    "clerk_publishable_key": PUBLISHABLE,
    "clerk_authorized_parties": "http://localhost:3000",
    "billing_mode": "sandbox",
    "llm_mode": "sandbox",
    "llm_model": "test-model",
    "analytics_mode": "sandbox",
    "posthog_project_api_key": "phc_test_only",
    "error_reporting_mode": "sandbox",
    **SECRETS,
}


class FakeErrorReporter:
    def __init__(self, event_id: str | None = "0123456789abcdef0123456789abcdef") -> None:
        self.event_id = event_id
        self.sent = 0

    def send_test_event(self) -> str | None:
        self.sent += 1
        return self.event_id


def _price(price_id: str, interval: str, **kwargs: Any) -> PriceDescription:
    values: dict[str, Any] = {
        "price_id": price_id,
        "active": True,
        "recurring_interval": interval,
        "currency": "eur",
        "livemode": False,
    }
    values.update(kwargs)
    return PriceDescription(**values)


@pytest.fixture
def sandbox(make_harness: Callable[..., Harness]) -> Callable[..., Harness]:
    def factory(settings: dict[str, Any] | None = None, **overrides: Any) -> Harness:
        overrides.setdefault("error_reporter", FakeErrorReporter())
        h = make_harness({**SANDBOX, **(settings or {})}, **overrides)
        h.billing.set_price(_price(PRICE_MONTHLY, "month"))
        h.billing.set_price(_price(PRICE_YEARLY, "year"))
        return h

    return factory


@pytest.fixture
def operator_harness(make_harness: Callable[..., Harness]) -> Harness:
    return make_harness(OPERATORS)


def check(h: Harness, key: str, user: str = "user_a") -> dict[str, Any]:
    response = h.client.post(f"{BASE}/{key}/check", headers=h.login(user))
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def test_requires_authentication(harness: Harness) -> None:
    assert harness.client.get(BASE).status_code == 401
    assert harness.client.post(f"{BASE}/database/check").status_code == 401


def test_report_lists_every_integration_without_secrets(sandbox: Callable[..., Harness]) -> None:
    h = sandbox()
    response = h.client.get(BASE, headers=h.login("user_a"))
    assert response.status_code == 200
    body = response.json()
    assert body["environment"] == "test"
    modes = {item["key"]: item["mode"] for item in body["integrations"]}
    assert modes == {
        "database": "local",
        "identity": "sandbox",
        "billing": "sandbox",
        "llm": "sandbox",
        "analytics": "sandbox",
        "error_reporting": "sandbox",
    }
    raw = response.text
    for name, value in SECRETS.items():
        assert value not in raw, name
    # Connection strings, including the local one, are never echoed.
    assert "postgresql://" not in raw
    assert "postgres:postgres" not in raw


def test_non_operators_cannot_see_or_run_diagnostics(make_harness: Callable[..., Harness]) -> None:
    """Diagnostics expose configuration and call providers: deny by default."""
    h = make_harness({"diagnostics_operator_ids": "user_ops"})
    outsider = h.login("user_a")
    assert h.client.get(BASE, headers=outsider).status_code == 404
    assert h.client.post(f"{BASE}/database/check", headers=outsider).status_code == 404
    operator = h.login("user_ops")
    assert h.client.get(BASE, headers=operator).status_code == 200

    # Without any operator, only APP_ENV=local opens diagnostics to signed-in users.
    closed = make_harness()
    assert closed.client.get(BASE, headers=closed.login("user_a")).status_code == 404


def test_mock_and_disabled_integrations_are_skipped_not_passed(operator_harness: Harness) -> None:
    h = operator_harness
    for key in ("billing", "llm", "identity", "analytics", "error_reporting"):
        body = check(h, key)
        assert body["outcome"] == "skipped", key
        assert body["mode"] in ("mock", "disabled")
    assert h.billing.calls == []
    assert h.analytics.events == []


def test_database_check_runs_as_least_privileged_role_with_rls(operator_harness: Harness) -> None:
    body = check(operator_harness, "database")
    assert body["outcome"] == "passed", body
    assert body["mode"] == "local"
    assert "as app_api" in body["summary"]
    assert "row-level security" in body["summary"]


def test_identity_check_reads_memberships_from_directory(sandbox: Callable[..., Harness]) -> None:
    h = sandbox()
    h.directory.set("user_a", [ExternalMembership("org_1", Role.ADMIN), ExternalMembership("org_2", Role.MEMBER)])
    body = check(h, "identity")
    assert body["outcome"] == "passed"
    assert "2 organization memberships" in body["summary"]


def test_identity_check_reports_directory_outage(sandbox: Callable[..., Harness]) -> None:
    h = sandbox()
    h.directory.available = False
    body = check(h, "identity")
    assert body["outcome"] == "failed"
    assert body["error_code"] == "dependency_unavailable"


def test_billing_check_verifies_both_prices(sandbox: Callable[..., Harness]) -> None:
    h = sandbox()
    body = check(h, "billing")
    assert body["outcome"] == "passed", body
    assert "monthly (month, eur)" in body["summary"]
    assert "yearly (year, eur)" in body["summary"]


@pytest.mark.parametrize(
    ("monthly", "expected"),
    [
        ({"recurring_interval": "year"}, "bills every year, expected every month"),
        ({"active": False}, "archived"),
        ({"livemode": True}, "belongs to live mode"),
        ({"currency": "usd"}, "different currencies"),
    ],
)
def test_billing_check_flags_misconfigured_prices(
    sandbox: Callable[..., Harness], monthly: dict[str, Any], expected: str
) -> None:
    h = sandbox()
    h.billing.set_price(_price(PRICE_MONTHLY, "month", **monthly))
    body = check(h, "billing")
    assert body["outcome"] == "failed"
    assert body["error_code"] == "price_mismatch"
    assert expected in body["summary"]


def test_billing_check_reports_unknown_price(sandbox: Callable[..., Harness]) -> None:
    h = sandbox({"stripe_price_pro_yearly": "price_missing"})
    body = check(h, "billing")
    assert body["outcome"] == "failed"
    assert body["error_code"] == "invalid_request"


def test_llm_check_reports_provider_failure(sandbox: Callable[..., Harness]) -> None:
    h = sandbox(llm_provider=MockLLMProvider(ok=False, error_code="auth_failed"))
    body = check(h, "llm")
    assert body["outcome"] == "failed"
    assert body["error_code"] == "auth_failed"


def test_analytics_check_sends_allowlisted_event_and_detects_export_failure(
    sandbox: Callable[..., Harness],
) -> None:
    h = sandbox()
    assert check(h, "analytics")["outcome"] == "passed"
    assert [(event, props) for _, event, props in h.analytics.events] == [("integration_test_sent", {"source": "api"})]
    h.analytics.export_ok = False
    body = check(h, "analytics")
    assert body["outcome"] == "failed"
    assert body["error_code"] == "export_failed"


def test_error_reporting_check_returns_event_reference(sandbox: Callable[..., Harness]) -> None:
    reporter = FakeErrorReporter()
    h = sandbox(error_reporter=reporter)
    body = check(h, "error_reporting")
    assert body["outcome"] == "sent"
    assert body["reference"] == reporter.event_id
    assert reporter.sent == 1

    h2 = sandbox(error_reporter=FakeErrorReporter(event_id=None))
    assert check(h2, "error_reporting")["outcome"] == "failed"


def test_checks_are_rate_limited_per_user(make_harness: Callable[..., Harness]) -> None:
    h = make_harness({**OPERATORS, "diagnostics_runs_per_user_per_minute": 2})
    headers = h.login("user_a")
    assert h.client.post(f"{BASE}/database/check", headers=headers).status_code == 200
    assert h.client.post(f"{BASE}/database/check", headers=headers).status_code == 200
    limited = h.client.post(f"{BASE}/database/check", headers=headers)
    assert limited.status_code == 429
    assert limited.headers["retry-after"]
    # Another user is unaffected.
    assert h.client.post(f"{BASE}/database/check", headers=h.login("user_b")).status_code == 200


def test_disabled_diagnostics_are_not_found(make_harness: Callable[..., Harness]) -> None:
    h = make_harness({**OPERATORS, "diagnostics_enabled": False})
    headers = h.login("user_a")
    assert h.client.get(BASE, headers=headers).status_code == 404
    assert h.client.post(f"{BASE}/database/check", headers=headers).status_code == 404


def test_unknown_integration_is_rejected(operator_harness: Harness) -> None:
    response = operator_harness.client.post(f"{BASE}/smtp/check", headers=operator_harness.login("user_a"))
    assert response.status_code == 422
    assert json.loads(response.text)["code"] == "VALIDATION_FAILED"


def test_operator_ids_parsed_and_local_default_is_open_only_locally() -> None:
    from app.modules.diagnostics.settings import open_to_any_signed_in_user
    from tests.conftest import make_settings

    def s(**kw: Any) -> Settings:
        return Settings.model_construct(**kw)

    assert make_settings(diagnostics_operator_ids=" user_1, ,user_2 ").diagnostics_operator_ids == ["user_1", "user_2"]
    local_open = s(app_env=AppEnv.LOCAL, diagnostics_operator_ids=[])
    assert open_to_any_signed_in_user(local_open, local_open)
    local_listed = s(app_env=AppEnv.LOCAL, diagnostics_operator_ids=["user_1"])
    assert not open_to_any_signed_in_user(local_listed, local_listed)
    for env in (AppEnv.TEST, AppEnv.PREVIEW, AppEnv.PRODUCTION):
        other = s(app_env=env, diagnostics_operator_ids=[])
        assert not open_to_any_signed_in_user(other, other)


def test_diagnostics_default_off_in_production_only() -> None:
    from app.modules.diagnostics.settings import diagnostics_active

    def s(**kw: Any) -> Settings:
        return Settings.model_construct(**kw)

    prod_default = s(app_env=AppEnv.PRODUCTION, diagnostics_enabled=None)
    assert diagnostics_active(prod_default, prod_default) is False
    prod_on = s(app_env=AppEnv.PRODUCTION, diagnostics_enabled=True)
    assert diagnostics_active(prod_on, prod_on) is True
    for env in (AppEnv.LOCAL, AppEnv.PREVIEW, AppEnv.TEST):
        other = s(app_env=env, diagnostics_enabled=None)
        assert diagnostics_active(other, other) is True
