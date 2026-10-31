"""Billing ports: synchronized billing records, the Stripe API, and webhook verification."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.kernel.webhooks import EventStatus
from app.modules.billing.domain import PortalConfiguration, SubscriptionSnapshot


@dataclass(frozen=True, slots=True)
class BillingWebhookEvent:
    event_id: str
    event_type: str
    created_at: datetime
    customer_id: str | None


@dataclass(frozen=True, slots=True)
class CheckoutSession:
    """A hosted Checkout Session: where to send the user, and the ID to expire it by."""

    session_id: str
    url: str
    expires_at: datetime | None


@dataclass(frozen=True, slots=True)
class OpenCheckout:
    """The Checkout Session a user may still complete (DEC-013): at most one per user."""

    stripe_session_id: str
    idempotency_key: str
    expires_at: datetime | None


@dataclass(frozen=True, slots=True)
class PriceDescription:
    """Non-secret facts about a billing price, used by configuration checks."""

    price_id: str
    active: bool
    recurring_interval: str | None
    currency: str | None
    livemode: bool
    product_id: str | None


class BillingRepository(Protocol):
    """Customers, subscriptions, and the Stripe webhook ledger.

    Request transactions read only the caller's records and may create the
    caller's customer link. Synchronization and the ledger run in system
    transactions.

    A customer's place in the reconciliation rotation is when it was last
    checked; a new link starts there at ``linked_at``, at the back.
    """

    def get_billing_customer_id(self, user_id: UUID) -> str | None: ...
    def insert_billing_customer(self, user_id: UUID, stripe_customer_id: str, linked_at: datetime) -> str: ...
    def list_subscriptions(self, user_id: UUID) -> list[SubscriptionSnapshot]: ...

    def get_open_checkout(self, user_id: UUID) -> OpenCheckout | None: ...
    def save_open_checkout(self, user_id: UUID, checkout: OpenCheckout) -> str | None:
        """Remember the user's open session. Returns the session ID it replaced, if any."""
        ...

    def delete_open_checkout(self, user_id: UUID, stripe_session_id: str) -> None: ...

    def user_exists(self, user_id: UUID) -> bool: ...
    def lock_customer(self, stripe_customer_id: str) -> None: ...
    def find_user_by_customer(self, stripe_customer_id: str) -> UUID | None: ...
    def link_customer(self, user_id: UUID, stripe_customer_id: str, linked_at: datetime) -> None: ...
    def relink_customer(self, user_id: UUID, old_customer_id: str, new_customer_id: str, linked_at: datetime) -> str:
        """Point the user's link at a new customer, if it still names the old one. Returns the customer linked now."""
        ...

    def upsert_subscription(self, user_id: UUID, sub: SubscriptionSnapshot) -> None: ...
    def list_subscriptions_by_customer(self, stripe_customer_id: str) -> list[SubscriptionSnapshot]: ...
    def list_billing_customers(self, limit: int, after_customer_id: str | None) -> list[str]: ...
    def list_least_recently_reconciled(self, limit: int) -> list[str]: ...
    def mark_reconciled(self, stripe_customer_id: str, at: datetime) -> None: ...
    def list_flagged_customers(self, limit: int, after_customer_id: str | None) -> list[str]: ...
    def list_recently_flagged_customers(self, limit: int) -> list[str]: ...
    def resolve_flagged_events(self, stripe_customer_id: str) -> int: ...

    def claim_billing_event(self, event: BillingWebhookEvent) -> EventStatus: ...
    def finish_billing_event(self, event_id: str, status: EventStatus) -> None: ...


class BillingProvider(Protocol):
    def create_customer(self, user_id: UUID, *, replaces: str | None = None) -> str:
        """Create the user's customer. ``replaces`` names the deleted customer a new one takes the place of."""
        ...

    def customer_exists(self, customer_id: str) -> bool:
        """Whether Stripe still has the customer. A deleted customer, or one it never had, does not exist."""
        ...

    def create_checkout_session(
        self,
        *,
        customer_id: str,
        price_id: str,
        user_id: UUID,
        success_url: str,
        cancel_url: str,
        idempotency_key: str,
    ) -> CheckoutSession: ...
    def expire_checkout_session(self, session_id: str) -> None:
        """Close a session so it can no longer be completed. ``InvalidRequestError`` if it is not open (any more)."""
        ...

    def expire_open_checkout_sessions(self, customer_id: str) -> None:
        """Close every session of the customer that can still be completed, whoever opened it.

        ``InvalidRequestError`` if Stripe rejects the customer (for example, because it was deleted).
        """
        ...

    def open_checkout_session_url(self, session_id: str) -> str | None:
        """The URL of the session, or ``None`` if it is not open (any more)."""
        ...

    def create_portal_session(self, *, customer_id: str, return_url: str) -> str: ...
    def list_customer_subscriptions(self, customer_id: str) -> list[SubscriptionSnapshot]: ...
    def retrieve_subscription(self, subscription_id: str) -> SubscriptionSnapshot: ...
    def cancel_subscription_at_period_end(self, subscription_id: str) -> SubscriptionSnapshot:
        """Stop the subscription renewing; it runs to the end of the period it paid for."""
        ...

    def cancel_subscription_immediately(self, subscription_id: str) -> SubscriptionSnapshot:
        """End the subscription now, with no final invoice. Only for one nothing was paid for (``incomplete``)."""
        ...

    def get_customer_app_user_id(self, customer_id: str) -> UUID | None: ...
    def describe_price(self, price_id: str) -> PriceDescription: ...
    def describe_portal(self) -> PortalConfiguration | None:
        """The default Customer Portal configuration, read only. ``None`` if none is saved in this mode."""
        ...

    def check_subscriptions_readable(self) -> None:
        """Read one subscription listing. ``ForbiddenError`` if the key may not read subscriptions."""
        ...


class BillingWebhookVerifier(Protocol):
    def verify(self, payload: bytes, signature_header: str | None) -> BillingWebhookEvent: ...
