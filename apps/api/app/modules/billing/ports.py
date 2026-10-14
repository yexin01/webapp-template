"""Billing ports: synchronized billing records, the Stripe API, and webhook verification."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.kernel.webhooks import EventStatus
from app.modules.billing.domain import SubscriptionSnapshot


@dataclass(frozen=True, slots=True)
class BillingWebhookEvent:
    event_id: str
    event_type: str
    created_at: datetime
    customer_id: str | None


@dataclass(frozen=True, slots=True)
class PriceDescription:
    """Non-secret facts about a billing price, used by configuration checks."""

    price_id: str
    active: bool
    recurring_interval: str | None
    currency: str | None
    livemode: bool


class BillingRepository(Protocol):
    """Customers, subscriptions, and the Stripe webhook ledger.

    Request transactions read only the caller's records and may create the
    caller's customer link. Synchronization and the ledger run in system
    transactions.
    """

    def get_billing_customer_id(self, user_id: UUID) -> str | None: ...
    def insert_billing_customer(self, user_id: UUID, stripe_customer_id: str) -> str: ...
    def list_subscriptions(self, user_id: UUID) -> list[SubscriptionSnapshot]: ...

    def user_exists(self, user_id: UUID) -> bool: ...
    def lock_customer(self, stripe_customer_id: str) -> None: ...
    def find_user_by_customer(self, stripe_customer_id: str) -> UUID | None: ...
    def link_customer(self, user_id: UUID, stripe_customer_id: str) -> None: ...
    def upsert_subscription(self, user_id: UUID, sub: SubscriptionSnapshot) -> None: ...
    def list_subscriptions_by_customer(self, stripe_customer_id: str) -> list[SubscriptionSnapshot]: ...
    def list_billing_customers(self, limit: int, after_user_id: UUID | None) -> list[tuple[UUID, str]]: ...

    def claim_billing_event(self, event: BillingWebhookEvent) -> EventStatus: ...
    def finish_billing_event(self, event_id: str, status: EventStatus) -> None: ...


class BillingProvider(Protocol):
    def create_customer(self, user_id: UUID) -> str: ...
    def create_checkout_session(
        self,
        *,
        customer_id: str,
        price_id: str,
        user_id: UUID,
        success_url: str,
        cancel_url: str,
        idempotency_key: str,
    ) -> str: ...
    def create_portal_session(self, *, customer_id: str, return_url: str) -> str: ...
    def list_customer_subscriptions(self, customer_id: str) -> list[SubscriptionSnapshot]: ...
    def get_customer_app_user_id(self, customer_id: str) -> UUID | None: ...
    def describe_price(self, price_id: str) -> PriceDescription: ...


class BillingWebhookVerifier(Protocol):
    def verify(self, payload: bytes, signature_header: str | None) -> BillingWebhookEvent: ...
