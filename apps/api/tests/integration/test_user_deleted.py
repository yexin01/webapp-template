"""A deleted user's subscription is ended at the period's end (BILL-16, ADR-0004 section 21).

Clerk reports `user.deleted`; identity revokes the memberships and records `identity.user_deleted` in the same
transaction; billing, which identity knows nothing about, handles that event after commit. Stripe keeps charging a
subscription whose user can no longer sign in, and nobody else can reach the Customer Portal.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

import pytest

from app.kernel.errors import DependencyUnavailableError, InvalidRequestError
from app.modules.billing.adapters.fakes import InMemoryBillingProvider
from app.modules.billing.domain import SubscriptionSnapshot, SubscriptionStatus
from app.modules.identity.adapters.clerk import ClerkWebhookVerifier
from tests.conftest import Harness, superuser_query
from tests.integration.test_billing import customer_of, event_payload, make_pro, send_webhook, snapshot
from tests.integration.test_platform import SVIX_SECRET, clerk_send

pytestmark = pytest.mark.db

SANDBOX = {"billing_mode": "sandbox", "stripe_secret_key": "sk_test_" + "o" * 24}
Make = Callable[..., Harness]


def world(make_harness: Make, **settings: Any) -> Harness:
    return make_harness({**SANDBOX, **settings}, identity_webhook_verifier=ClerkWebhookVerifier(SVIX_SECRET))


def delete_user(h: Harness, name: str = "user_a", msg_id: str | None = None) -> Any:
    return clerk_send(h, "user.deleted", {"id": name, "deleted": True}, msg_id=msg_id)


def events(h: Harness) -> list[tuple[Any, ...]]:
    return superuser_query(
        h.database_url,
        "SELECT payload->>'user_id', status, attempts FROM app.outbox WHERE event_type = 'identity.user_deleted' "
        "ORDER BY occurred_at",
    )


def sign_in(h: Harness, name: str = "user_a") -> None:
    """The app knows a user from their first request."""
    assert h.client.get("/api/v1/me", headers=h.login(name)).status_code == 200


def internal_id(h: Harness, name: str) -> str:
    return str(superuser_query(h.database_url, "SELECT id FROM app.users WHERE clerk_user_id = %s", (name,))[0][0])


def errors(caplog: pytest.LogCaptureFixture) -> list[Any]:
    return [r for r in caplog.records if r.getMessage() == "billing_deleted_user_has_subscription"]


def test_the_deletion_is_recorded_once_with_the_revocation_and_a_replay_adds_nothing(make_harness: Make) -> None:
    h = world(make_harness)
    sign_in(h)
    first = delete_user(h, msg_id="msg_deleted_1")
    assert first.json()["status"] == "processed"
    assert events(h) == [(internal_id(h, "user_a"), "processed", 1)]

    assert delete_user(h, msg_id="msg_deleted_1").json()["status"] == "processed"  # Clerk delivers the same event again
    assert len(events(h)) == 1


def test_a_user_the_app_never_saw_leaves_nothing_to_handle(make_harness: Make) -> None:
    h = world(make_harness)
    assert delete_user(h, "user_never_signed_in").json()["status"] == "processed"
    assert events(h) == []
    assert h.billing.calls == []


def test_the_subscription_is_set_to_end_when_the_period_ends(make_harness: Make) -> None:
    h = world(make_harness)
    customer = make_pro(h, "user_a")
    sub = f"sub_{customer[4:]}"

    assert delete_user(h).json()["status"] == "processed"

    assert h.billing.cancellations == [sub]
    assert [
        (s.stripe_subscription_id, s.cancel_at_period_end) for s in h.billing.list_customer_subscriptions(customer)
    ] == [(sub, True)]
    assert events(h)[0][1:] == ("processed", 1)
    # What was paid for runs out: Pro lasts to the period's end, and Stripe's own event shows it as canceling.
    assert send_webhook(h, event_payload(customer, "customer.subscription.updated")).json()["status"] == "processed"
    body = h.client.get("/api/v1/billing/status", headers=h.login("user_a")).json()
    assert (body["plan"], body["state"]) == ("pro", "canceling")


def test_a_second_event_for_the_same_user_ends_nothing_twice(make_harness: Make) -> None:
    h = world(make_harness)
    make_pro(h, "user_a")
    delete_user(h, msg_id="msg_first")
    delete_user(h, msg_id="msg_second")  # a different event for the same deletion
    assert len(events(h)) == 2
    assert len(h.billing.cancellations) == 1  # the second found the subscription already scheduled to end


def test_a_user_without_a_customer_costs_no_stripe_call(make_harness: Make) -> None:
    h = world(make_harness)
    sign_in(h)
    delete_user(h)
    assert h.billing.calls == []
    assert events(h)[0][1] == "processed"


def test_only_a_subscription_that_is_not_over_is_ended(make_harness: Make) -> None:
    h = world(make_harness)
    customer = make_pro(h, "user_a")
    h.billing.set_subscriptions(customer, [snapshot(customer, SubscriptionStatus.CANCELED)])
    delete_user(h)
    assert h.billing.cancellations == []
    assert "cancel_subscription_at_period_end" not in h.billing.calls


@pytest.mark.parametrize(
    "status", [SubscriptionStatus.PAST_DUE, SubscriptionStatus.TRIALING, SubscriptionStatus.UNPAID]
)
def test_a_subscription_that_still_bills_or_will_is_ended_in_any_such_state(
    make_harness: Make, status: SubscriptionStatus
) -> None:
    h = world(make_harness)
    customer = make_pro(h, "user_a", status)
    delete_user(h)
    assert h.billing.cancellations == [f"sub_{customer[4:]}"]


def test_a_failure_to_end_it_is_an_error_naming_the_customer_and_the_event_is_retried(
    make_harness: Make, caplog: pytest.LogCaptureFixture
) -> None:
    h = world(make_harness)
    customer = make_pro(h, "user_a")
    original = InMemoryBillingProvider.cancel_subscription_at_period_end

    def refuse(self: InMemoryBillingProvider, subscription_id: str) -> SubscriptionSnapshot:
        raise DependencyUnavailableError("Billing is temporarily unavailable.")

    h.billing.cancel_subscription_at_period_end = refuse.__get__(h.billing)  # type: ignore[method-assign]
    with caplog.at_level("ERROR"):
        assert delete_user(h).json()["status"] == "processed"  # the webhook is answered; the work is retried

    [record] = errors(caplog)
    assert (
        (record.levelname, record.stripe_customer_id, record.stripe_subscription_ids)
        == (  # type: ignore[attr-defined]
            "ERROR",
            customer,
            f"sub_{customer[4:]}",
        )
    )
    assert events(h)[0][1:] == ("pending", 1)
    assert h.billing.cancellations == []

    h.billing.cancel_subscription_at_period_end = original.__get__(h.billing)  # type: ignore[method-assign]
    h.clock.advance(seconds=2)  # the backoff after the first attempt
    assert h.container.kernel.outbox.drain().processed == 1
    assert events(h)[0][1:] == ("processed", 2)
    assert h.billing.cancellations == [f"sub_{customer[4:]}"]


def test_a_stripe_outage_while_looking_is_retried_too(make_harness: Make, caplog: pytest.LogCaptureFixture) -> None:
    h = world(make_harness)
    customer = make_pro(h, "user_a")
    h.billing.fail_with = lambda: DependencyUnavailableError("Billing is temporarily unavailable.")
    with caplog.at_level("ERROR"):
        delete_user(h)
    assert [r.stripe_customer_id for r in errors(caplog)] == [customer]  # type: ignore[attr-defined]
    assert events(h)[0][1] == "pending"

    h.billing.fail_with = None
    h.clock.advance(seconds=2)
    assert h.container.kernel.outbox.drain().processed == 1
    assert len(h.billing.cancellations) == 1


def test_an_outage_is_reported_even_when_nothing_is_stored_as_live(
    make_harness: Make, caplog: pytest.LogCaptureFixture
) -> None:
    """Stripe may know a subscription the webhook has not told us about: the customer is named either way."""
    h = world(make_harness)
    customer = make_pro(h, "user_a")
    h.billing.set_subscriptions(customer, [snapshot(customer, SubscriptionStatus.CANCELED)])
    assert send_webhook(h, event_payload(customer)).json()["status"] == "processed"  # stored as over
    h.billing.fail_with = lambda: DependencyUnavailableError("Billing is temporarily unavailable.")
    with caplog.at_level("ERROR"):
        delete_user(h)
    [record] = errors(caplog)
    assert (record.stripe_customer_id, record.stripe_subscription_ids) == (customer, "")  # type: ignore[attr-defined]
    assert events(h)[0][1] == "pending"


def test_a_customer_stripe_deleted_has_nothing_left_to_end(
    make_harness: Make, caplog: pytest.LogCaptureFixture
) -> None:
    """Deleting a customer cancels its subscriptions; there is nothing to retry and nothing to report."""
    h = world(make_harness)
    customer = make_pro(h, "user_a")
    h.billing.delete_customer(customer)
    with caplog.at_level("ERROR"):
        delete_user(h)
    assert errors(caplog) == []
    assert (h.billing.cancellations, events(h)[0][1]) == ([], "processed")


def test_a_rejection_for_a_customer_stripe_still_has_is_retried_and_reported(
    make_harness: Make, caplog: pytest.LogCaptureFixture
) -> None:
    h = world(make_harness)
    customer = make_pro(h, "user_a")

    def rejected(customer_id: str) -> list[SubscriptionSnapshot]:
        raise InvalidRequestError("The billing request was rejected.")

    h.billing.list_customer_subscriptions = rejected  # type: ignore[method-assign]
    with caplog.at_level("ERROR"):
        delete_user(h)
    assert [r.stripe_customer_id for r in errors(caplog)] == [customer]  # type: ignore[attr-defined]
    assert events(h)[0][1] == "pending"


def test_with_log_the_subscription_is_only_named_for_an_operator(
    make_harness: Make, caplog: pytest.LogCaptureFixture
) -> None:
    h = world(make_harness, billing_on_user_deleted="log")
    customer = make_pro(h, "user_a")
    calls = list(h.billing.calls)
    with caplog.at_level("ERROR"):
        delete_user(h)
    [record] = errors(caplog)
    assert (record.stripe_customer_id, record.stripe_subscription_ids) == (customer, f"sub_{customer[4:]}")  # type: ignore[attr-defined]
    assert h.billing.calls == calls  # Stripe was not asked anything
    assert events(h)[0][1:] == ("processed", 1)  # an alert, not a retry


def test_mock_mode_logs_by_default_and_a_user_without_a_live_subscription_is_not_an_alert(
    make_harness: Make, caplog: pytest.LogCaptureFixture
) -> None:
    h = make_harness({}, identity_webhook_verifier=ClerkWebhookVerifier(SVIX_SECRET))
    customer = make_pro(h, "user_a")
    h.billing.set_subscriptions(customer, [snapshot(customer, SubscriptionStatus.CANCELED)])
    assert send_webhook(h, event_payload(customer)).json()["status"] == "processed"  # stored as over
    with caplog.at_level("ERROR"):
        delete_user(h)
    assert errors(caplog) == []
    assert h.billing.cancellations == []


def test_the_event_names_the_user_and_nothing_else(make_harness: Make) -> None:
    h = world(make_harness)
    make_pro(h, "user_a")
    delete_user(h)
    [payload] = [
        r[0]
        for r in superuser_query(
            h.database_url, "SELECT payload FROM app.outbox WHERE event_type = 'identity.user_deleted'"
        )
    ]
    assert payload == {"user_id": internal_id(h, "user_a")}
    uuid.UUID(payload["user_id"])
    assert customer_of(h, "user_a") not in str(payload)  # no Stripe or Clerk identifiers in the payload
    assert "user_a" not in str(payload)
