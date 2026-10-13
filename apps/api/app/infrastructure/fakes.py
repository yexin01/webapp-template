"""Deterministic in-memory adapters for ``mock`` integration mode and tests.

These never contact external services. Results produced with them are
"mocked" evidence and never prove live connectivity (TEST-04).
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from app.domain.entitlements import SubscriptionSnapshot
from app.domain.errors import AuthenticationError, DependencyUnavailableError
from app.domain.tenancy import ExternalMembership, VerifiedIdentity


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class InMemoryBillingProvider:
    def __init__(self, app_base_url: str = "http://localhost:3000") -> None:
        self._base = app_base_url
        self._lock = threading.Lock()
        self._customers: dict[str, UUID] = {}
        self._subscriptions: dict[str, list[SubscriptionSnapshot]] = {}
        self.calls: list[str] = []
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
    ) -> str:
        self._maybe_fail("create_checkout_session")
        return f"{self._base}/billing/success?session_id=cs_mock"

    def create_portal_session(self, *, customer_id: str, return_url: str) -> str:
        self._maybe_fail("create_portal_session")
        return return_url

    def list_customer_subscriptions(self, customer_id: str) -> list[SubscriptionSnapshot]:
        self._maybe_fail("list_customer_subscriptions")
        with self._lock:
            return list(self._subscriptions.get(customer_id, []))

    def get_customer_app_user_id(self, customer_id: str) -> UUID | None:
        self._maybe_fail("get_customer_app_user_id")
        with self._lock:
            return self._customers.get(customer_id)

    # Test helpers -----------------------------------------------------------

    def register_customer(self, customer_id: str, user_id: UUID) -> None:
        with self._lock:
            self._customers[customer_id] = user_id

    def set_subscriptions(self, customer_id: str, subs: list[SubscriptionSnapshot]) -> None:
        with self._lock:
            self._subscriptions[customer_id] = list(subs)


class StaticMembershipDirectory:
    """Membership directory backed by a dictionary (tests and mock mode)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._memberships: dict[str, list[ExternalMembership]] = {}
        self.available = True
        self.calls = 0

    def list_user_memberships(self, external_user_id: str) -> list[ExternalMembership]:
        with self._lock:
            self.calls += 1
            if not self.available:
                raise DependencyUnavailableError("Organization directory is unavailable.")
            return list(self._memberships.get(external_user_id, []))

    def set(self, external_user_id: str, memberships: list[ExternalMembership]) -> None:
        with self._lock:
            self._memberships[external_user_id] = list(memberships)


class RejectingVerifier:
    """Used when authentication is not configured: every token is rejected."""

    def verify(self, token: str) -> VerifiedIdentity:
        raise AuthenticationError()
