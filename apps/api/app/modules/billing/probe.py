"""Diagnostics probe for Stripe: the key works and reads subscriptions, the Pro prices belong to the Pro product,
and the portal matches DEC-010."""

from __future__ import annotations

from app.kernel.diagnostics import IntegrationInfo, IntegrationMode, Outcome, Verdict
from app.kernel.errors import ForbiddenError
from app.kernel.tenancy import TenantContext
from app.modules.billing.domain import PLAN_INTERVALS, PlanKey, PriceCatalog, portal_problems
from app.modules.billing.ports import BillingProvider

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
        product = self._catalog.product
        for plan, interval in PLAN_INTERVALS.items():
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
            if price.product_id != product:
                problems.append(
                    f"{label} price belongs to product {price.product_id or '?'}, but STRIPE_PRODUCT_PRO is {product}"
                )
            if price.currency:
                currencies.add(price.currency.upper())
            found.append(f"{label.lower()} ({price.recurring_interval or 'one-time'}, {price.currency or '?'})")
        if len(currencies) > 1:
            problems.append("the monthly and yearly prices use different currencies")
        access: list[str] = []
        try:
            self._provider.check_subscriptions_readable()
        except ForbiddenError:
            access.append("the API key cannot read Subscriptions, so no subscription could be synchronized")
        portal_note = "Customer Portal default configuration matches DEC-010"
        portal_issues: list[str] = []
        try:
            portal_issues = portal_problems(self._provider.describe_portal(), self._catalog)
        except ForbiddenError:
            portal_note = "Customer Portal: not checked (the API key cannot read portal configurations)"
        if problems or portal_issues or access:
            summary = "Stripe is reachable, but " + "; ".join([*problems, *portal_issues, *access]) + "."
            code = "price_mismatch" if problems else "portal_mismatch" if portal_issues else "permission_denied"
            return Verdict(Outcome.FAILED, summary, code)
        return Verdict(
            Outcome.PASSED,
            "API key accepted; Pro prices verified: " + ", ".join(found) + f". Subscriptions readable. {portal_note}."
            " Write access to Subscriptions, which BILLING_ON_USER_DELETED needs to end a deleted user's subscription,"
            " is not checked."
            " Webhook endpoint and signing secret: not checked (confirm with a test checkout).",
        )
