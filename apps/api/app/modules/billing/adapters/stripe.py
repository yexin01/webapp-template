"""Stripe adapters. Stripe SDK types never leave this module (ARCH-04)."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import stripe

from app.kernel.errors import (
    DependencyUnavailableError,
    ForbiddenError,
    InvalidRequestError,
    NotConfiguredError,
    ProviderRejectedError,
    WebhookSignatureError,
)
from app.modules.billing.domain import (
    PlanKey,
    PortalConfiguration,
    PriceCatalog,
    SubscriptionSnapshot,
    SubscriptionStatus,
)
from app.modules.billing.ports import BillingWebhookEvent, CheckoutSession, PriceDescription

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


def _plan_key(item: Any, catalog: PriceCatalog) -> PlanKey:
    """The plan of one subscription item: its price, or the product and billing interval of its price."""
    price = _get(item, "price")
    recurring = _get(price, "recurring")
    return catalog.key_for(
        _id(price),
        _id(_get(price, "product")),
        _get(recurring, "interval"),
        _get(recurring, "interval_count"),
    )


def to_snapshot(sub: Any, catalog: PriceCatalog) -> SubscriptionSnapshot:
    items = _get(_get(sub, "items"), "data") or []
    # A subscription may carry more items (add-ons); the plan is the item with a known plan.
    item = next((i for i in items if _plan_key(i, catalog) is not PlanKey.UNKNOWN), items[0] if items else None)
    price_id = _id(_get(item, "price"))
    # Since API version 2025-03-31, billing periods live on subscription items.
    period_end = _get(item, "current_period_end") or _get(sub, "current_period_end")
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
        plan_key=_plan_key(item, catalog),
        price_id=price_id,
        current_period_end=_ts(period_end),
        cancel_at_period_end=bool(_get(sub, "cancel_at_period_end")),
        cancel_at=_ts(_get(sub, "cancel_at")),
        ended_at=_ts(_get(sub, "ended_at")),
    )


def _configuration_error(exc: stripe.StripeError) -> ProviderRejectedError:
    # The error handler reports this once, scrubbed, at error level; an ERROR record here would be a second
    # event. Stripe's message text never leaves: it can quote request values.
    logger.warning(
        "stripe_configuration_error",
        extra={
            "error_type": type(exc).__name__,
            "code": exc.code,
            "param": getattr(exc, "param", None),
            "http_status": exc.http_status,
            "request_id": exc.request_id,
        },
    )
    return ProviderRejectedError("Billing is not configured correctly for this Stripe account.")


class StripeBillingProvider:
    def __init__(
        self,
        *,
        secret_key: str,
        catalog: PriceCatalog,
        timeout_seconds: float = 10.0,
        api_base: str | None = None,
        custom_domain: str | None = None,
    ) -> None:
        self._catalog = catalog
        self._custom_domain = custom_domain
        self._client = stripe.StripeClient(
            secret_key,
            # The SDK attaches idempotency keys to retried POSTs, so bounded
            # retries never duplicate side effects.
            max_network_retries=2,
            http_client=stripe.HTTPXClient(timeout=timeout_seconds, allow_sync_methods=True),
            # Only set for local stubs such as stripe-mock; defaults to Stripe's API.
            base_addresses={"api": api_base} if api_base else None,
        )

    def _call(self, fn: Any, *args: Any, server_built: bool = False, **kwargs: Any) -> Any:
        """Call Stripe and map its failures.

        A rejected key is a configuration error. So is a rejected request whose
        every input the server built itself (``server_built``): it cannot be the
        caller's fault, and a deleted customer is the one input that can go stale.
        Both reach error reporting; messages from Stripe never do.
        """
        try:
            return fn(*args, **kwargs)
        except (stripe.AuthenticationError, stripe.PermissionError) as exc:
            raise _configuration_error(exc) from exc
        except stripe.InvalidRequestError as exc:
            if server_built and exc.param != "customer":
                raise _configuration_error(exc) from exc
            logger.warning("stripe_invalid_request", extra={"code": exc.code, "http_status": exc.http_status})
            raise InvalidRequestError("The billing request was rejected.") from exc
        except stripe.StripeError as exc:
            logger.warning("stripe_error", extra={"error_type": type(exc).__name__, "http_status": exc.http_status})
            raise DependencyUnavailableError("Billing is temporarily unavailable.") from exc

    def create_customer(self, user_id: UUID, *, replaces: str | None = None) -> str:
        # Stripe replays a response for 24 hours per idempotency key, a deleted customer's included: a replacement
        # needs a key of its own, and a repeat of the same replacement gets the customer it made.
        key = f"customer-create-{user_id}" if replaces is None else f"customer-create-{user_id}-replaces-{replaces}"
        customer = self._call(
            self._client.v1.customers.create,
            params={"metadata": {_METADATA_USER_KEY: str(user_id)}},
            options={"idempotency_key": key},
            server_built=True,
        )
        return str(customer.id)

    def customer_exists(self, customer_id: str) -> bool:
        try:
            customer = self._call(self._client.v1.customers.retrieve, customer_id)
        except InvalidRequestError as exc:
            cause = exc.__cause__
            if isinstance(cause, stripe.InvalidRequestError) and cause.code == "resource_missing":
                return False
            raise
        return not _get(customer, "deleted")

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
                # Cards only: a bank debit reads "active" before the funds arrive (docs/limitations.md).
                "allowed_payment_method_types": ["card"],
            },
            options={"idempotency_key": idempotency_key},
            server_built=True,
        )
        if not session.url:
            raise DependencyUnavailableError("Billing is temporarily unavailable.")
        return CheckoutSession(str(session.id), self._stripe_hosted(str(session.url)), _ts(_get(session, "expires_at")))

    def expire_checkout_session(self, session_id: str) -> None:
        self._call(self._client.v1.checkout.sessions.expire, session_id)

    def open_checkout_session_url(self, session_id: str) -> str | None:
        session = self._call(self._client.v1.checkout.sessions.retrieve, session_id)
        url = _get(session, "url")
        return self._stripe_hosted(str(url)) if _get(session, "status") == "open" and url else None

    def create_portal_session(self, *, customer_id: str, return_url: str) -> str:
        session = self._call(
            self._client.v1.billing_portal.sessions.create,
            params={"customer": customer_id, "return_url": return_url},
            server_built=True,
        )
        return self._stripe_hosted(str(session.url))

    def _stripe_hosted(self, url: str) -> str:
        """The user is sent to this URL, so it must be on Stripe or the account's custom domain (SEC-14)."""
        try:
            parts = urlsplit(url)
            host, userinfo = parts.hostname, parts.username
        except ValueError:
            host, userinfo = None, None
            parts = urlsplit("")
        if (
            parts.scheme != "https"
            or host is None
            or userinfo is not None
            or "\\" in url
            or not (host == "stripe.com" or host.endswith(".stripe.com") or host == self._custom_domain)
        ):
            # The host only: the path carries the session ID.
            logger.error("stripe_session_host_unexpected", extra={"host": (host or "")[:253]})
            raise NotConfiguredError(
                "Billing redirects are not configured for this Stripe account (STRIPE_CUSTOM_DOMAIN)."
            )
        return url

    def list_customer_subscriptions(self, customer_id: str) -> list[SubscriptionSnapshot]:
        result = self._call(
            self._client.v1.subscriptions.list,
            params={"customer": customer_id, "status": "all", "limit": _MAX_SUBSCRIPTIONS_PER_CUSTOMER},
        )
        return [to_snapshot(sub, self._catalog) for sub in result.data]

    def retrieve_subscription(self, subscription_id: str) -> SubscriptionSnapshot:
        return to_snapshot(self._call(self._client.v1.subscriptions.retrieve, subscription_id), self._catalog)

    def cancel_subscription_at_period_end(self, subscription_id: str) -> SubscriptionSnapshot:
        # A natural repeat: no idempotency key, because Stripe replays even a failed response for 24 hours per key.
        updated = self._call(
            self._client.v1.subscriptions.update, subscription_id, params={"cancel_at_period_end": True}
        )
        return to_snapshot(updated, self._catalog)

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
            product_id=_id(_get(price, "product")),
        )

    def describe_portal(self) -> PortalConfiguration | None:
        # Diagnostics only, like describe_price. Read only: the portal's configuration is owned by
        # the Stripe Dashboard (ADR-0001) and is never created or changed from here.
        try:
            found = self._client.v1.billing_portal.configurations.list(params={"is_default": True, "limit": 1})
        except stripe.AuthenticationError as exc:
            raise DependencyUnavailableError("Stripe rejected the API key.") from exc
        except stripe.PermissionError as exc:
            raise ForbiddenError("The Stripe API key cannot read Customer Portal configurations.") from exc
        except stripe.InvalidRequestError as exc:
            raise InvalidRequestError("Stripe rejected the request for the portal configuration.") from exc
        except stripe.StripeError as exc:
            raise DependencyUnavailableError("Stripe is temporarily unavailable.") from exc
        if not found.data:
            return None
        config = found.data[0]
        features = _get(config, "features")
        cancel = _get(features, "subscription_cancel")
        update = _get(features, "subscription_update")
        return PortalConfiguration(
            active=bool(_get(config, "active")),
            payment_method_update=bool(_get(_get(features, "payment_method_update"), "enabled")),
            invoice_history=bool(_get(_get(features, "invoice_history"), "enabled")),
            cancel_enabled=bool(_get(cancel, "enabled")),
            cancel_mode=str(_get(cancel, "mode") or ""),
            switch_enabled=bool(_get(update, "enabled")),
            allowed_updates=tuple(str(u) for u in _get(update, "default_allowed_updates") or []),
            switch_prices=frozenset(
                str(price) for product in _get(update, "products") or [] for price in _get(product, "prices") or []
            ),
        )

    def check_subscriptions_readable(self) -> None:
        # Diagnostics only, like describe_portal. A restricted key without Subscriptions read access passes the
        # price and portal checks, and then every synchronization fails.
        try:
            self._client.v1.subscriptions.list(params={"limit": 1})
        except stripe.AuthenticationError as exc:
            raise DependencyUnavailableError("Stripe rejected the API key.") from exc
        except stripe.PermissionError as exc:
            raise ForbiddenError("The Stripe API key cannot read Subscriptions.") from exc
        except stripe.InvalidRequestError as exc:
            raise InvalidRequestError("Stripe rejected the request to list subscriptions.") from exc
        except stripe.StripeError as exc:
            raise DependencyUnavailableError("Stripe is temporarily unavailable.") from exc

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
            logger.warning("stripe_webhook_rejected", extra={"reason": "missing_signature"})
            raise WebhookSignatureError()
        try:
            stripe.WebhookSignature.verify_header(
                payload,
                signature_header,
                self._secret,
                _SIGNATURE_TOLERANCE_SECONDS,  # gitleaks:allow (variable reference, not a secret)
            )
        except (stripe.SignatureVerificationError, ValueError) as exc:
            # Never the body or the header (BILL-08).
            logger.warning("stripe_webhook_rejected", extra={"reason": type(exc).__name__})
            raise WebhookSignatureError() from exc

        # Signed by Stripe, so a body this API cannot read is a setup mistake, not a forgery.
        try:
            body = json.loads(payload)
        except ValueError:
            body = None
        if not isinstance(body, dict) or body.get("object") != "event":
            found = body if isinstance(body, dict) else {}
            logger.error(
                "stripe_webhook_unsupported_payload",
                extra={"object": _short(found.get("object")), "event_type": _short(found.get("type"))},
            )
            raise InvalidRequestError(
                "This endpoint accepts Stripe snapshot events only. "
                "Create the Stripe event destination with the payload format Snapshot (docs/integrations.md)."
            )

        obj = _get(_get(body, "data"), "object")
        customer_id = _id(obj) if _get(obj, "object") == "customer" else _id(_get(obj, "customer"))
        return BillingWebhookEvent(
            event_id=str(_get(body, "id")),
            event_type=str(_get(body, "type")),
            created_at=_ts(_get(body, "created")) or datetime.now(UTC),
            customer_id=customer_id,
        )


def _short(value: Any) -> str:
    """A value from a signed body, bounded for the logs."""
    return value[:64] if isinstance(value, str) else type(value).__name__
