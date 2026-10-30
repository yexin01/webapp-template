"""``python -m app.cli setup-stripe``: the Pro product and its two prices in a Stripe sandbox.

Sandbox only, and idempotent: it finds the prices by their lookup keys and
creates only what is missing. It stops, changing nothing, if a price it finds
differs from what was asked for (Stripe prices cannot be edited). It prints the
``.env`` lines to set and never writes ``.env``, never prints the secret key,
and then reads the Customer Portal's default configuration (read only: the
portal stays dashboard-owned, ADR-0001) to print the dashboard steps still needed.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import stripe
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.kernel.errors import DomainError, ForbiddenError
from app.kernel.infrastructure.config import ENV_FILE
from app.modules.billing.adapters.stripe import StripeBillingProvider, _get
from app.modules.billing.domain import PlanKey, PriceCatalog, portal_problems

PRODUCT_ID = "pro"
_PRODUCT_NAME = "Pro"

# The lookup key is the identity of a price: it is how an existing one is found again.
_PRICES: tuple[tuple[PlanKey, str, str, str], ...] = (
    (PlanKey.PRO_MONTHLY, "month", "Pro monthly", "STRIPE_PRICE_PRO_MONTHLY"),
    (PlanKey.PRO_YEARLY, "year", "Pro yearly", "STRIPE_PRICE_PRO_YEARLY"),
)


class SetupError(Exception):
    """A refusal or failure whose message is safe to print: it never holds a key or Stripe's own text."""


class StripeSetupSettings(BaseSettings):
    """Only what this command reads. The application's settings are not loaded: they demand a full configuration."""

    model_config = SettingsConfigDict(
        env_file=ENV_FILE, env_file_encoding="utf-8", extra="ignore", hide_input_in_errors=True
    )

    stripe_secret_key: SecretStr | None = None


@dataclass(frozen=True, slots=True)
class Amounts:
    currency: str
    monthly: int
    yearly: int

    def amount(self, plan: PlanKey) -> int:
        return self.monthly if plan is PlanKey.PRO_MONTHLY else self.yearly


def check_key(key: str) -> None:
    """ENV-01: only a test-mode secret key. The value is never echoed."""
    if not key:
        raise SetupError("STRIPE_SECRET_KEY is not set. Put your Stripe sandbox secret key (sk_test_...) in .env.")
    if not key.startswith("sk_test_"):
        raise SetupError(
            "Refusing to run: STRIPE_SECRET_KEY is not a Stripe test-mode secret key (sk_test_...). "
            "This command only changes sandboxes, and a restricted key cannot create products."
        )


def run(
    key: str,
    amounts: Amounts,
    *,
    api_base: str | None = None,
    out: Callable[[str], None] = print,
) -> None:
    """Create what is missing, print the ``.env`` lines and the dashboard steps. Raises ``SetupError``."""
    check_key(key)
    client = stripe.StripeClient(
        key,
        max_network_retries=2,
        http_client=stripe.HTTPXClient(timeout=15.0, allow_sync_methods=True),
        base_addresses={"api": api_base} if api_base else None,
    )
    try:
        price_ids = _ensure_prices(client, amounts, out)
    except stripe.StripeError as exc:
        # Stripe's message can quote the key's last characters or request values: type and ids only.
        raise SetupError(
            f"Stripe rejected the request ({type(exc).__name__}, status {exc.http_status}, "
            f"code {exc.code}, request {exc.request_id}). Check that the key belongs to the sandbox you mean."
        ) from exc

    out("")
    out("Set these in .env (this command never writes it):")
    out("BILLING_MODE=sandbox")
    for plan, _interval, _name, variable in _PRICES:
        out(f"{variable}={price_ids[plan]}")
    out("STRIPE_SECRET_KEY=<your sk_test_ key, which this command never prints>")
    out("STRIPE_WEBHOOK_SECRET=<whsec_..., from `stripe listen --print-secret`; see docs/integrations.md>")

    catalog = PriceCatalog(price_ids)
    _report_portal(StripeBillingProvider(secret_key=key, catalog=catalog, api_base=api_base), catalog, out)


def _ensure_prices(client: stripe.StripeClient, amounts: Amounts, out: Callable[[str], None]) -> dict[PlanKey, str]:
    found = client.v1.prices.list(params={"lookup_keys": [plan.value for plan, *_ in _PRICES], "limit": 10})
    existing = {str(_get(p, "lookup_key")): p for p in found.data}

    differences: list[str] = []
    for plan, interval, _name, _variable in _PRICES:
        if (price := existing.get(plan.value)) is not None:
            differences += _differences(price, plan, interval, amounts)
    if differences:
        raise SetupError(_differences_message(differences))

    product_id = next((str(_get(p, "product")) for p in existing.values() if _get(p, "product")), None)
    ids: dict[PlanKey, str] = {}
    for plan, interval, name, _variable in _PRICES:
        price = existing.get(plan.value)
        if price is not None:
            ids[plan] = str(_get(price, "id"))
            out(f"Price {plan.value}: {ids[plan]} (already exists, matches)")
            continue
        product_id = product_id or _ensure_product(client, out)
        amount = amounts.amount(plan)
        created = client.v1.prices.create(
            params={
                "product": product_id,
                "currency": amounts.currency,
                "unit_amount": amount,
                "recurring": {"interval": interval},
                "lookup_key": plan.value,
                "nickname": name,
            },
            # The same request repeated within a day returns the price it made, not a second one.
            options={"idempotency_key": f"setup-stripe-{plan.value}-{product_id}-{amounts.currency}-{amount}"},
        )
        ids[plan] = str(created.id)
        out(f"Price {plan.value}: {ids[plan]} (created)")
    return ids


def _ensure_product(client: stripe.StripeClient, out: Callable[[str], None]) -> str:
    """The product is found by its own ID, so an interrupted run never leaves two."""
    try:
        product = client.v1.products.retrieve(PRODUCT_ID)
    except stripe.InvalidRequestError as exc:
        if exc.code != "resource_missing":
            raise
        client.v1.products.create(
            params={"id": PRODUCT_ID, "name": _PRODUCT_NAME},
            options={"idempotency_key": f"setup-stripe-product-{PRODUCT_ID}"},
        )
        out(f"Product {PRODUCT_ID}: created")
        return PRODUCT_ID
    if not _get(product, "active"):
        raise SetupError(
            f"The product {PRODUCT_ID} exists but is archived. Unarchive it in the Stripe Dashboard "
            "(Product catalog), or create the Pro prices there yourself and put their IDs in .env."
        )
    out(f"Product {PRODUCT_ID}: already exists")
    return PRODUCT_ID


def _differences(price: Any, plan: PlanKey, interval: str, amounts: Amounts) -> list[str]:
    recurring = _get(price, "recurring")
    problems: list[str] = []
    if not _get(price, "active"):
        problems.append("it is archived")
    if str(_get(price, "currency")) != amounts.currency:
        problems.append(f"its currency is {str(_get(price, 'currency')).upper()}, not {amounts.currency.upper()}")
    if _get(price, "unit_amount") != amounts.amount(plan):
        problems.append(f"its amount is {_get(price, 'unit_amount')} (minor units), not {amounts.amount(plan)}")
    if not recurring or _get(recurring, "interval") != interval or _get(recurring, "interval_count") not in (None, 1):
        problems.append(f"it does not bill once every {interval}")
    return [f"{plan.value} ({_get(price, 'id')}): " + ", ".join(problems)] if problems else []


def _differences_message(differences: list[str]) -> str:
    return (
        "Nothing was created or changed. A price that already has a lookup key here differs from what you asked for:\n"
        + "\n".join(f"- {d}" for d in differences)
        + "\nStripe prices cannot be edited, and this command never moves a lookup key. To change a price: create "
        "the new one in the Stripe Dashboard, move the lookup key to it, set STRIPE_PRICE_PRO_MONTHLY (or "
        "STRIPE_PRICE_PRO_YEARLY) to the new ID, and list the old ID in STRIPE_PRICE_PRO_MONTHLY_RETAINED (or "
        "STRIPE_PRICE_PRO_YEARLY_RETAINED) so existing subscribers keep Pro. Or re-run with the amounts the existing "
        "prices have."
    )


_PORTAL_STEP = (
    "Open the Stripe Dashboard in test mode > Settings > Billing > Customer portal, turn on what is missing, and Save."
)

_OTHER_STEPS = (
    "Stripe Dashboard > Billing > Revenue recovery > Retries: when all retries fail, cancel the subscription "
    "or mark it unpaid (leaving it past due keeps Pro indefinitely, DEC-010).",
    "Run `stripe listen --forward-to localhost:8000/api/v1/webhooks/stripe` in another terminal while you test. "
    "For a deployed endpoint choose the payload format Snapshot, not Thin (docs/integrations.md).",
)


def _report_portal(provider: StripeBillingProvider, catalog: PriceCatalog, out: Callable[[str], None]) -> None:
    out("")
    try:
        problems = portal_problems(provider.describe_portal(), catalog)
    except ForbiddenError:
        out("Customer Portal: not checked (the key cannot read portal configurations).")
        problems = []
    except DomainError as exc:
        out(f"Customer Portal: not checked ({exc.public_message})")
        problems = []
    else:
        if not problems:
            out("Customer Portal: the default configuration matches DEC-010.")
    out("Still to do in the Stripe Dashboard (this command does not change the portal):")
    for problem in problems:
        out(f"- Customer Portal: {problem}")
    if problems:
        out(f"- {_PORTAL_STEP}")
    for step in _OTHER_STEPS:
        out(f"- {step}")
