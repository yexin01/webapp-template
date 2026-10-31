"""Which plan a price has (BILL-03): a configured price, or a monthly or yearly price of the Pro product."""

from __future__ import annotations

import pytest

from app.modules.billing.domain import PlanKey, PriceCatalog

PRICES = {PlanKey.PRO_MONTHLY: "price_m", PlanKey.PRO_YEARLY: "price_y"}
PRODUCT = "prod_pro"
CATALOG = PriceCatalog(PRICES, PRODUCT)


def test_checkout_sells_only_the_configured_prices() -> None:
    assert CATALOG.price_for(PlanKey.PRO_MONTHLY) == "price_m"
    assert CATALOG.price_for(PlanKey.PRO_YEARLY) == "price_y"


@pytest.mark.parametrize(
    ("price", "product", "interval", "count", "expected"),
    [
        # The configured prices, whatever else is known about them.
        ("price_m", PRODUCT, "month", 1, PlanKey.PRO_MONTHLY),
        ("price_y", PRODUCT, "year", 1, PlanKey.PRO_YEARLY),
        ("price_m", None, None, None, PlanKey.PRO_MONTHLY),
        ("price_y", "prod_other", "week", 3, PlanKey.PRO_YEARLY),
        # An older price of the Pro product keeps the plan of its interval: Stripe prices cannot be edited.
        ("price_old", PRODUCT, "month", 1, PlanKey.PRO_MONTHLY),
        ("price_old", PRODUCT, "year", 1, PlanKey.PRO_YEARLY),
        # A price of another product grants nothing.
        ("price_old", "prod_other", "month", 1, PlanKey.UNKNOWN),
        ("price_old", None, "month", 1, PlanKey.UNKNOWN),
        # Only one month or one year counts.
        ("price_old", PRODUCT, "week", 1, PlanKey.UNKNOWN),
        ("price_old", PRODUCT, "day", 1, PlanKey.UNKNOWN),
        ("price_old", PRODUCT, "month", 3, PlanKey.UNKNOWN),
        ("price_old", PRODUCT, "month", 12, PlanKey.UNKNOWN),
        ("price_old", PRODUCT, "year", 2, PlanKey.UNKNOWN),
        # What Stripe did not say fails closed.
        ("price_old", PRODUCT, None, 1, PlanKey.UNKNOWN),
        ("price_old", PRODUCT, "month", None, PlanKey.UNKNOWN),
        ("price_old", PRODUCT, "Month", 1, PlanKey.UNKNOWN),
        (None, PRODUCT, "month", 1, PlanKey.UNKNOWN),
    ],
)
def test_a_price_has_a_plan_when_it_is_configured_or_a_month_or_year_of_the_pro_product(
    price: str | None, product: str | None, interval: str | None, count: int | None, expected: PlanKey
) -> None:
    assert CATALOG.key_for(price, product, interval, count) is expected


@pytest.mark.parametrize("product", [None, ""])
def test_without_a_pro_product_only_the_configured_prices_have_a_plan(product: str | None) -> None:
    """Mock mode: no product is set, and a price that names no product must not match the missing one."""
    catalog = PriceCatalog(PRICES, product)
    assert catalog.key_for("price_m") is PlanKey.PRO_MONTHLY
    assert catalog.key_for("price_old", None, "month", 1) is PlanKey.UNKNOWN
    assert catalog.key_for("price_old", "", "month", 1) is PlanKey.UNKNOWN
    assert catalog.key_for("price_old", "prod_pro", "month", 1) is PlanKey.UNKNOWN


def test_an_unconfigured_price_is_unknown_without_the_price_details() -> None:
    assert CATALOG.key_for("price_old") is PlanKey.UNKNOWN
