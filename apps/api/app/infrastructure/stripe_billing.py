"""Stripe adapters. Stripe SDK types never leave this module (ARCH-04)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import stripe

from app.domain.catalog import PriceCatalog
from app.domain.entitlements import SubscriptionSnapshot, SubscriptionStatus
from app.domain.errors import DependencyUnavailableError, InvalidRequestError, WebhookSignatureError
from app.ports import BillingWebhookEvent, PriceDescription

logger = logging.getLogger(__name__)

_METADATA_USER_KEY = "app_user_id"
_SIGNATURE_TOLERANCE_SECONDS = 300
_MAX_SUBSCRIPTIONS_PER_CUSTOMER = 100


def _ts(value: Any) -> datetime | None:
    return datetime.fromtimestamp(int(value), UTC) if value else None


def _get(obj: Any, key: str) -> Any:
    """Read a field from a StripeObject or plain dict."""
    if obj is None:
        return None
    try:
        return obj[key]
    except (KeyError, TypeError):
        return getattr(obj, key, None)


def _id(value: Any) -> str | None:
    """Stripe fields may hold an ID string or an expanded object."""
    if isinstance(value, str):
        return value
    found = _get(value, "id")
    return found if isinstance(found, str) else None


def to_snapshot(sub: Any, catalog: PriceCatalog) -> SubscriptionSnapshot:
    items = _get(_get(sub, "items"), "data") or []
    first = items[0] if items else None
    price = _get(first, "price")
    price_id = _id(price)
    # Since API version 2025-03-31, billing periods live on subscription items.
    period_end = _get(first, "current_period_end") or _get(sub, "current_period_end")
    status_raw = str(_get(sub, "status"))
    try:
        status = SubscriptionStatus(status_raw)
    except ValueError:
        logger.warning("stripe_unknown_subscription_status", extra={"status": status_raw})
        status = SubscriptionStatus.INCOMPLETE  # never entitled
    return SubscriptionSnapshot(
        stripe_subscription_id=str(_get(sub, "id")),
        stripe_customer_id=str(_id(_get(sub, "customer"))),
        status=status,
        plan_key=catalog.key_for(price_id),
        price_id=price_id,
        current_period_end=_ts(period_end),
        cancel_at_period_end=bool(_get(sub, "cancel_at_period_end")),
        cancel_at=_ts(_get(sub, "cancel_at")),
        ended_at=_ts(_get(sub, "ended_at")),
    )


class StripeBillingProvider:
    def __init__(
        self,
        *,
        secret_key: str,
        catalog: PriceCatalog,
        timeout_seconds: float = 10.0,
        api_base: str | None = None,
    ) -> None:
        self._catalog = catalog
        self._client = stripe.StripeClient(
            secret_key,
            # The SDK attaches idempotency keys to retried POSTs, so bounded
            # retries never duplicate side effects.
            max_network_retries=2,
            http_client=stripe.HTTPXClient(timeout=timeout_seconds, allow_sync_methods=True),
            # Only set for local stubs such as stripe-mock; defaults to Stripe's API.
            base_addresses={"api": api_base} if api_base else None,
        )

    def _call(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except stripe.InvalidRequestError as exc:
            logger.warning("stripe_invalid_request", extra={"code": exc.code, "http_status": exc.http_status})
            raise InvalidRequestError("The billing request was rejected.") from exc
        except stripe.StripeError as exc:
            logger.warning("stripe_error", extra={"error_type": type(exc).__name__, "http_status": exc.http_status})
            raise DependencyUnavailableError("Billing is temporarily unavailable.") from exc

    def create_customer(self, user_id: UUID) -> str:
        customer = self._call(
            self._client.v1.customers.create,
            params={"metadata": {_METADATA_USER_KEY: str(user_id)}},
            options={"idempotency_key": f"customer-create-{user_id}"},
        )
        return str(customer.id)

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
        session = self._call(
            self._client.v1.checkout.sessions.create,
            params={
                "mode": "subscription",
                "customer": customer_id,
                "client_reference_id": str(user_id),
                "line_items": [{"price": price_id, "quantity": 1}],
                "success_url": success_url,
                "cancel_url": cancel_url,
                "subscription_data": {"metadata": {_METADATA_USER_KEY: str(user_id)}},
            },
            options={"idempotency_key": idempotency_key},
        )
        if not session.url:
            raise DependencyUnavailableError("Billing is temporarily unavailable.")
        return str(session.url)

    def create_portal_session(self, *, customer_id: str, return_url: str) -> str:
        session = self._call(
            self._client.v1.billing_portal.sessions.create,
            params={"customer": customer_id, "return_url": return_url},
        )
        return str(session.url)

    def list_customer_subscriptions(self, customer_id: str) -> list[SubscriptionSnapshot]:
        result = self._call(
            self._client.v1.subscriptions.list,
            params={"customer": customer_id, "status": "all", "limit": _MAX_SUBSCRIPTIONS_PER_CUSTOMER},
        )
        return [to_snapshot(sub, self._catalog) for sub in result.data]

    def describe_price(self, price_id: str) -> PriceDescription:
        # Diagnostics only: unlike customer-facing calls, failures are specific
        # so that an operator can tell a bad key from a missing price.
        try:
            price = self._client.v1.prices.retrieve(price_id)
        except stripe.AuthenticationError as exc:
            raise DependencyUnavailableError("Stripe rejected the API key.") from exc
        except stripe.PermissionError as exc:
            raise DependencyUnavailableError("The Stripe API key cannot read prices.") from exc
        except stripe.InvalidRequestError as exc:
            raise InvalidRequestError(f"Stripe has no price {price_id} in this account and mode.") from exc
        except stripe.StripeError as exc:
            raise DependencyUnavailableError("Stripe is temporarily unavailable.") from exc
        recurring = _get(price, "recurring")
        currency = _get(price, "currency")
        return PriceDescription(
            price_id=str(_get(price, "id")),
            active=bool(_get(price, "active")),
            recurring_interval=str(_get(recurring, "interval")) if recurring else None,
            currency=str(currency) if currency else None,
            livemode=bool(_get(price, "livemode")),
        )

    def get_customer_app_user_id(self, customer_id: str) -> UUID | None:
        customer = self._call(self._client.v1.customers.retrieve, customer_id)
        if _get(customer, "deleted"):
            return None
        raw = _get(_get(customer, "metadata"), _METADATA_USER_KEY)
        try:
            return UUID(str(raw)) if raw else None
        except ValueError:
            return None


class StripeWebhookVerifier:
    """Verifies the Stripe-Signature header against the unmodified raw body (BILL-08).

    Pure cryptographic verification; no network access. Used in every
    integration mode so tests exercise the real verification path.
    """

    def __init__(self, signing_secret: str) -> None:
        self._secret = signing_secret

    def verify(self, payload: bytes, signature_header: str | None) -> BillingWebhookEvent:
        if not signature_header:
            raise WebhookSignatureError()
        try:
            event = stripe.Webhook.construct_event(
                payload,
                signature_header,
                self._secret,
                tolerance=_SIGNATURE_TOLERANCE_SECONDS,  # gitleaks:allow (variable reference, not a secret)
            )
        except (stripe.SignatureVerificationError, ValueError) as exc:
            raise WebhookSignatureError() from exc

        obj = _get(_get(event, "data"), "object")
        customer_id = _id(obj) if _get(obj, "object") == "customer" else _id(_get(obj, "customer"))
        return BillingWebhookEvent(
            event_id=str(_get(event, "id")),
            event_type=str(_get(event, "type")),
            created_at=_ts(_get(event, "created")) or datetime.now(UTC),
            customer_id=customer_id,
        )
