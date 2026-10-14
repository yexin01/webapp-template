"""Diagnostics probe for Stripe: the API key works and the Pro prices are configured correctly."""

from __future__ import annotations

from app.kernel.diagnostics import IntegrationInfo, IntegrationMode, Outcome, Verdict
from app.kernel.tenancy import TenantContext
from app.modules.billing.domain import PlanKey, PriceCatalog
from app.modules.billing.ports import BillingProvider

_EXPECTED_INTERVALS = {PlanKey.PRO_MONTHLY: "month", PlanKey.PRO_YEARLY: "year"}
_PLAN_LABELS = {PlanKey.PRO_MONTHLY: "Monthly", PlanKey.PRO_YEARLY: "Yearly"}


class BillingProbe:
    def __init__(self, info: IntegrationInfo, provider: BillingProvider | None, catalog: PriceCatalog) -> None:
        self._info = info
        self._provider = provider
        self._catalog = catalog

    @property
    def info(self) -> IntegrationInfo:
        return self._info

    def check(self, ctx: TenantContext) -> Verdict:
        if self._provider is None:
            return Verdict(Outcome.FAILED, "Billing is not configured.", "not_configured")
        expect_live = self._info.mode is IntegrationMode.LIVE
        problems: list[str] = []
        found: list[str] = []
        currencies: set[str] = set()
        for plan, interval in _EXPECTED_INTERVALS.items():
            label = _PLAN_LABELS[plan]
            price_id = self._catalog.price_for(plan)
            if price_id is None:
                problems.append(f"{label} price is not configured")
                continue
            price = self._provider.describe_price(price_id)
            if not price.active:
                problems.append(f"{label} price is archived")
            if price.recurring_interval != interval:
                problems.append(
                    f"{label} price bills every {price.recurring_interval or 'one-time'}, expected every {interval}"
                )
            if price.livemode != expect_live:
                problems.append(f"{label} price belongs to {'live' if price.livemode else 'test'} mode")
            if price.currency:
                currencies.add(price.currency.upper())
            found.append(f"{label.lower()} ({price.recurring_interval or 'one-time'}, {price.currency or '?'})")
        if len(currencies) > 1:
            problems.append("the monthly and yearly prices use different currencies")
        if problems:
            return Verdict(Outcome.FAILED, "Stripe is reachable, but " + "; ".join(problems) + ".", "price_mismatch")
        return Verdict(Outcome.PASSED, "API key accepted; Pro prices verified: " + ", ".join(found) + ".")
