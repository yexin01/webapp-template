"""Wiring for the billing module."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.kernel.diagnostics import IntegrationInfo, IntegrationKey, IntegrationMode
from app.kernel.http.auth import AuthDeps
from app.kernel.infrastructure.config import AppEnv, CoreSettings, Mode
from app.kernel.jobs import Job
from app.kernel.wiring import Contribution, Kernel, pick
from app.modules.billing.adapters.fakes import InMemoryBillingProvider
from app.modules.billing.adapters.repository import PgBillingRepository
from app.modules.billing.adapters.stripe import StripeBillingProvider, StripeWebhookVerifier
from app.modules.billing.domain import EntitlementChanged, PlanKey, PriceCatalog
from app.modules.billing.ports import BillingProvider, BillingWebhookVerifier
from app.modules.billing.probe import BillingProbe
from app.modules.billing.routes import build_action_router, build_read_router, build_webhook_router
from app.modules.billing.service import BillingService, BillingUrls
from app.modules.billing.settings import BillingSettings, retained_prices


@dataclass
class BillingModule:
    service: BillingService
    catalog: PriceCatalog
    contribution: Contribution


def build_billing(
    core: CoreSettings, settings: BillingSettings, kernel: Kernel, auth: AuthDeps, overrides: Mapping[str, Any]
) -> BillingModule:
    contribution = Contribution()
    catalog = PriceCatalog(
        {
            key: price
            for key, price in (
                (PlanKey.PRO_MONTHLY, settings.stripe_price_pro_monthly),
                (PlanKey.PRO_YEARLY, settings.stripe_price_pro_yearly),
            )
            if price
        },
        retained_prices(settings),
    )
    provider: BillingProvider | None = pick(overrides, "billing_provider", lambda: _provider(core, settings, catalog))
    webhooks: BillingWebhookVerifier | None = pick(
        overrides,
        "billing_webhook_verifier",
        lambda: (
            StripeWebhookVerifier(settings.stripe_webhook_secret.get_secret_value())
            if settings.stripe_webhook_secret
            else None
        ),
    )
    service = BillingService(
        tx=kernel.transactions(PgBillingRepository),
        provider=provider,
        webhook_verifier=webhooks,
        catalog=catalog,
        urls=BillingUrls(core.app_base_url.rstrip("/")),
        clock=kernel.clock,
        analytics=kernel.analytics,
        rate_limiter=kernel.rate_limiter,
        checkouts_per_hour=settings.checkouts_per_user_per_hour,
        # Synchronization holds a worker connection across Stripe calls; leave
        # at least one worker connection for everything else (the pool has two or more).
        sync_slots=core.database_worker_pool_max_size - 1,
        sync_wait_seconds=core.database_connect_timeout_seconds,
    )

    kernel.handlers.subscribe(EntitlementChanged, "billing.analytics", service.capture_entitlement_change)
    kernel.jobs.register(
        Job(
            "reconcile-billing",
            "Compare billing records with Stripe; apply fixes only if BILLING_RECONCILE_APPLY=true.",
            "0 4 * * *",
            lambda: service.reconcile_job(settings.billing_reconcile_apply, settings.billing_reconcile_batch_size),
        )
    )
    contribution.routers += [
        build_read_router(service, kernel.clock, auth, kernel.request_transaction),
        build_action_router(service, auth),
        build_webhook_router(service),
    ]
    contribution.probes.append(BillingProbe(describe(settings), provider, catalog))
    return BillingModule(service, catalog, contribution)


def _provider(core: CoreSettings, settings: BillingSettings, catalog: PriceCatalog) -> BillingProvider | None:
    if settings.billing_mode is Mode.MOCK:
        if core.app_env is AppEnv.PRODUCTION:  # pragma: no cover - rejected by Settings
            return None
        return InMemoryBillingProvider(core.app_base_url)
    if settings.stripe_secret_key is None:  # pragma: no cover - validated in Settings
        return None
    return StripeBillingProvider(secret_key=settings.stripe_secret_key.get_secret_value(), catalog=catalog)


def describe(settings: BillingSettings) -> IntegrationInfo:
    configured = (("monthly", settings.stripe_price_pro_monthly), ("yearly", settings.stripe_price_pro_yearly))
    prices = [label for label, value in configured if value]
    return IntegrationInfo(
        IntegrationKey.BILLING,
        "Stripe",
        IntegrationMode(settings.billing_mode.value),
        (
            "Pro prices configured: " + (", ".join(prices) or "none"),
            "Webhook signing secret: " + ("configured" if settings.stripe_webhook_secret else "missing"),
        ),
    )
