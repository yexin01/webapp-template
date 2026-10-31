"""The billing adapter and synchronization against real Stripe test-mode objects (spec §9.10, DEC-006).

Nothing here is mocked: prices, customers, Checkout Sessions, and portal sessions are Stripe's, and
renewals and failed renewals are driven by test clocks. No payment is made through a hosted page.

Status: NOT VERIFIED until someone runs `pnpm test:stripe-sandbox` with a sandbox key and records the result.
"""

from __future__ import annotations

import time
import uuid
import warnings
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import stripe

from app.kernel.diagnostics import IntegrationInfo, IntegrationKey, IntegrationMode, Outcome
from app.kernel.errors import InvalidRequestError
from app.modules.billing.adapters.stripe import StripeBillingProvider
from app.modules.billing.domain import PlanKey, PriceCatalog, SubscriptionStatus, portal_problems
from app.modules.billing.ports import BillingWebhookEvent
from app.modules.billing.probe import BillingProbe
from app.modules.identity.adapters.clerk import ClerkWebhookVerifier
from tests.conftest import Harness, superuser_query
from tests.integration.test_platform import SVIX_SECRET, clerk_send
from tests.sandbox.conftest import PRICE_MONTHLY, PRICE_YEARLY, PRODUCT

Cleanup = list[Callable[[], object]]
RUN = uuid.uuid4().hex[:8]
TWO_HOURS = 2 * 3600  # a renewal invoice stays a draft for about an hour before it is finalized and charged


def eventually(check: Callable[[], Any], *, seconds: float = 90, every: float = 3) -> Any:
    """Poll until ``check`` returns something truthy: Stripe finishes some work after its API call returns."""
    deadline = time.monotonic() + seconds
    while True:
        result = check()
        if result or time.monotonic() >= deadline:
            return result
        time.sleep(every)


def new_user(world: Harness, name: str | None = None) -> tuple[dict[str, str], uuid.UUID]:
    headers = world.login(name or f"sandbox_{uuid.uuid4().hex[:12]}")
    return headers, uuid.UUID(world.client.get("/api/v1/me", headers=headers).json()["user_id"])


def status_of(world: Harness, headers: dict[str, str]) -> dict[str, Any]:
    body: dict[str, Any] = world.client.get("/api/v1/billing/status", headers=headers).json()
    return body


def synchronize(world: Harness, customer_id: str, event_type: str = "customer.subscription.created") -> None:
    """The path after a webhook's signature is verified, with the customer read from Stripe for real."""
    event = BillingWebhookEvent(f"evt_sandbox_{uuid.uuid4().hex}", event_type, datetime.now(UTC), customer_id)
    world.container.billing.service.process_webhook(event)


def reconcile(world: Harness, user: uuid.UUID) -> None:
    world.container.billing.service.reconcile(user_id=user, apply=True)


def forget_customer_of(admin: stripe.StripeClient, world: Harness, user: uuid.UUID) -> None:
    for (customer_id,) in superuser_query(
        world.database_url, "SELECT stripe_customer_id FROM app.billing_customers WHERE user_id = %s", (user,)
    ):
        admin.v1.customers.delete(str(customer_id))


# -- Prices and the portal ------------------------------------------------------


def test_the_configured_prices_exist_in_test_mode_and_match_the_catalog(provider: StripeBillingProvider) -> None:
    for price_id, interval in ((PRICE_MONTHLY, "month"), (PRICE_YEARLY, "year")):
        price = provider.describe_price(price_id)
        assert price.active, f"{price_id} is archived"
        assert price.recurring_interval == interval
        assert price.livemode is False
        assert price.product_id == PRODUCT, f"{price_id} belongs to {price.product_id}, not STRIPE_PRODUCT_PRO"
    probe = BillingProbe(
        IntegrationInfo(IntegrationKey.BILLING, "Stripe", IntegrationMode.SANDBOX),
        provider,
        PriceCatalog({PlanKey.PRO_MONTHLY: PRICE_MONTHLY, PlanKey.PRO_YEARLY: PRICE_YEARLY}, PRODUCT),
    )
    verdict = probe.check(None)  # type: ignore[arg-type]
    assert verdict.outcome is Outcome.PASSED, verdict.summary


def test_the_default_customer_portal_follows_dec_010_and_opens(
    world: Harness, admin: stripe.StripeClient, provider: StripeBillingProvider, cleanup: Cleanup
) -> None:
    problems = portal_problems(
        provider.describe_portal(), PriceCatalog({PlanKey.PRO_MONTHLY: PRICE_MONTHLY, PlanKey.PRO_YEARLY: PRICE_YEARLY})
    )
    assert problems == [], (
        "Fix in the Stripe Dashboard, Settings > Billing > Customer portal (test mode): " + "; ".join(problems)
    )

    _, user = new_user(world)
    customer = provider.create_customer(user)
    cleanup.append(lambda: admin.v1.customers.delete(customer))
    url = provider.create_portal_session(customer_id=customer, return_url="http://localhost:3000/billing?from=portal")
    assert url.startswith("https://billing.stripe.com/")


# -- Checkout (no payment) -----------------------------------------------------


def test_checkout_sessions_are_created_for_the_user_and_the_next_one_closes_the_last(
    world: Harness, admin: stripe.StripeClient, cleanup: Cleanup
) -> None:
    """DEC-013 against Stripe's own objects: two tabs leave one session that can be completed."""
    headers, user = new_user(world)
    cleanup.append(lambda: forget_customer_of(admin, world, user))

    def checkout(plan: str) -> str:
        r = world.client.post(
            "/api/v1/billing/checkout", json={"plan": plan}, headers={**headers, "Idempotency-Key": uuid.uuid4().hex}
        )
        assert r.status_code == 200, r.text
        url = str(r.json()["url"])
        assert url.startswith("https://checkout.stripe.com/")
        return url

    checkout("pro_monthly")
    [(first,)] = superuser_query(
        world.database_url, "SELECT stripe_session_id FROM app.billing_checkout_sessions WHERE user_id = %s", (user,)
    )
    cleanup.append(lambda: admin.v1.checkout.sessions.expire(str(first)))
    session = admin.v1.checkout.sessions.retrieve(str(first))
    assert (session.status, session.mode, session.client_reference_id) == ("open", "subscription", str(user))
    assert list(session.payment_method_types) == ["card"]  # no bank debit: Pro would start before the funds arrive
    assert session.subscription is None  # nothing was paid

    checkout("pro_yearly")
    [(second,)] = superuser_query(
        world.database_url, "SELECT stripe_session_id FROM app.billing_checkout_sessions WHERE user_id = %s", (user,)
    )
    cleanup.append(lambda: admin.v1.checkout.sessions.expire(str(second)))
    assert second != first
    assert admin.v1.checkout.sessions.retrieve(str(first)).status == "expired"
    assert admin.v1.checkout.sessions.retrieve(str(second)).status == "open"


def test_a_customer_deleted_in_stripe_is_replaced_at_the_next_checkout(
    world: Harness, admin: stripe.StripeClient, provider: StripeBillingProvider, cleanup: Cleanup
) -> None:
    """BILL-04 against Stripe's own objects. Stripe documents only that a deleted customer takes no further operation
    (https://docs.stripe.com/api/customers/delete), not what listing its subscriptions or its open sessions answers: a
    rejection, or a list. The service replaces the customer on either answer (the rejection of the listing, or of the
    new session), so this accepts both and reports which one Stripe gave as warnings."""
    headers, user = new_user(world)
    cleanup.append(lambda: forget_customer_of(admin, world, user))

    def checkout(plan: str) -> str:
        r = world.client.post(
            "/api/v1/billing/checkout", json={"plan": plan}, headers={**headers, "Idempotency-Key": uuid.uuid4().hex}
        )
        assert r.status_code == 200, r.text
        return str(r.json()["url"])

    def linked_customer() -> str:
        [(customer_id,)] = superuser_query(
            world.database_url, "SELECT stripe_customer_id FROM app.billing_customers WHERE user_id = %s", (user,)
        )
        return str(customer_id)

    checkout("pro_monthly")
    old = linked_customer()
    assert provider.customer_exists(old) is True
    admin.v1.customers.delete(old)

    assert provider.customer_exists(old) is False

    def subscriptions() -> str:
        return f"answers with {len(provider.list_customer_subscriptions(old))} of them"

    def open_sessions() -> str:
        provider.expire_open_checkout_sessions(old)
        return "answers"

    for what, read in (("subscriptions", subscriptions), ("open Checkout Sessions", open_sessions)):
        try:
            answer = read()
        except InvalidRequestError:
            answer = "is rejected"
        warnings.warn(f"Listing the {what} of a deleted customer {answer} (BILL-04, BILL-16).", stacklevel=1)

    url = checkout("pro_yearly")  # the one that must not answer 400 for good
    assert url.startswith("https://checkout.stripe.com/")
    new = linked_customer()
    assert new != old
    assert provider.customer_exists(new) is True
    assert admin.v1.customers.retrieve(new).metadata["app_user_id"] == str(user)
    [(session_id,)] = superuser_query(
        world.database_url, "SELECT stripe_session_id FROM app.billing_checkout_sessions WHERE user_id = %s", (user,)
    )
    cleanup.append(lambda: admin.v1.checkout.sessions.expire(str(session_id)))
    session = admin.v1.checkout.sessions.retrieve(str(session_id))
    assert (session.status, session.customer) == ("open", new)


# -- Test clocks: renewal, cancellation, and a failed renewal --------------------


def start_clock(admin: stripe.StripeClient, cleanup: Cleanup, name: str) -> str:
    clock = admin.v1.test_helpers.test_clocks.create(params={"frozen_time": int(time.time()), "name": name})
    cleanup.append(lambda: admin.v1.test_helpers.test_clocks.delete(clock.id))
    return str(clock.id)


def advance(admin: stripe.StripeClient, clock_id: str, to: int) -> None:
    """Move the clock and wait until Stripe has finished everything that happens on the way (it is asynchronous)."""
    admin.v1.test_helpers.test_clocks.advance(clock_id, params={"frozen_time": to})

    def state() -> str:
        return str(admin.v1.test_helpers.test_clocks.retrieve(clock_id).status)

    assert eventually(lambda: state() in ("ready", "internal_failure"), seconds=180, every=3)
    assert state() == "ready", f"the test clock ended as {state()}"


def subscribe_on_clock(
    admin: stripe.StripeClient, clock_id: str, user: uuid.UUID, name: str, card: str = "pm_card_visa"
) -> tuple[str, str]:
    """A customer on the clock, as the application links them (metadata), subscribed to the monthly price."""
    customer = admin.v1.customers.create(
        params={"test_clock": clock_id, "name": name, "metadata": {"app_user_id": str(user)}}
    )
    method = admin.v1.payment_methods.attach(card, params={"customer": customer.id})
    admin.v1.customers.update(customer.id, params={"invoice_settings": {"default_payment_method": method.id}})
    subscription = admin.v1.subscriptions.create(
        params={
            "customer": customer.id,
            "items": [{"price": PRICE_MONTHLY}],
            "metadata": {"app_user_id": str(user)},
        }
    )
    assert subscription.status == "active", f"the first payment did not succeed ({subscription.status})"
    return str(customer.id), str(subscription.id)


def period_end(admin: stripe.StripeClient, subscription_id: str) -> int:
    # Since API version 2025-03-31 the billing period belongs to the subscription item.
    return int(admin.v1.subscriptions.retrieve(subscription_id)["items"]["data"][0]["current_period_end"])


def test_a_subscription_is_pro_until_its_scheduled_cancellation_passes_and_then_free(
    world: Harness, admin: stripe.StripeClient, cleanup: Cleanup
) -> None:
    headers, user = new_user(world)
    clock = start_clock(admin, cleanup, f"template-sandbox-{RUN}-cancel")
    customer, subscription = subscribe_on_clock(admin, clock, user, f"template-sandbox-{RUN}-cancel")

    synchronize(world, customer)  # adoption from the customer's metadata, through the real customers.retrieve
    first = status_of(world, headers)
    assert (first["plan"], first["state"], first["plan_key"]) == ("pro", "active", "pro_monthly")

    end = period_end(admin, subscription)
    advance(admin, clock, end + TWO_HOURS)  # the renewal is paid
    reconcile(world, user)
    renewed = status_of(world, headers)
    assert (renewed["plan"], renewed["state"]) == ("pro", "active")
    assert datetime.fromisoformat(renewed["current_period_end"]).timestamp() > end

    admin.v1.subscriptions.update(subscription, params={"cancel_at_period_end": True})
    reconcile(world, user)
    canceling = status_of(world, headers)
    assert (canceling["plan"], canceling["state"]) == ("pro", "canceling")

    admin.v1.subscriptions.update(subscription, params={"cancel_at_period_end": False})
    reconcile(world, user)
    assert status_of(world, headers)["state"] == "active"  # reactivated before the period ended

    admin.v1.subscriptions.update(subscription, params={"cancel_at_period_end": True})
    advance(admin, clock, period_end(admin, subscription) + TWO_HOURS)
    reconcile(world, user)
    ended = status_of(world, headers)
    assert (ended["plan"], ended["state"]) == ("free", "canceled")
    assert ended["checkout_blocked"] is None  # a finished subscription lets the user subscribe again


def test_a_failed_renewal_keeps_pro_while_stripe_retries_and_recovery_is_followed(
    world: Harness, admin: stripe.StripeClient, cleanup: Cleanup
) -> None:
    """DEC-010: past_due keeps Pro. The card declines only after the first payment, as a real renewal failure does."""
    headers, user = new_user(world)
    clock = start_clock(admin, cleanup, f"template-sandbox-{RUN}-pastdue")
    customer, subscription = subscribe_on_clock(admin, clock, user, f"template-sandbox-{RUN}-pastdue")
    synchronize(world, customer)
    assert status_of(world, headers)["plan"] == "pro"

    declining = admin.v1.payment_methods.attach("pm_card_chargeCustomerFail", params={"customer": customer})
    admin.v1.customers.update(customer, params={"invoice_settings": {"default_payment_method": declining.id}})
    advance(admin, clock, period_end(admin, subscription) + TWO_HOURS)

    def past_due() -> dict[str, Any] | None:
        reconcile(world, user)
        status = status_of(world, headers)
        return status if status["state"] == "past_due" else None

    failed = eventually(past_due, seconds=60)
    assert failed, "the renewal did not fail: the subscription is not past_due"
    assert failed["plan"] == "pro"  # kept while Stripe retries (DEC-010)

    working = admin.v1.payment_methods.attach("pm_card_visa", params={"customer": customer})
    admin.v1.customers.update(customer, params={"invoice_settings": {"default_payment_method": working.id}})
    latest = admin.v1.subscriptions.retrieve(subscription, params={"expand": ["latest_invoice"]})
    admin.v1.invoices.pay(str(latest.latest_invoice.id), params={"payment_method": working.id})

    def recovered() -> bool:
        reconcile(world, user)
        return status_of(world, headers)["state"] == "active"

    assert eventually(recovered, seconds=60), "paying the open invoice did not bring the subscription back to active"


# -- A user Clerk reports as deleted (BILL-16) -----------------------------------


def test_a_subscription_is_set_to_end_with_its_period_through_the_adapter(
    world: Harness, admin: stripe.StripeClient, provider: StripeBillingProvider, cleanup: Cleanup
) -> None:
    """The request the adapter sends (Update Subscription with ``cancel_at_period_end``) does what the loopback stub
    only assumes: the subscription stays active to the end of the period it paid for, and nothing else changes."""
    _, user = new_user(world)
    clock = start_clock(admin, cleanup, f"template-sandbox-{RUN}-end")
    customer, subscription = subscribe_on_clock(admin, clock, user, f"template-sandbox-{RUN}-end")
    end = datetime.fromtimestamp(period_end(admin, subscription), UTC)

    ended = provider.cancel_subscription_at_period_end(subscription)

    assert (ended.status, ended.cancel_at_period_end, ended.current_period_end) == (
        SubscriptionStatus.ACTIVE,
        True,
        end,
    )
    at_stripe = admin.v1.subscriptions.retrieve(subscription)
    assert (at_stripe.status, at_stripe.cancel_at_period_end) == ("active", True)
    again = provider.cancel_subscription_at_period_end(subscription)
    assert (again.status, again.cancel_at_period_end) == (SubscriptionStatus.ACTIVE, True)  # a repeat changes nothing
    listed = provider.list_customer_subscriptions(customer)
    assert [(s.stripe_subscription_id, s.cancel_at_period_end) for s in listed] == [(subscription, True)]


def test_a_subscription_awaiting_its_first_payment_is_cancelled_at_once_through_the_adapter(
    world: Harness, admin: stripe.StripeClient, provider: StripeBillingProvider, cleanup: Cleanup
) -> None:
    """The premise of the user.deleted handler for an ``incomplete`` subscription: Stripe's Cancel Subscription accepts
    one that was never paid (https://docs.stripe.com/api/subscriptions/cancel), and ends it."""
    _, user = new_user(world)
    customer = admin.v1.customers.create(
        params={"name": f"template-sandbox-{RUN}-incomplete", "metadata": {"app_user_id": str(user)}}
    )
    cleanup.append(lambda: admin.v1.customers.delete(customer.id))
    subscription = admin.v1.subscriptions.create(
        params={"customer": customer.id, "items": [{"price": PRICE_MONTHLY}], "payment_behavior": "default_incomplete"}
    )
    assert subscription.status == "incomplete", f"the subscription is {subscription.status}, not awaiting a payment"

    canceled = provider.cancel_subscription_immediately(str(subscription.id))

    # Which of the two Stripe reports for a subscription canceled before its payment is not documented; both are over.
    assert canceled.status in (SubscriptionStatus.CANCELED, SubscriptionStatus.INCOMPLETE_EXPIRED)
    assert admin.v1.subscriptions.retrieve(str(subscription.id)).status in ("canceled", "incomplete_expired")


def test_user_deleted_ends_the_subscription_and_the_open_session_through_the_real_adapter(
    make_harness: Callable[..., Harness], admin: stripe.StripeClient, provider: StripeBillingProvider, cleanup: Cleanup
) -> None:
    """ADR-0004 section 21 and 27 against Stripe: the handler (``BILLING_ON_USER_DELETED`` is off in mock mode, so the
    test turns it on) ends a subscription at the period's end and expires a Checkout Session that could still be paid.
    Clerk's event is signed here with a test secret."""
    world = make_harness(
        {
            "stripe_price_pro_monthly": PRICE_MONTHLY,
            "stripe_price_pro_yearly": PRICE_YEARLY,
            "stripe_product_pro": PRODUCT,
            "billing_on_user_deleted": "cancel_at_period_end",
        },
        billing_provider=provider,
        identity_webhook_verifier=ClerkWebhookVerifier(SVIX_SECRET),
    )
    subscriber = f"sandbox_{uuid.uuid4().hex[:12]}"
    _, subscriber_id = new_user(world, subscriber)
    clock = start_clock(admin, cleanup, f"template-sandbox-{RUN}-deleted")
    customer, subscription = subscribe_on_clock(admin, clock, subscriber_id, f"template-sandbox-{RUN}-deleted")
    synchronize(world, customer)  # linked, and the subscription stored, as a webhook would

    shopper = f"sandbox_{uuid.uuid4().hex[:12]}"
    headers, shopper_id = new_user(world, shopper)
    cleanup.append(lambda: forget_customer_of(admin, world, shopper_id))
    opened = world.client.post(
        "/api/v1/billing/checkout",
        json={"plan": "pro_monthly"},
        headers={**headers, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert opened.status_code == 200, opened.text
    [(session_id,)] = superuser_query(
        world.database_url,
        "SELECT stripe_session_id FROM app.billing_checkout_sessions WHERE user_id = %s",
        (shopper_id,),
    )
    assert admin.v1.checkout.sessions.retrieve(str(session_id)).status == "open"

    for name in (subscriber, shopper):
        assert clerk_send(world, "user.deleted", {"id": name, "deleted": True}).json()["status"] == "processed"

    ended = admin.v1.subscriptions.retrieve(subscription)
    assert (ended.status, ended.cancel_at_period_end) == ("active", True)  # Pro runs to the end of the paid period
    assert admin.v1.checkout.sessions.retrieve(str(session_id)).status == "expired"  # nothing left to pay
