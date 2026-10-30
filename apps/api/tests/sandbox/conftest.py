"""The Stripe sandbox suite: real test-mode objects, never run by the normal suite.

It runs only with STRIPE_SANDBOX_TESTS=1 and a Stripe sandbox secret key (sk_test_..., unrestricted:
test clocks need it) in STRIPE_SECRET_KEY, with the Pro prices that `pnpm setup:stripe` creates in
STRIPE_PRICE_PRO_MONTHLY and STRIPE_PRICE_PRO_YEARLY. The key and prices come from the environment or the
repository .env, and are read when this file is imported, at collection: the shared fixtures remove every
STRIPE_* variable once the session starts. Run it with `pnpm test:stripe-sandbox`.

Everything it creates is deleted when each test ends (a test clock takes its customers and subscriptions
with it). Names start with `template-sandbox-`, so a leftover from a killed run is easy to find.
"""

from __future__ import annotations

import os
import warnings
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
import stripe
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.kernel.infrastructure.config import ENV_FILE
from app.modules.billing.adapters.stripe import StripeBillingProvider
from app.modules.billing.domain import PlanKey, PriceCatalog
from tests.conftest import Harness

HERE = Path(__file__).parent


class _SandboxSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, env_file_encoding="utf-8", extra="ignore")

    stripe_secret_key: SecretStr | None = None
    stripe_price_pro_monthly: str = ""
    stripe_price_pro_yearly: str = ""


ENABLED = os.environ.get("STRIPE_SANDBOX_TESTS") == "1"
_SETTINGS = _SandboxSettings() if ENABLED else None
KEY = _SETTINGS.stripe_secret_key.get_secret_value() if _SETTINGS and _SETTINGS.stripe_secret_key else ""
PRICE_MONTHLY = _SETTINGS.stripe_price_pro_monthly if _SETTINGS else ""
PRICE_YEARLY = _SETTINGS.stripe_price_pro_yearly if _SETTINGS else ""


def unavailable() -> str | None:
    """Why the suite is skipped, or None when it can run."""
    if not ENABLED:
        return "Stripe sandbox suite: set STRIPE_SANDBOX_TESTS=1 (pnpm test:stripe-sandbox) to run it"
    if not KEY.startswith("sk_test_"):
        return (
            "Stripe sandbox suite: STRIPE_SECRET_KEY must be a sandbox secret key (sk_test_...); test clocks need one"
        )
    if not (PRICE_MONTHLY.startswith("price_") and PRICE_YEARLY.startswith("price_")):
        return "Stripe sandbox suite: set STRIPE_PRICE_PRO_MONTHLY and STRIPE_PRICE_PRO_YEARLY (pnpm setup:stripe)"
    return None


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    reason = unavailable()
    for item in items:
        if HERE not in item.path.parents:
            continue
        item.add_marker(pytest.mark.sandbox)
        if reason is not None:
            item.add_marker(pytest.mark.skip(reason=reason))


@pytest.fixture
def admin() -> stripe.StripeClient:
    """A separate client for the test's own setup and checks, so the code under test is never its own witness."""
    return stripe.StripeClient(KEY, max_network_retries=2)


@pytest.fixture
def cleanup() -> Iterator[list[Callable[[], object]]]:
    """Undo steps, run last-in first when the test ends, whatever its outcome."""
    steps: list[Callable[[], object]] = []
    yield steps
    failed = 0
    for step in reversed(steps):
        try:
            step()
        except stripe.StripeError:
            failed += 1
    if failed:
        warnings.warn(
            f"{failed} Stripe sandbox object(s) could not be deleted; remove leftovers named template-sandbox-*",
            stacklevel=1,
        )


@pytest.fixture
def provider() -> StripeBillingProvider:
    """The adapter exactly as the application builds it for BILLING_MODE=sandbox."""
    catalog = PriceCatalog({PlanKey.PRO_MONTHLY: PRICE_MONTHLY, PlanKey.PRO_YEARLY: PRICE_YEARLY})
    return StripeBillingProvider(secret_key=KEY, catalog=catalog)


@pytest.fixture
def world(make_harness: Callable[..., Harness], provider: StripeBillingProvider) -> Harness:
    """The application on the test database, with the real Stripe adapter and the real prices."""
    return make_harness(
        {"stripe_price_pro_monthly": PRICE_MONTHLY, "stripe_price_pro_yearly": PRICE_YEARLY},
        billing_provider=provider,
    )
