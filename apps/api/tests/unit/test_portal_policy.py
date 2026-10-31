"""The Customer Portal policy of DEC-010 / ADR-0001: what a default configuration may and may not offer."""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.modules.billing.domain import PlanKey, PortalConfiguration, PriceCatalog, portal_problems

CATALOG = PriceCatalog({PlanKey.PRO_MONTHLY: "price_m", PlanKey.PRO_YEARLY: "price_y"}, "prod_pro")
GOOD = PortalConfiguration(
    active=True,
    payment_method_update=True,
    invoice_history=True,
    cancel_enabled=True,
    cancel_mode="at_period_end",
    switch_enabled=True,
    allowed_updates=("price", "quantity"),
    switch_prices=frozenset({"price_m", "price_y"}),
)


def test_a_configuration_that_follows_the_policy_has_no_problems() -> None:
    assert portal_problems(GOOD, CATALOG) == []


def test_product_choices_are_not_checked() -> None:
    """Proration, promotion codes, and customer details are the adopter's to set."""
    assert portal_problems(replace(GOOD, allowed_updates=("price", "promotion_code")), CATALOG) == []


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"active": False}, ["inactive"]),
        ({"payment_method_update": False}, ["update their payment method"]),
        ({"invoice_history": False}, ["invoice history"]),
        ({"cancel_enabled": False}, ["does not let customers cancel"]),
        ({"cancel_mode": "immediately"}, ["cancels immediately instead of at the end of the billing period"]),
        ({"switch_enabled": False}, ["switch between the Pro prices"]),
        ({"allowed_updates": ("quantity",)}, ["switch between the Pro prices"]),
        ({"switch_prices": frozenset({"price_m"})}, ["cannot switch to the pro_yearly price price_y"]),
        ({"switch_prices": frozenset()}, ["pro_monthly price price_m", "pro_yearly price price_y"]),
        (
            {"switch_prices": frozenset({"price_m", "price_y", "price_old"})},
            ["also offers price_old, which checkout does not sell"],
        ),
    ],
)
def test_each_departure_from_the_policy_is_named(change: dict[str, object], expected: list[str]) -> None:
    problems = portal_problems(replace(GOOD, **change), CATALOG)  # type: ignore[arg-type]
    assert len(problems) == len(expected), problems
    for fragment, problem in zip(expected, problems, strict=True):
        assert fragment in problem


def test_without_a_default_configuration_the_dashboard_step_is_named() -> None:
    [problem] = portal_problems(None, CATALOG)
    assert "no default Customer Portal configuration" in problem
    assert "Settings > Billing > Customer portal" in problem


def test_switching_off_does_not_also_list_every_missing_price() -> None:
    assert len(portal_problems(replace(GOOD, switch_enabled=False, switch_prices=frozenset()), CATALOG)) == 1
