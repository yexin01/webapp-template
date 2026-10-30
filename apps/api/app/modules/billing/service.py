"""Individual Free/Pro billing (spec §9).

Layering: Stripe state -> synchronized records -> entitlement decision -> UI.
Webhooks are treated as *triggers*: the handler re-reads the customer's
current subscriptions from Stripe instead of applying event payloads, so
duplicate, delayed, and out-of-order deliveries converge on the same state.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from app.kernel.application.rate_limit import RateLimiter
from app.kernel.authorization import authorize
from app.kernel.errors import (
    ConflictError,
    DependencyUnavailableError,
    EntitlementRequiredError,
    InvalidRequestError,
    NotConfiguredError,
)
from app.kernel.events import StoredEvent
from app.kernel.jobs import TIME_BUDGET_SECONDS, JobSummary
from app.kernel.ports import Analytics, Clock, Transactions, Tx
from app.kernel.tenancy import TenantContext
from app.kernel.webhooks import EventStatus
from app.modules.billing.domain import (
    BILLING_MANAGE,
    FINAL_STATUSES,
    PRO_FEATURE_USE,
    BillingSummary,
    EntitlementChanged,
    EntitlementDecision,
    Plan,
    PlanKey,
    PriceCatalog,
    SubscriptionSnapshot,
    SubscriptionStatus,
    decide,
    entitling_subscriptions,
    summarize,
)
from app.modules.billing.ports import (
    BillingProvider,
    BillingRepository,
    BillingWebhookEvent,
    BillingWebhookVerifier,
)

logger = logging.getLogger(__name__)

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
        # The page re-reads Stripe once when it sees ``from=portal``; the parameter grants nothing.
        return f"{self.app_base_url}/billing?from=portal"


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
    # Stripe customers with flagged webhook events that no user could be linked to.
    unlinked_customers: list[str] = field(default_factory=list)
    # Customers whose check Stripe rejected (for example, deleted customers).
    rejected_customers: list[str] = field(default_factory=list)
    applied: bool = False


class BillingService:
    def __init__(
        self,
        *,
        tx: Transactions[BillingRepository],
        provider: BillingProvider | None,
        webhook_verifier: BillingWebhookVerifier | None,
        catalog: PriceCatalog,
        urls: BillingUrls,
        clock: Clock,
        analytics: Analytics,
        rate_limiter: RateLimiter,
        checkouts_per_hour: int,
        portal_sessions_per_hour: int,
        syncs_per_hour: int,
        max_concurrent_stripe_calls: int,
        sync_slots: int = 1,
        sync_wait_seconds: float = 5.0,
        reconcile_time_budget_seconds: float = TIME_BUDGET_SECONDS,
    ) -> None:
        self._tx = tx
        self._provider = provider
        self._verifier = webhook_verifier
        self._catalog = catalog
        self._urls = urls
        self._clock = clock
        self._analytics = analytics
        self._rate_limiter = rate_limiter
        self._checkouts_per_hour = checkouts_per_hour
        self._portal_sessions_per_hour = portal_sessions_per_hour
        self._syncs_per_hour = syncs_per_hour
        self._stripe_calls = threading.BoundedSemaphore(max_concurrent_stripe_calls)
        self.sync_slots = sync_slots
        self.sync_wait_seconds = sync_wait_seconds
        self._sync_slots = _Slots(sync_slots, sync_wait_seconds)
        self._reconcile_time_budget = reconcile_time_budget_seconds

    # -- Reads ---------------------------------------------------------------

    def has_price(self, key: PlanKey) -> bool:
        return self._catalog.price_for(key) is not None

    def summary(self, ctx: TenantContext) -> BillingSummary:
        authorize(ctx, BILLING_MANAGE)
        with self._tx.as_tenant(ctx) as t:
            subs = t.repo.list_subscriptions(ctx.user_id)
        return summarize(subs, self._clock.now())

    def entitlement(self, ctx: TenantContext) -> EntitlementDecision:
        with self._tx.as_tenant(ctx) as t:
            subs = t.repo.list_subscriptions(ctx.user_id)
        return decide(subs, self._clock.now())

    def require_pro(self, ctx: TenantContext) -> None:
        authorize(ctx, PRO_FEATURE_USE)
        if self.entitlement(ctx).plan is not Plan.PRO:
            raise EntitlementRequiredError()

    # -- Checkout and portal -------------------------------------------------

    def start_checkout(self, ctx: TenantContext, plan_key: PlanKey, idempotency_key: str) -> str:
        authorize(ctx, BILLING_MANAGE)
        if not _IDEMPOTENCY_KEY.fullmatch(idempotency_key):
            raise InvalidRequestError("Idempotency-Key must be 8-64 characters [A-Za-z0-9_-].")
        provider = self._require_provider()
        price_id = self._catalog.price_for(plan_key)
        if price_id is None:
            raise NotConfiguredError("This plan is not available.")
        self._rate_limiter.hit(f"checkout:{ctx.user_id}", self._checkouts_per_hour, 3600)

        with self._tx.as_tenant(ctx) as t:
            subs = t.repo.list_subscriptions(ctx.user_id)
            customer_id = t.repo.get_billing_customer_id(ctx.user_id)
        if decide(subs, self._clock.now()).plan is Plan.PRO:
            raise ConflictError("You already have Pro. Manage your plan from the billing page.")

        with self._stripe_call_slot():
            if customer_id is None:
                created = provider.create_customer(ctx.user_id)
                with self._tx.as_tenant(ctx) as t:
                    customer_id = t.repo.insert_billing_customer(ctx.user_id, created, self._clock.now())
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
        authorize(ctx, BILLING_MANAGE)
        provider = self._require_provider()
        with self._tx.as_tenant(ctx) as t:
            customer_id = t.repo.get_billing_customer_id(ctx.user_id)
        if customer_id is None:
            raise ConflictError("There is no billing account to manage yet.")
        self._rate_limiter.hit(f"portal:{ctx.user_id}", self._portal_sessions_per_hour, 3600)
        with self._stripe_call_slot():
            url = provider.create_portal_session(customer_id=customer_id, return_url=self._urls.portal_return)
        self._analytics.capture(ctx.user_id, "billing_portal_opened", {})
        return url

    def sync_from_stripe(self, ctx: TenantContext) -> BillingSummary:
        """Re-read the signed-in user's own customer from Stripe, as a webhook would (BILL-02, BILL-06).

        Called after checkout and after the portal, when the webhook may not
        have landed. It takes no input: entitlement still comes only from what
        this server reads from Stripe for the user's own customer, never from
        a redirect parameter. Webhooks, reconciliation, and this share one
        customer lock, so they converge on one state. Users cannot crowd out
        the other two: this counts against the per-instance bound that
        checkout and the portal share, it never takes the last synchronization
        slot (``_Slots.admit_user``), and it is limited per user. There is no
        limit shared by all users: any crowd of free accounts could fill one and
        lock everyone out, and the bound on concurrent Stripe calls already caps
        the rate this instance sends.
        """
        authorize(ctx, BILLING_MANAGE)
        self._require_provider()
        self._rate_limiter.hit(f"billing-sync:{ctx.user_id}", self._syncs_per_hour, 3600)
        with self._tx.as_tenant(ctx) as t:
            customer_id = t.repo.get_billing_customer_id(ctx.user_id)
        if customer_id is not None:
            with self._stripe_call_slot(), self._sync_slots.admit_user(), self._tx.as_system() as t:
                t.repo.lock_customer(customer_id)
                before = decide(t.repo.list_subscriptions_by_customer(customer_id), self._clock.now()).plan
                try:
                    self._sync_customer(t, customer_id)
                except InvalidRequestError:
                    # Stripe rejected the re-read (for example, the customer was deleted):
                    # the stored state stays, and reconciliation owns that case.
                    logger.warning("billing_user_sync_rejected")
                after = decide(t.repo.list_subscriptions_by_customer(customer_id), self._clock.now()).plan
            # Normal when the webhook has not landed yet. Not an error: the CLI redirects at once.
            logger.info("billing_user_sync", extra={"changed_plan": after is not before})
        return self.summary(ctx)

    @contextmanager
    def _stripe_call_slot(self) -> Iterator[None]:
        """Admit a user action's Stripe calls, or refuse at once with ``503``.

        Each admitted action holds a request thread until Stripe answers (up to
        three attempts at the client timeout), so only a few run per instance
        and a slow Stripe cannot take the threads every other route needs.
        Nothing waits for a slot: waiting would hold a thread too.
        """
        if not self._stripe_calls.acquire(blocking=False):
            raise DependencyUnavailableError("Billing is busy. Try again shortly.")
        try:
            yield
        finally:
            self._stripe_calls.release()

    # -- Webhooks and synchronization ----------------------------------------

    def handle_webhook(self, payload: bytes, signature_header: str | None) -> EventStatus:
        return self.process_webhook(self.verify_webhook(payload, signature_header))

    def verify_webhook(self, payload: bytes, signature_header: str | None) -> BillingWebhookEvent:
        """Check the signature on the raw body (BILL-08). No I/O."""
        if self._verifier is None:
            raise NotConfiguredError()
        return self._verifier.verify(payload, signature_header)

    @staticmethod
    def requires_sync(event: BillingWebhookEvent) -> bool:
        """Whether the event re-reads a customer from Stripe, holding a worker connection meanwhile."""
        return event.event_type in SYNC_EVENT_TYPES and event.customer_id is not None

    def process_webhook(self, event: BillingWebhookEvent) -> EventStatus:
        """Record a verified event and synchronize its customer when it can change subscriptions."""
        customer_id = event.customer_id if self.requires_sync(event) else None
        if customer_id is None:
            with self._tx.as_system() as t:
                status = t.repo.claim_billing_event(event)
                if status in (EventStatus.PROCESSED, EventStatus.IGNORED):
                    return status
                t.repo.finish_billing_event(event.event_id, EventStatus.IGNORED)
                return EventStatus.IGNORED

        with self._sync_slots.admit(), self._tx.as_system() as t:
            # The customer lock comes first, on every path that takes both it
            # and ledger rows (reconciliation resolves flagged rows under it),
            # so the two can never deadlock.
            t.repo.lock_customer(customer_id)
            # Inserts the ledger row or locks the existing one, so concurrent
            # duplicate deliveries are serialized (BILL-09, BILL-14).
            status = t.repo.claim_billing_event(event)
            if status in (EventStatus.PROCESSED, EventStatus.IGNORED):
                return status
            try:
                result = self._sync_customer(t, customer_id)
            except InvalidRequestError:
                # Stripe rejected the re-read (for example, the customer no
                # longer exists). A retry cannot succeed, so the event is
                # acknowledged and left for reconciliation (BILL-11).
                logger.warning("billing_sync_rejected", extra={"event_id": event.event_id})
                result = EventStatus.NEEDS_RECONCILIATION
            t.repo.finish_billing_event(event.event_id, result)
            return result

    def _sync_customer(self, t: Tx[BillingRepository], customer_id: str) -> EventStatus:
        """Re-read the customer's subscriptions from Stripe and store them. The caller holds the customer lock."""
        provider = self._require_provider()
        repo = t.repo
        user_id = repo.find_user_by_customer(customer_id)
        if user_id is None:
            user_id = self._adopt_customer(repo, provider, customer_id)
            if user_id is None:
                return EventStatus.NEEDS_RECONCILIATION
        remote = _read_remote(provider, customer_id, repo.list_subscriptions_by_customer(customer_id))
        self._store(t, user_id, customer_id, remote)
        return EventStatus.PROCESSED

    def _store(
        self, t: Tx[BillingRepository], user_id: UUID, customer_id: str, remote: Sequence[SubscriptionSnapshot]
    ) -> None:
        """Upsert Stripe's state for a locked customer, and record a plan change."""
        repo = t.repo
        now = self._clock.now()
        before = decide(repo.list_subscriptions_by_customer(customer_id), now).plan
        for sub in remote:
            repo.upsert_subscription(user_id, sub)
        stored = repo.list_subscriptions_by_customer(customer_id)
        after = decide(stored, now).plan
        if after is not before:
            t.record(EntitlementChanged(user_id=user_id, plan=after))
        _log_anomalies(customer_id, stored, now)

    def _adopt_customer(self, repo: BillingRepository, provider: BillingProvider, customer_id: str) -> UUID | None:
        """Link a customer we created whose local record was lost or not yet written.

        The user reference comes from metadata this server wrote on the
        customer and is re-read from Stripe with the secret key, never from
        the webhook payload.
        """
        claimed = provider.get_customer_app_user_id(customer_id)
        if claimed is None or not repo.user_exists(claimed):
            return None
        if repo.get_billing_customer_id(claimed) not in (None, customer_id):
            return None
        repo.link_customer(claimed, customer_id, self._clock.now())
        return claimed

    def reconcile(self, *, user_id: UUID | None, apply: bool, page_size: int = 100) -> ReconciliationReport:
        """Compare local billing records with Stripe (BILL-19). Dry-run unless ``apply``.

        Checks one user's customer, or every customer: first those with
        webhook events flagged as needing reconciliation, then every linked
        customer. The scheduled job checks a bounded batch instead
        (``reconcile_job``).
        """
        self._require_provider()
        report = ReconciliationReport(applied=apply)
        if user_id is not None:
            with self._tx.as_system() as t:
                customer_id = t.repo.get_billing_customer_id(user_id)
            if customer_id is not None:
                self._reconcile_customer(customer_id, apply, report)
            return report
        seen: set[str] = set()
        for flagged in (True, False):
            after: str | None = None
            while True:
                with self._tx.as_system() as t:
                    lister = t.repo.list_flagged_customers if flagged else t.repo.list_billing_customers
                    page = lister(page_size, after)
                for customer_id in page:
                    if customer_id not in seen:
                        seen.add(customer_id)
                        self._reconcile_customer(customer_id, apply, report)
                if len(page) < page_size:
                    break
                after = page[-1]
        return report

    def reconcile_job(self, apply: bool, batch_size: int) -> JobSummary:
        """Job ``reconcile-billing``: a bounded batch per run (ARCH-16).

        Customers with the most recently flagged webhook events take up to
        half the batch and half the time budget. The rest go to the rotation:
        the linked customers checked least recently, where a new customer
        starts at the back, as if checked when it was linked, and a flagged
        customer the first half did not reach waits its turn like any other.
        So successive runs check every linked customer, even while some flags
        cannot be resolved automatically and new customers keep arriving. A
        customer Stripe rejects still counts as checked, so it moves to the
        back of the rotation. The run stops starting new checks once its time
        budget is spent.
        """
        self._require_provider()
        report = ReconciliationReport(applied=apply)
        with self._tx.as_system() as t:
            flagged = t.repo.list_recently_flagged_customers(batch_size // 2)
            least_recent = t.repo.list_least_recently_reconciled(batch_size)
        started = time.monotonic()
        checked = self._reconcile_until(flagged, started + self._reconcile_time_budget / 2, apply, report)
        rotation = [c for c in least_recent if c not in checked][: batch_size - len(checked)]
        self._reconcile_until(rotation, started + self._reconcile_time_budget, apply, report)
        # The summary holds counts only; these name what an operator has to look at.
        for d in report.divergences:
            logger.warning(
                "billing_divergence",
                extra={
                    "stripe_subscription_id": d.stripe_subscription_id,
                    "kind": d.kind,
                    "local_status": d.local_status,
                    "remote_status": d.remote_status,
                },
            )
        for customer_id in report.rejected_customers:
            logger.warning("billing_reconcile_rejected", extra={"stripe_customer_id": customer_id})
        return {
            "customers": report.customers_checked,
            "divergences": len(report.divergences),
            "unlinked": len(report.unlinked_customers),
            "rejected": len(report.rejected_customers),
            "applied": apply,
        }

    def _reconcile_until(
        self, customers: Sequence[str], deadline: float, apply: bool, report: ReconciliationReport
    ) -> set[str]:
        """Check customers in order until ``deadline``. Returns those checked."""
        checked: set[str] = set()
        for customer_id in customers:
            if time.monotonic() >= deadline:
                break
            self._reconcile_customer(customer_id, apply, report)
            checked.add(customer_id)
        return checked

    def _reconcile_customer(self, customer_id: str, apply: bool, report: ReconciliationReport) -> None:
        """Check one customer. A Stripe outage stops the run; a rejection for one customer does not."""
        report.customers_checked += 1
        try:
            self._check_customer(customer_id, apply, report)
        except InvalidRequestError:
            report.rejected_customers.append(customer_id)
            # Recorded as checked, or the customer would head every scheduled batch.
            with self._tx.as_system() as t:
                t.repo.mark_reconciled(customer_id, self._clock.now())

    def _check_customer(self, customer_id: str, apply: bool, report: ReconciliationReport) -> None:
        provider = self._require_provider()
        if apply:
            # Stripe is read under the customer lock, as webhook synchronization
            # does, so a concurrent webhook can never be overwritten by an older
            # read (BILL-10).
            with self._sync_slots.admit(), self._tx.as_system() as t:
                t.repo.lock_customer(customer_id)
                user_id = t.repo.find_user_by_customer(customer_id) or self._adopt_customer(
                    t.repo, provider, customer_id
                )
                if user_id is None:
                    report.unlinked_customers.append(customer_id)
                    return
                local = t.repo.list_subscriptions_by_customer(customer_id)
                remote = _read_remote(provider, customer_id, local)
                diffs = _diff(user_id, _by_id(local), _by_id(remote))
                if diffs:
                    self._store(t, user_id, customer_id, remote)
                t.repo.resolve_flagged_events(customer_id)
                t.repo.mark_reconciled(customer_id, self._clock.now())
            report.divergences.extend(diffs)
            return

        with self._tx.as_system() as t:
            user_id = t.repo.find_user_by_customer(customer_id)
            known = t.repo.list_subscriptions_by_customer(customer_id)
        if user_id is None:
            # Linking a customer is a write, so a dry run only reports it.
            report.unlinked_customers.append(customer_id)
            return
        remote = _read_remote(provider, customer_id, known)
        with self._tx.as_system() as t:
            local = t.repo.list_subscriptions_by_customer(customer_id)
            t.repo.mark_reconciled(customer_id, self._clock.now())
        report.divergences.extend(_diff(user_id, _by_id(local), _by_id(remote)))

    def capture_entitlement_change(self, event: StoredEvent) -> None:
        """Outbox handler: product analytics for plan changes.

        A redelivery sends the same PostHog event (the outbox event's ID and
        time), which PostHog deduplicates. Raises unless the export is
        confirmed, so the outbox retries it instead of marking it processed.
        """
        self._analytics.capture(
            UUID(str(event.payload["user_id"])),
            "plan_changed",
            {"plan": str(event.payload["plan"])},
            event_id=event.id,
            occurred_at=event.occurred_at,
        )
        if not self._analytics.flush():
            raise DependencyUnavailableError("Analytics export could not be confirmed.")

    def _require_provider(self) -> BillingProvider:
        if self._provider is None:
            raise NotConfiguredError("Billing is not configured in this environment.")
        return self._provider


class _Slots:
    """Admission for synchronizations: each holds a worker connection across Stripe calls (BILL-10).

    Fewer slots than worker connections exist, so a burst of webhooks never
    takes the connections that readiness, rate limits, and membership
    refreshes need. Webhook deliveries queue for a slot in the event loop
    first (``routes.build_webhook_router``), where waiting holds no thread;
    here they contend with reconciliation. A caller waits briefly. A
    waiting caller holds a threadpool thread (40 by default, shared by every
    synchronous route), so only ``max_waiting`` callers wait; anyone else gets
    ``503`` at once, and Stripe retries the delivery.

    The re-reads signed-in users ask for (``admit_user``) never wait and never
    take the last slot: at most one slot fewer than exist is held by users, so
    webhooks and reconciliation always have one. With two worker connections
    there is one slot, and user re-reads are always refused.
    """

    def __init__(self, slots: int, wait_seconds: float, max_waiting: int = 8) -> None:
        self._slots = threading.BoundedSemaphore(slots)
        self._users = threading.BoundedSemaphore(max(slots - 1, 0))
        self._max_waiting = max_waiting
        self._waiting = 0
        self._lock = threading.Lock()
        self._wait_seconds = wait_seconds

    @contextmanager
    def admit_user(self) -> Iterator[None]:
        """A slot now or ``503`` at once, and never the last one: a user can simply ask again."""
        if not self._users.acquire(blocking=False):
            raise _busy()
        try:
            if not self._slots.acquire(blocking=False):
                raise _busy()
            try:
                yield
            finally:
                self._slots.release()
        finally:
            self._users.release()

    @contextmanager
    def admit(self) -> Iterator[None]:
        if not self._slots.acquire(blocking=False):
            with self._lock:
                if self._waiting >= self._max_waiting:
                    raise _busy()
                self._waiting += 1
            try:
                admitted = self._slots.acquire(timeout=self._wait_seconds)
            finally:
                with self._lock:
                    self._waiting -= 1
            if not admitted:
                raise _busy()
        try:
            yield
        finally:
            self._slots.release()


def _busy() -> DependencyUnavailableError:
    return DependencyUnavailableError("Billing synchronization is busy. Retry shortly.")


def _read_remote(
    provider: BillingProvider, customer_id: str, local: Sequence[SubscriptionSnapshot]
) -> list[SubscriptionSnapshot]:
    """Stripe's state of the customer's subscriptions, including stored ones the listing no longer returns.

    Deleting a customer in Stripe cancels its subscriptions, and the customer
    can no longer be listed. Every subscription stored as not yet final is
    then read by its ID, so its end is recorded instead of Pro lasting
    forever. Only what Stripe returns is applied: a subscription Stripe does
    not return (for example, because the key belongs to another account)
    rejects the read, and nothing changes.
    """
    rejected: InvalidRequestError | None = None
    try:
        remote = provider.list_customer_subscriptions(customer_id)
    except InvalidRequestError as exc:
        rejected, remote = exc, []
    listed = {s.stripe_subscription_id for s in remote}
    unlisted = [
        s.stripe_subscription_id
        for s in local
        if s.stripe_subscription_id not in listed and s.status not in FINAL_STATUSES
    ]
    if rejected is not None and not unlisted:
        raise rejected
    return [*remote, *(provider.retrieve_subscription(sub_id) for sub_id in unlisted)]


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


def _by_id(subs: Sequence[SubscriptionSnapshot]) -> dict[str, SubscriptionSnapshot]:
    return {s.stripe_subscription_id: s for s in subs}


def _log_anomalies(customer_id: str, subs: Sequence[SubscriptionSnapshot], now: datetime) -> None:
    """Log states an operator must look at. They never change the entitlement decision."""
    for sub in subs:
        if sub.status is SubscriptionStatus.TRIALING:
            # Trials are out of scope (DEC-010, spec §9.5): not entitled, and logged.
            logger.warning("billing_trialing_anomaly", extra={"stripe_subscription_id": sub.stripe_subscription_id})
        if sub.plan_key is PlanKey.UNKNOWN and sub.status not in FINAL_STATUSES:
            # A price outside the catalog never grants Pro: for example, a replaced
            # price that is not listed in STRIPE_PRICE_PRO_*_RETAINED. While the
            # subscription is paid, a user pays without Pro, so it is an error.
            paid = sub.status in (SubscriptionStatus.ACTIVE, SubscriptionStatus.PAST_DUE)
            logger.log(
                logging.ERROR if paid else logging.WARNING,
                "billing_unknown_price",
                extra={"stripe_subscription_id": sub.stripe_subscription_id, "price_id": sub.price_id},
            )
    if len(entitling_subscriptions(subs, now)) > 1:
        # One user paying for more than one Pro subscription (for example, two
        # checkouts completed in parallel). Resolve it in Stripe and refund.
        logger.error("billing_multiple_entitling_subscriptions", extra={"stripe_customer_id": customer_id})
