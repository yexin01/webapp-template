"""Stripe configuration (DEC-010). Composed into ``app.settings.Settings``."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, SecretStr

from app.kernel.infrastructure.config import CoreSettings, Mode, mode_problems, secret
from app.modules.billing.domain import PlanKey

# A bare lowercase hostname with at least one dot: no scheme, port, path, or wildcard.
_HOSTNAME = re.compile(r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


class BillingSettings(BaseModel):
    billing_mode: Mode = Mode.MOCK
    stripe_secret_key: SecretStr | None = None
    stripe_webhook_secret: SecretStr | None = None
    stripe_price_pro_monthly: str | None = None
    stripe_price_pro_yearly: str | None = None
    # Comma-separated prices that are no longer sold but still grant Pro to existing subscribers.
    stripe_price_pro_monthly_retained: str = ""
    stripe_price_pro_yearly_retained: str = ""
    # The custom domain of Stripe-hosted Checkout and the Customer Portal, if the account has one.
    stripe_custom_domain: str | None = None
    checkouts_per_user_per_hour: int = Field(default=10, ge=1, le=1_000)
    # Each portal session is a Stripe API call, and Stripe's rate limit is shared by the whole account.
    portal_sessions_per_user_per_hour: int = Field(default=20, ge=1, le=1_000)
    # Checkout and portal requests in Stripe calls at once per instance. Each holds a server
    # thread (40 by default, shared by every synchronous route) until Stripe answers.
    billing_max_concurrent_stripe_calls: int = Field(default=4, ge=1, le=16)
    # The scheduled reconcile-billing job reports divergences; it applies them only when true.
    billing_reconcile_apply: bool = False
    # Customers the scheduled job checks per run (one Stripe call each), least recently checked first.
    billing_reconcile_batch_size: int = Field(default=200, ge=1, le=2_000)


def billing_problems(s: BillingSettings, core: CoreSettings) -> list[str]:
    problems = mode_problems("BILLING_MODE", s.billing_mode, core.app_env) + retained_problems(s)
    if s.stripe_custom_domain and not _HOSTNAME.fullmatch(s.stripe_custom_domain):
        problems.append("STRIPE_CUSTOM_DOMAIN must be a hostname such as payments.example.com")
    if s.billing_mode is Mode.DISABLED:
        problems.append("BILLING_MODE must be mock, sandbox, or live")
    if s.billing_mode is Mode.MOCK:
        return problems
    key = secret(s.stripe_secret_key)
    expected = ("sk_live_", "rk_live_") if s.billing_mode is Mode.LIVE else ("sk_test_", "rk_test_")
    if not key:
        problems.append("STRIPE_SECRET_KEY is required when BILLING_MODE is sandbox or live")
    elif not key.startswith(expected):
        problems.append(f"STRIPE_SECRET_KEY does not match BILLING_MODE={s.billing_mode.value}")
    if not secret(s.stripe_webhook_secret).startswith("whsec_"):
        problems.append("STRIPE_WEBHOOK_SECRET is required and must start with whsec_")
    for name, value in (
        ("STRIPE_PRICE_PRO_MONTHLY", s.stripe_price_pro_monthly),
        ("STRIPE_PRICE_PRO_YEARLY", s.stripe_price_pro_yearly),
    ):
        if not value or not value.startswith("price_"):
            problems.append(f"{name} is required and must be a Stripe price ID")
    return problems


def retained_prices(s: BillingSettings) -> dict[str, PlanKey]:
    """Replaced prices that still grant Pro (``STRIPE_PRICE_PRO_*_RETAINED``)."""
    retained: dict[str, PlanKey] = {}
    for key, raw in (
        (PlanKey.PRO_MONTHLY, s.stripe_price_pro_monthly_retained),
        (PlanKey.PRO_YEARLY, s.stripe_price_pro_yearly_retained),
    ):
        for price in (p.strip() for p in raw.split(",")):
            if price:
                retained[price] = key
    return retained


def retained_problems(s: BillingSettings) -> list[str]:
    problems = []
    current = {s.stripe_price_pro_monthly, s.stripe_price_pro_yearly}
    seen: set[str] = set()
    for name, raw in (
        ("STRIPE_PRICE_PRO_MONTHLY_RETAINED", s.stripe_price_pro_monthly_retained),
        ("STRIPE_PRICE_PRO_YEARLY_RETAINED", s.stripe_price_pro_yearly_retained),
    ):
        for price in (p.strip() for p in raw.split(",")):
            if not price:
                continue
            if not price.startswith("price_"):
                problems.append(f"{name} must list Stripe price IDs, separated by commas")
            elif price in current:
                problems.append(f"{name} must not list a price that is still sold")
            elif price in seen:
                problems.append(f"{name} lists a price more than once")
            seen.add(price)
    return problems
