"""Billing and LLM configuration: retained prices and the bounds that protect shared capacity."""

from __future__ import annotations

import pytest

from app.kernel.infrastructure.config import ConfigError
from app.modules.billing.domain import PlanKey
from app.modules.billing.settings import retained_prices
from tests.conftest import PRICE_MONTHLY, make_settings


def test_retained_prices_map_to_their_plan() -> None:
    settings = make_settings(
        stripe_price_pro_monthly_retained=" price_old_a, price_old_b ,",
        stripe_price_pro_yearly_retained="price_old_y",
    )
    assert retained_prices(settings) == {
        "price_old_a": PlanKey.PRO_MONTHLY,
        "price_old_b": PlanKey.PRO_MONTHLY,
        "price_old_y": PlanKey.PRO_YEARLY,
    }
    assert retained_prices(make_settings()) == {}


@pytest.mark.parametrize(
    ("overrides", "problem"),
    [
        ({"stripe_price_pro_monthly_retained": "prod_123"}, "must list Stripe price IDs"),
        ({"stripe_price_pro_yearly_retained": PRICE_MONTHLY}, "must not list a price that is still sold"),
        (
            {"stripe_price_pro_monthly_retained": "price_a", "stripe_price_pro_yearly_retained": "price_a"},
            "lists a price more than once",
        ),
    ],
)
def test_invalid_retained_prices_are_refused(overrides: dict[str, str], problem: str) -> None:
    with pytest.raises(ConfigError, match=problem):
        make_settings(**overrides)


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
