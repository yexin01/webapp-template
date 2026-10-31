"""Billing and LLM configuration: the Pro product and the bounds that protect shared capacity."""

from __future__ import annotations

import pytest

from app.kernel.infrastructure.config import ConfigError, Mode
from app.modules.billing.module import describe
from app.modules.billing.settings import BillingSettings, reconcile_applies
from tests.conftest import PRODUCT_PRO, make_settings

SANDBOX = {"billing_mode": "sandbox", "stripe_secret_key": "sk_test_" + "o" * 24}


@pytest.mark.parametrize("product", [None, "", "price_test_monthly", "pro", "Prod_abc"])
def test_a_sandbox_needs_the_pro_product_as_a_stripe_product_id(product: str | None) -> None:
    with pytest.raises(ConfigError, match="STRIPE_PRODUCT_PRO is required and must be a Stripe product ID"):
        make_settings(**SANDBOX, stripe_product_pro=product)


def test_a_sandbox_with_the_pro_product_is_valid() -> None:
    assert make_settings(**SANDBOX).stripe_product_pro == PRODUCT_PRO


@pytest.mark.parametrize("product", [None, "", "prod_abc"])
def test_the_pro_product_is_optional_in_mock_mode(product: str | None) -> None:
    assert make_settings(stripe_product_pro=product).billing_mode.value == "mock"


def test_the_report_says_whether_the_pro_product_is_set() -> None:
    assert "Pro product: configured" in describe(make_settings()).details
    assert "Pro product: missing" in describe(make_settings(stripe_product_pro=None)).details


def test_the_worker_pool_keeps_a_connection_beside_billing_synchronization() -> None:
    """Synchronization may hold all but one worker connection (BILL-10), so one is too few."""
    with pytest.raises((ConfigError, ValueError)):
        make_settings(database_worker_pool_max_size=1)
    assert make_settings(database_worker_pool_max_size=2).database_worker_pool_max_size == 2


def test_llm_concurrency_stays_well_below_the_server_threads() -> None:
    with pytest.raises((ConfigError, ValueError)):
        make_settings(llm_max_concurrent_checks=17)


@pytest.mark.parametrize("value", [0, 17])
def test_stripe_call_concurrency_stays_well_below_the_server_threads(value: int) -> None:
    with pytest.raises(ConfigError, match="BILLING_MAX_CONCURRENT_STRIPE_CALLS"):
        make_settings(billing_max_concurrent_stripe_calls=value)
    assert make_settings().billing_max_concurrent_stripe_calls == 4


@pytest.mark.parametrize("value", [0, 1_001])
def test_portal_sessions_per_user_are_bounded(value: int) -> None:
    with pytest.raises(ConfigError, match="PORTAL_SESSIONS_PER_USER_PER_HOUR"):
        make_settings(portal_sessions_per_user_per_hour=value)
    assert make_settings().portal_sessions_per_user_per_hour == 20


@pytest.mark.parametrize(
    "value", ["https://payments.example.com", "payments.example.com/x", "payments", "Payments.Example.com", "a.b:8080"]
)
def test_a_custom_domain_is_a_bare_hostname(value: str) -> None:
    with pytest.raises(ConfigError, match="STRIPE_CUSTOM_DOMAIN must be a hostname"):
        make_settings(stripe_custom_domain=value)


def test_a_custom_domain_is_optional() -> None:
    assert make_settings().stripe_custom_domain is None
    assert make_settings(stripe_custom_domain="").stripe_custom_domain is None  # empty means unset
    assert make_settings(stripe_custom_domain="payments.example.com").stripe_custom_domain == "payments.example.com"


@pytest.mark.parametrize("value", [0, 1_001])
def test_user_syncs_per_hour_are_bounded(value: int) -> None:
    with pytest.raises(ConfigError, match="BILLING_SYNCS_PER_USER_PER_HOUR"):
        make_settings(billing_syncs_per_user_per_hour=value)
    assert make_settings().billing_syncs_per_user_per_hour == 30


@pytest.mark.parametrize(
    ("mode", "setting", "applies"),
    [
        (Mode.MOCK, None, False),  # there is no Stripe to compare with
        (Mode.SANDBOX, None, True),
        (Mode.LIVE, None, True),
        (Mode.MOCK, True, True),
        (Mode.SANDBOX, False, False),
        (Mode.LIVE, False, False),
    ],
)
def test_the_scheduled_job_applies_stripes_state_unless_billing_is_mocked_or_told_to_only_report(
    mode: Mode, setting: bool | None, applies: bool
) -> None:
    assert reconcile_applies(BillingSettings(billing_mode=mode, billing_reconcile_apply=setting)) is applies


@pytest.mark.parametrize(("value", "expected"), [("", None), ("true", True), ("false", False)])
def test_the_apply_setting_reads_true_false_and_empty_as_unset(
    monkeypatch: pytest.MonkeyPatch, value: str, expected: bool | None
) -> None:
    monkeypatch.setenv("BILLING_RECONCILE_APPLY", value)
    assert make_settings().billing_reconcile_apply is expected
