"""Individual Free/Pro billing (spec §9).

Layering: Stripe state -> synchronized records -> entitlement decision -> UI.
Webhooks are treated as *triggers*: the handler re-reads the customer's
current subscriptions from Stripe instead of applying event payloads, so
duplicate, delayed, and out-of-order deliveries converge on the same state.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from uuid import UUID

from app.application.rate_limit import RateLimiter
from app.domain.authorization import Action, authorize
from app.domain.catalog import PriceCatalog
from app.domain.entitlements import (
    BillingSummary,
    EntitlementDecision,
    Plan,
    PlanKey,
    SubscriptionSnapshot,
    decide,
    summarize,
)
from app.domain.errors import (
    ConflictError,
    EntitlementRequiredError,
    InvalidRequestError,
    NotConfiguredError,
)
from app.domain.tenancy import TenantContext
from app.ports import (
    Analytics,
    BillingProvider,
    BillingWebhookVerifier,
    Clock,
    EventStatus,
    SystemRepository,
    UnitOfWork,
)

_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

# Events that may change a customer's subscriptions. Everything else is
# acknowledged and recorded as ignored (BILL-12).
SYNC_EVENT_TYPES = frozenset(
    {
        "checkout.session.completed",
        "checkout.session.async_payment_succeeded",
        "checkout.session.async_payment_failed",
        "checkout.session.expired",
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
        "customer.subscription.paused",
        "customer.subscription.resumed",
        "customer.subscription.pending_update_applied",
        "customer.subscription.pending_update_expired",
        "invoice.paid",
        "invoice.payment_failed",
        "invoice.payment_action_required",
    }
)


@dataclass(frozen=True, slots=True)
class BillingUrls:
    """Redirect targets are built from configuration, never from client input (SEC-14)."""

    app_base_url: str

    @property
    def checkout_success(self) -> str:
        return f"{self.app_base_url}/billing/success?session_id={{CHECKOUT_SESSION_ID}}"

    @property
    def checkout_cancel(self) -> str:
        return f"{self.app_base_url}/billing?checkout=canceled"

    @property
    def portal_return(self) -> str:
        return f"{self.app_base_url}/billing"


@dataclass(frozen=True, slots=True)
class Divergence:
    user_id: UUID
    stripe_subscription_id: str
    kind: str
    local_status: str | None
    remote_status: str | None


@dataclass(slots=True)
class ReconciliationReport:
    customers_checked: int = 0
    divergences: list[Divergence] = field(default_factory=list)
    applied: bool = False


class BillingService:
    def __init__(
        self,
        *,
        uow: UnitOfWork,
        provider: BillingProvider | None,
        webhook_verifier: BillingWebhookVerifier | None,
        catalog: PriceCatalog,
        urls: BillingUrls,
        clock: Clock,
        analytics: Analytics,
        rate_limiter: RateLimiter,
        checkouts_per_hour: int,
    ) -> None:
        self._uow = uow
        self._provider = provider
        self._verifier = webhook_verifier
        self._catalog = catalog
        self._urls = urls
        self._clock = clock
        self._analytics = analytics
        self._rate_limiter = rate_limiter
        self._checkouts_per_hour = checkouts_per_hour

    # -- Reads ---------------------------------------------------------------

    def has_price(self, key: PlanKey) -> bool:
        return self._catalog.price_for(key) is not None

    def summary(self, ctx: TenantContext) -> BillingSummary:
        authorize(ctx, Action.BILLING_MANAGE)
        with self._uow.as_tenant(ctx) as repo:
            subs = repo.list_subscriptions(ctx.user_id)
        return summarize(subs, self._clock.now())

    def entitlement(self, ctx: TenantContext) -> EntitlementDecision:
        with self._uow.as_tenant(ctx) as repo:
            subs = repo.list_subscriptions(ctx.user_id)
        return decide(ctx.kind, subs, self._clock.now())

    def require_pro(self, ctx: TenantContext) -> None:
        authorize(ctx, Action.PRO_FEATURE_USE)
        if self.entitlement(ctx).plan is not Plan.PRO:
            raise EntitlementRequiredError()

    # -- Checkout and portal -------------------------------------------------

    def start_checkout(self, ctx: TenantContext, plan_key: PlanKey, idempotency_key: str) -> str:
        authorize(ctx, Action.BILLING_MANAGE)
        if not _IDEMPOTENCY_KEY.fullmatch(idempotency_key):
            raise InvalidRequestError("Idempotency-Key must be 8-64 characters [A-Za-z0-9_-].")
        provider = self._require_provider()
        price_id = self._catalog.price_for(plan_key)
        if price_id is None:
            raise NotConfiguredError("This plan is not available.")
        self._rate_limiter.hit(f"checkout:{ctx.user_id}", self._checkouts_per_hour, 3600)

        with self._uow.as_tenant(ctx) as repo:
            subs = repo.list_subscriptions(ctx.user_id)
            customer_id = repo.get_billing_customer_id(ctx.user_id)
        if decide(ctx.kind, subs, self._clock.now()).plan is Plan.PRO:
            raise ConflictError("You already have Pro. Manage your plan from the billing page.")

        if customer_id is None:
            created = provider.create_customer(ctx.user_id)
            with self._uow.as_tenant(ctx) as repo:
                customer_id = repo.insert_billing_customer(ctx.user_id, created)

        url = provider.create_checkout_session(
            customer_id=customer_id,
            price_id=price_id,
            user_id=ctx.user_id,
            success_url=self._urls.checkout_success,
            cancel_url=self._urls.checkout_cancel,
            idempotency_key=f"checkout-{ctx.user_id}-{plan_key.value}-{idempotency_key}",
        )
        self._analytics.capture(ctx.user_id, "checkout_started", {"plan": plan_key.value})
        return url

    def open_portal(self, ctx: TenantContext) -> str:
        authorize(ctx, Action.BILLING_MANAGE)
        provider = self._require_provider()
        with self._uow.as_tenant(ctx) as repo:
            customer_id = repo.get_billing_customer_id(ctx.user_id)
        if customer_id is None:
            raise ConflictError("There is no billing account to manage yet.")
        url = provider.create_portal_session(customer_id=customer_id, return_url=self._urls.portal_return)
        self._analytics.capture(ctx.user_id, "billing_portal_opened", {})
        return url

    # -- Webhooks and synchronization ----------------------------------------

    def handle_webhook(self, payload: bytes, signature_header: str | None) -> EventStatus:
        if self._verifier is None:
            raise NotConfiguredError()
        event = self._verifier.verify(payload, signature_header)

        with self._uow.as_system() as repo:
            # Inserts the ledger row or locks the existing one, so concurrent
            # duplicate deliveries are serialized (BILL-09, BILL-14).
            status = repo.claim_billing_event(event)
            if status in (EventStatus.PROCESSED, EventStatus.IGNORED):
                return status
            if event.event_type not in SYNC_EVENT_TYPES or event.customer_id is None:
                repo.finish_billing_event(event.event_id, EventStatus.IGNORED)
                return EventStatus.IGNORED
            result = self._sync_customer(repo, event.customer_id)
            repo.finish_billing_event(event.event_id, result)
            return result

    def _sync_customer(self, repo: SystemRepository, customer_id: str) -> EventStatus:
        provider = self._require_provider()
        repo.lock_customer(customer_id)
        user_id = repo.find_user_by_customer(customer_id)
        if user_id is None:
            user_id = self._adopt_customer(repo, provider, customer_id)
            if user_id is None:
                return EventStatus.NEEDS_RECONCILIATION
        for sub in provider.list_customer_subscriptions(customer_id):
            repo.upsert_subscription(user_id, sub)
        return EventStatus.PROCESSED

    @staticmethod
    def _adopt_customer(repo: SystemRepository, provider: BillingProvider, customer_id: str) -> UUID | None:
        """Link a customer we created whose local record was lost or not yet written.

        The user reference comes from metadata this server wrote on the
        customer and is re-read from Stripe with the secret key, never from
        the webhook payload.
        """
        claimed = provider.get_customer_app_user_id(customer_id)
        if claimed is None or not repo.user_exists(claimed):
            return None
        if repo.get_customer_for_user(claimed) not in (None, customer_id):
            return None
        repo.link_customer(claimed, customer_id)
        return claimed

    def reconcile(self, *, user_id: UUID | None, apply: bool, batch_size: int = 100) -> ReconciliationReport:
        """Compare local billing records with Stripe (BILL-19). Dry-run unless ``apply``."""
        provider = self._require_provider()
        report = ReconciliationReport(applied=apply)
        after: UUID | None = None
        while True:
            with self._uow.as_system() as repo:
                if user_id is not None:
                    customer = repo.get_customer_for_user(user_id)
                    batch = [(user_id, customer)] if customer else []
                else:
                    batch = repo.list_billing_customers(batch_size, after)
            for uid, customer_id in batch:
                report.customers_checked += 1
                remote = {s.stripe_subscription_id: s for s in provider.list_customer_subscriptions(customer_id)}
                with self._uow.as_system() as repo:
                    repo.lock_customer(customer_id)
                    local = {s.stripe_subscription_id: s for s in repo.list_subscriptions_by_customer(customer_id)}
                    diffs = _diff(uid, local, remote)
                    report.divergences.extend(diffs)
                    if apply and diffs:
                        for sub in remote.values():
                            repo.upsert_subscription(uid, sub)
            if user_id is not None or len(batch) < batch_size:
                return report
            after = batch[-1][0]

    def _require_provider(self) -> BillingProvider:
        if self._provider is None:
            raise NotConfiguredError("Billing is not configured in this environment.")
        return self._provider


def _diff(
    user_id: UUID,
    local: Mapping[str, SubscriptionSnapshot],
    remote: Mapping[str, SubscriptionSnapshot],
) -> list[Divergence]:
    diffs: list[Divergence] = []
    for sub_id, r in remote.items():
        lsub = local.get(sub_id)
        if lsub is None:
            diffs.append(Divergence(user_id, sub_id, "missing_locally", None, r.status.value))
        elif lsub != r:
            diffs.append(Divergence(user_id, sub_id, "differs", lsub.status.value, r.status.value))
    for sub_id, lsub in local.items():
        if sub_id not in remote:
            diffs.append(Divergence(user_id, sub_id, "missing_remotely", lsub.status.value, None))
    return diffs
