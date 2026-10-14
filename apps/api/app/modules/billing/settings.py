"""Stripe configuration (DEC-010). Composed into ``app.settings.Settings``."""

from __future__ import annotations

from pydantic import BaseModel, Field, SecretStr

from app.kernel.infrastructure.config import CoreSettings, Mode, mode_problems, secret


class BillingSettings(BaseModel):
    billing_mode: Mode = Mode.MOCK
    stripe_secret_key: SecretStr | None = None
    stripe_webhook_secret: SecretStr | None = None
    stripe_price_pro_monthly: str | None = None
    stripe_price_pro_yearly: str | None = None
    checkouts_per_user_per_hour: int = Field(default=10, ge=1, le=1_000)
    # The scheduled reconcile-billing job reports divergences; it applies them only when true.
    billing_reconcile_apply: bool = False
    # Customers the scheduled job checks per run (one Stripe call each), least recently checked first.
    billing_reconcile_batch_size: int = Field(default=200, ge=1, le=2_000)


def billing_problems(s: BillingSettings, core: CoreSettings) -> list[str]:
    problems = mode_problems("BILLING_MODE", s.billing_mode, core.app_env)
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
