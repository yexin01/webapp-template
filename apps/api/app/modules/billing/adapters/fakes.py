"""In-process Stripe stand-in for mock mode and tests. Never selected in production."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from app.kernel.errors import InvalidRequestError
from app.modules.billing.domain import PortalConfiguration, SubscriptionSnapshot
from app.modules.billing.ports import CheckoutSession, PriceDescription


@dataclass
class _Session:
    session_id: str
    user_id: UUID
    customer_id: str
    url: str
    expires_at: datetime
    status: str = "open"


class InMemoryBillingProvider:
    def __init__(self, app_base_url: str = "http://localhost:3000") -> None:
        self._base = app_base_url
        self._lock = threading.Lock()
        self._customers: dict[str, UUID] = {}
        self._subscriptions: dict[str, list[SubscriptionSnapshot]] = {}
        self._prices: dict[str, PriceDescription] = {}
        self._deleted_customers: set[str] = set()
        self._portal: PortalConfiguration | Exception | None = None
        self._sessions: dict[str, _Session] = {}
        self._session_by_key: dict[str, str] = {}
        # When the sessions created from now on expire.
        self.session_expires_at = datetime(2100, 1, 1, tzinfo=UTC)
        self.calls: list[str] = []
        # The arguments of each session created, so tests can check what Stripe would be asked for.
        self.checkout_requests: list[dict[str, object]] = []
        self.portal_requests: list[dict[str, object]] = []
        self.fail_with: Callable[[], Exception] | None = None

    def _maybe_fail(self, call: str) -> None:
        with self._lock:
            self.calls.append(call)
        if self.fail_with is not None:
            raise self.fail_with()

    def create_customer(self, user_id: UUID) -> str:
        self._maybe_fail("create_customer")
        customer_id = f"cus_mock{user_id.hex[:12]}"
        with self._lock:
            self._customers[customer_id] = user_id
        return customer_id

    def create_checkout_session(
        self,
        *,
        customer_id: str,
        price_id: str,
        user_id: UUID,
        success_url: str,
        cancel_url: str,
        idempotency_key: str,
    ) -> CheckoutSession:
        self._maybe_fail("create_checkout_session")
        with self._lock:
            self.checkout_requests.append(
                {
                    "customer_id": customer_id,
                    "price_id": price_id,
                    "user_id": user_id,
                    "success_url": success_url,
                    "cancel_url": cancel_url,
                    "idempotency_key": idempotency_key,
                }
            )
            # Like Stripe: the same key returns the session it created, whatever has happened to it since.
            known = self._session_by_key.get(idempotency_key)
            session = self._sessions[known] if known else None
            if session is None:
                session_id = f"cs_mock_{len(self._sessions) + 1:04d}"
                session = _Session(
                    session_id,
                    user_id,
                    customer_id,
                    f"{self._base}/billing/success?session_id={session_id}",
                    self.session_expires_at,
                )
                self._sessions[session_id] = session
                self._session_by_key[idempotency_key] = session_id
            return CheckoutSession(session.session_id, session.url, session.expires_at)

    def expire_checkout_session(self, session_id: str) -> None:
        self._maybe_fail("expire_checkout_session")
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None or session.status != "open":
                raise InvalidRequestError("The billing request was rejected.")
            session.status = "expired"

    def open_checkout_session_url(self, session_id: str) -> str | None:
        self._maybe_fail("open_checkout_session_url")
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise InvalidRequestError("The billing request was rejected.")
            return session.url if session.status == "open" else None

    def create_portal_session(self, *, customer_id: str, return_url: str) -> str:
        self._maybe_fail("create_portal_session")
        with self._lock:
            self.portal_requests.append({"customer_id": customer_id, "return_url": return_url})
        return return_url

    def list_customer_subscriptions(self, customer_id: str) -> list[SubscriptionSnapshot]:
        self._maybe_fail("list_customer_subscriptions")
        with self._lock:
            if customer_id in self._deleted_customers:
                raise InvalidRequestError("The billing request was rejected.")
            return list(self._subscriptions.get(customer_id, []))

    def retrieve_subscription(self, subscription_id: str) -> SubscriptionSnapshot:
        self._maybe_fail("retrieve_subscription")
        with self._lock:
            for subs in self._subscriptions.values():
                for sub in subs:
                    if sub.stripe_subscription_id == subscription_id:
                        return sub
        raise InvalidRequestError("The billing request was rejected.")

    def get_customer_app_user_id(self, customer_id: str) -> UUID | None:
        self._maybe_fail("get_customer_app_user_id")
        with self._lock:
            return self._customers.get(customer_id)

    def describe_price(self, price_id: str) -> PriceDescription:
        self._maybe_fail("describe_price")
        with self._lock:
            price = self._prices.get(price_id)
        if price is None:
            raise InvalidRequestError("The billing request was rejected.")
        return price

    def describe_portal(self) -> PortalConfiguration | None:
        self._maybe_fail("describe_portal")
        with self._lock:
            portal = self._portal
        if isinstance(portal, Exception):
            raise portal
        return portal

    # Test helpers -----------------------------------------------------------

    def open_sessions(self, user_id: UUID | None = None) -> list[str]:
        """The IDs of the Checkout Sessions a user could still complete."""
        with self._lock:
            return [
                s.session_id
                for s in self._sessions.values()
                if s.status == "open" and (user_id is None or s.user_id == user_id)
            ]

    def complete_session(self, session_id: str, subscription: SubscriptionSnapshot) -> None:
        """The user paid in a session: it is complete, and Stripe holds the subscription it created."""
        with self._lock:
            session = self._sessions[session_id]
            session.status = "complete"
            self._subscriptions.setdefault(session.customer_id, []).append(subscription)

    def set_portal(self, portal: PortalConfiguration | Exception | None) -> None:
        with self._lock:
            self._portal = portal

    def set_price(self, price: PriceDescription) -> None:
        with self._lock:
            self._prices[price.price_id] = price

    def register_customer(self, customer_id: str, user_id: UUID) -> None:
        with self._lock:
            self._customers[customer_id] = user_id

    def set_subscriptions(self, customer_id: str, subs: list[SubscriptionSnapshot]) -> None:
        with self._lock:
            self._subscriptions[customer_id] = list(subs)

    def delete_customer(self, customer_id: str) -> None:
        """Like Stripe: the customer is gone, and its subscriptions can still be read one by one."""
        with self._lock:
            self._deleted_customers.add(customer_id)
