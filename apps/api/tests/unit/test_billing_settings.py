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
