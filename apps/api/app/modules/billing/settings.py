"""Stripe configuration (DEC-010). Composed into ``app.settings.Settings``."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, SecretStr

from app.kernel.infrastructure.config import CoreSettings, Mode, mode_problems, secret

# A bare lowercase hostname with at least one dot: no scheme, port, path, or wildcard.
_HOSTNAME = re.compile(r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


class BillingSettings(BaseModel):
    billing_mode: Mode = Mode.MOCK
    stripe_secret_key: SecretStr | None = None
    stripe_webhook_secret: SecretStr | None = None
    stripe_price_pro_monthly: str | None = None
    stripe_price_pro_yearly: str | None = None
    # Any monthly or yearly price of this product grants Pro, so replacing a price keeps its subscribers.
    stripe_product_pro: str | None = None
    # The custom domain of Stripe-hosted Checkout and the Customer Portal, if the account has one.
    stripe_custom_domain: str | None = None
    checkouts_per_user_per_hour: int = Field(default=10, ge=1, le=1_000)
    # Each portal session is a Stripe API call, and Stripe's rate limit is shared by the whole account.
    portal_sessions_per_user_per_hour: int = Field(default=20, ge=1, le=1_000)
    # Each re-read a signed-in user asks for after checkout or the portal is a Stripe call.
    billing_syncs_per_user_per_hour: int = Field(default=30, ge=1, le=1_000)
    # Checkout and portal requests in Stripe calls at once per instance. Each holds a server
    # thread (40 by default, shared by every synchronous route) until Stripe answers.
    billing_max_concurrent_stripe_calls: int = Field(default=4, ge=1, le=16)
    # Whether the scheduled reconcile-billing job applies Stripe's state or only reports divergences.
    # Unset: it applies, except in mock mode (``reconcile_applies``).
    billing_reconcile_apply: bool | None = None
    # Customers the scheduled job checks per run (one Stripe call each), least recently checked first.
    billing_reconcile_batch_size: int = Field(default=200, ge=1, le=2_000)


def reconcile_applies(s: BillingSettings) -> bool:
    """Whether the scheduled job applies what it finds (BILL-19).

    A webhook that never arrives would otherwise leave Pro in place after a cancellation, with only a log line
    to say so. Mock mode has no Stripe to compare with.
    """
    if s.billing_reconcile_apply is not None:
        return s.billing_reconcile_apply
    return s.billing_mode is not Mode.MOCK


def billing_problems(s: BillingSettings, core: CoreSettings) -> list[str]:
    problems = mode_problems("BILLING_MODE", s.billing_mode, core.app_env)
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
    if not s.stripe_product_pro or not s.stripe_product_pro.startswith("prod_"):
        problems.append("STRIPE_PRODUCT_PRO is required and must be a Stripe product ID")
    return problems
