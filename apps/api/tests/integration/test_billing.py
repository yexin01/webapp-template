"""Billing flows and webhook processing (spec §9; BILL-*, T-07 to T-09).

Stripe API calls use the in-memory provider (mocked). Webhook signatures are
generated with a test-only secret and verified by the production verifier.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from app.kernel.errors import DependencyUnavailableError, EntitlementRequiredError, InvalidRequestError
from app.kernel.tenancy import ContextKind, TenantContext
from app.modules.billing.adapters.fakes import InMemoryBillingProvider
from app.modules.billing.adapters.repository import PgBillingRepository
from app.modules.billing.adapters.stripe import to_snapshot
from app.modules.billing.domain import (
    PRO_INSIGHT,
    Plan,
    PlanKey,
    SubscriptionSnapshot,
    SubscriptionStatus,
    decide,
)
from tests.conftest import (
    PRICE_MONTHLY,
    PRICE_YEARLY,
    PRODUCT_PRO,
    TEST_STRIPE_WEBHOOK_SECRET,
    Harness,
    superuser_query,
)
from tests.lock_waits import wait_for_lock_wait

pytestmark = pytest.mark.db

FUTURE = datetime(2026, 7, 1, tzinfo=UTC)


def sign(payload: bytes, secret: str = TEST_STRIPE_WEBHOOK_SECRET, ts: int | None = None) -> str:
    stamp = ts if ts is not None else int(time.time())
    digest = hmac.new(secret.encode(), f"{stamp}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={stamp},v1={digest}"


def event_payload(
    customer_id: str,
    event_type: str = "customer.subscription.updated",
    event_id: str | None = None,
    obj: dict[str, Any] | None = None,
) -> bytes:
    return json.dumps(
        {
            "id": event_id or f"evt_{uuid.uuid4().hex}",
            "object": "event",
            "type": event_type,
            "created": int(time.time()),
            "data": {"object": obj or {"id": "sub_x", "object": "subscription", "customer": customer_id}},
        }
    ).encode()


def send_webhook(h: Harness, payload: bytes, signature: str | None = None) -> Any:
    headers = {
        "Stripe-Signature": signature if signature is not None else sign(payload),
        "Content-Type": "application/json",
    }
    return h.client.post("/api/v1/webhooks/stripe", content=payload, headers=headers)


def snapshot(customer_id: str, status: SubscriptionStatus, **kw: Any) -> SubscriptionSnapshot:
    values: dict[str, Any] = {
        "stripe_subscription_id": f"sub_{customer_id[4:]}",
        "stripe_customer_id": customer_id,
        "status": status,
        "plan_key": PlanKey.PRO_MONTHLY,
        "price_id": PRICE_MONTHLY,
        "current_period_end": FUTURE,
        "cancel_at_period_end": False,
        "cancel_at": None,
        "ended_at": None,
    }
    values.update(kw)
    return SubscriptionSnapshot(**values)


def start_checkout(h: Harness, headers: dict[str, str], plan: str = "pro_monthly") -> Any:
    return h.client.post(
        "/api/v1/billing/checkout", json={"plan": plan}, headers={**headers, "Idempotency-Key": uuid.uuid4().hex}
    )


def customer_of(h: Harness, external_user_id: str) -> str:
    rows = superuser_query(
        h.database_url,
        "SELECT c.stripe_customer_id FROM app.billing_customers c JOIN app.users u ON u.id = c.user_id WHERE u.clerk_user_id=%s",
        (external_user_id,),
    )
    return str(rows[0][0])


def make_pro(h: Harness, external_user_id: str, status: SubscriptionStatus = SubscriptionStatus.ACTIVE) -> str:
    headers = h.login(external_user_id)
    assert start_checkout(h, headers).status_code == 200
    customer = customer_of(h, external_user_id)
    h.billing.set_subscriptions(customer, [snapshot(customer, status)])
    assert send_webhook(h, event_payload(customer)).json()["status"] == "processed"
    return customer


# -- Checkout -----------------------------------------------------------------


def test_checkout_creates_customer_once_and_returns_url(harness: Harness) -> None:
    headers = harness.login("user_a")
    first = start_checkout(harness, headers)
    second = start_checkout(harness, headers, plan="pro_yearly")
    assert first.status_code == 200
    assert first.json()["url"].startswith("http://localhost:3000/billing/success")
    assert second.status_code == 200
    assert harness.billing.calls.count("create_customer") == 1
    assert "checkout_started" in [e[1] for e in harness.analytics.events]


def test_checkout_and_portal_send_the_plan_price_customer_and_configured_urls(harness: Harness) -> None:
    """BILL-03, BILL-05, SEC-14: what Stripe is asked for comes from the plan and configuration only."""
    headers = harness.login("user_a")
    user_id = harness.client.get("/api/v1/me", headers=headers).json()["user_id"]
    for plan in ("pro_monthly", "pro_yearly"):
        idempotency = {"Idempotency-Key": f"key-{plan}"}
        r = harness.client.post("/api/v1/billing/checkout", json={"plan": plan}, headers={**headers, **idempotency})
        assert r.status_code == 200
    customer = customer_of(harness, "user_a")
    sent = {
        "customer_id": customer,
        "user_id": uuid.UUID(user_id),
        "success_url": "http://localhost:3000/billing/success?session_id={CHECKOUT_SESSION_ID}",
        "cancel_url": "http://localhost:3000/billing?checkout=canceled",
    }
    first, second = harness.billing.checkout_requests
    assert first == {
        **sent,
        "price_id": PRICE_MONTHLY,
        "idempotency_key": f"checkout-{user_id}-pro_monthly-key-pro_monthly",
    }
    # The second request closed the first session; its key says so (see start_checkout).
    key = second.pop("idempotency_key")
    assert second == {**sent, "price_id": PRICE_YEARLY}
    assert isinstance(key, str)
    assert key.startswith(f"checkout-{user_id}-pro_yearly-key-pro_yearly-")
    assert harness.client.post("/api/v1/billing/portal", headers=headers).status_code == 200
    assert harness.billing.portal_requests == [
        {"customer_id": customer, "return_url": "http://localhost:3000/billing?from=portal"}
    ]


def test_checkout_rejects_unknown_plan_and_price_ids(harness: Harness) -> None:
    headers = {**harness.login("user_a"), "Idempotency-Key": uuid.uuid4().hex}
    for body in (
        {"plan": "pro_lifetime"},
        {"plan": "pro_monthly", "price": "price_attacker"},
        {"price": PRICE_MONTHLY},
    ):
        assert harness.client.post("/api/v1/billing/checkout", json=body, headers=headers).status_code == 422


def test_checkout_requires_idempotency_key(harness: Harness) -> None:
    r = harness.client.post("/api/v1/billing/checkout", json={"plan": "pro_monthly"}, headers=harness.login("user_a"))
    assert r.status_code == 422


@pytest.mark.parametrize(
    ("key", "status", "code"),
    [
        ("bad key!!", 400, "INVALID_REQUEST"),
        ("key with spaces", 400, "INVALID_REQUEST"),  # one offending character each, so a wider class is caught
        ("dot.in.key.1", 400, "INVALID_REQUEST"),
        ("slash/in/key1", 400, "INVALID_REQUEST"),
        ("semi;colon:key", 400, "INVALID_REQUEST"),
        ("short", 422, "VALIDATION_FAILED"),
        ("k" * 65, 422, "VALIDATION_FAILED"),
    ],
)
def test_checkout_refuses_an_idempotency_key_outside_8_to_64_letters_digits_hyphens_and_underscores(
    harness: Harness, key: str, status: int, code: str
) -> None:
    """The key becomes part of the key sent to Stripe and of a stored row: nothing else is accepted, before any call."""
    headers = {**harness.login("user_a"), "Idempotency-Key": key}
    r = harness.client.post("/api/v1/billing/checkout", json={"plan": "pro_monthly"}, headers=headers)
    assert (r.status_code, r.json()["code"]) == (status, code)
    assert harness.billing.calls == []  # a refused key costs no Stripe call and no customer
    assert superuser_query(harness.database_url, "SELECT count(*) FROM app.billing_customers") == [(0,)]


def test_checkout_accepts_the_whole_idempotency_alphabet(harness: Harness) -> None:
    headers = {**harness.login("user_a"), "Idempotency-Key": "Az09_-" + "x" * 58}
    assert (
        harness.client.post("/api/v1/billing/checkout", json={"plan": "pro_monthly"}, headers=headers).status_code
        == 200
    )


def test_checkout_refused_when_already_pro(harness: Harness) -> None:
    make_pro(harness, "user_a")
    r = start_checkout(harness, harness.login("user_a"))
    assert r.status_code == 409
    assert r.json()["code"] == "CONFLICT"


def test_checkout_rate_limited(make_harness: Any) -> None:
    h = make_harness({"checkouts_per_user_per_hour": 2})
    headers = h.login("user_a")
    assert [start_checkout(h, headers).status_code for _ in range(3)] == [200, 200, 429]
    r = start_checkout(h, headers)
    assert int(r.headers["retry-after"]) >= 1


def test_billing_provider_outage_is_503_not_500(harness: Harness) -> None:
    harness.billing.fail_with = lambda: DependencyUnavailableError("Billing is temporarily unavailable.")
    r = start_checkout(harness, harness.login("user_a"))
    assert r.status_code == 503
    assert r.json()["code"] == "DEPENDENCY_UNAVAILABLE"


def test_billing_not_configured(make_harness: Any) -> None:
    h = make_harness(billing_provider=None)
    r = start_checkout(h, h.login("user_a"))
    assert r.status_code == 503
    assert r.json()["code"] == "NOT_CONFIGURED"


def test_portal_requires_existing_customer(harness: Harness) -> None:
    headers = harness.login("user_a")
    assert harness.client.post("/api/v1/billing/portal", headers=headers).status_code == 409
    start_checkout(harness, headers)
    r = harness.client.post("/api/v1/billing/portal", headers=headers)
    assert r.status_code == 200
    assert r.json()["url"] == "http://localhost:3000/billing?from=portal"


def test_portal_rate_limited(make_harness: Any) -> None:
    h = make_harness({"portal_sessions_per_user_per_hour": 2})
    headers, other = h.login("user_a"), h.login("user_b")
    assert [h.client.post("/api/v1/billing/portal", headers=other).status_code for _ in range(3)] == [409] * 3
    for user in (headers, other):
        assert start_checkout(h, user).status_code == 200
    assert [h.client.post("/api/v1/billing/portal", headers=headers).status_code for _ in range(3)] == [200, 200, 429]
    r = h.client.post("/api/v1/billing/portal", headers=headers)
    assert int(r.headers["retry-after"]) >= 1
    assert h.billing.calls.count("create_portal_session") == 2  # no Stripe call past the limit
    # Each user has their own allowance, and a request refused for having no customer used none of it.
    assert [h.client.post("/api/v1/billing/portal", headers=other).status_code for _ in range(2)] == [200, 200]


def test_stripe_calls_from_checkout_and_the_portal_are_bounded_per_instance(make_harness: Any) -> None:
    """While Stripe is slow, at most the bound hold request threads in Stripe calls; the rest get 503 at once."""
    from fastapi.testclient import TestClient

    from app.bootstrap import create_app

    bound = 2
    h = make_harness({"billing_max_concurrent_stripe_calls": bound})
    headers, other = h.login("user_a"), h.login("user_b")
    assert start_checkout(h, headers).status_code == 200
    original = InMemoryBillingProvider.create_portal_session
    entered, release, lock = threading.Semaphore(0), threading.Event(), threading.Lock()
    inside = peak = 0

    def slow_portal(self: InMemoryBillingProvider, *, customer_id: str, return_url: str) -> str:
        nonlocal inside, peak
        with lock:
            inside += 1
            peak = max(peak, inside)
        entered.release()
        try:
            release.wait(10)  # Stripe answers only once the test lets it
            return original(self, customer_id=customer_id, return_url=return_url)
        finally:
            with lock:
                inside -= 1

    h.billing.create_portal_session = slow_portal.__get__(h.billing)  # type: ignore[method-assign]
    with TestClient(create_app(h.container), raise_server_exceptions=False) as client:  # one event loop
        responses: dict[str, Any] = {}
        threads: list[threading.Thread] = []

        def send(name: str, path: str, request_headers: dict[str, str], **kw: Any) -> threading.Thread:
            def run() -> None:
                started = time.monotonic()
                r = client.post(path, headers=request_headers, **kw)
                responses[name] = (r, time.monotonic() - started)

            thread = threading.Thread(target=run)
            threads.append(thread)
            thread.start()
            return thread

        try:
            for i in range(bound):
                send(f"held{i}", "/api/v1/billing/portal", headers)
            for _ in range(bound):
                assert entered.acquire(timeout=10)
            excess = [send(f"portal{i}", "/api/v1/billing/portal", headers) for i in range(3)]
            checkout_headers = {**other, "Idempotency-Key": uuid.uuid4().hex}
            excess.append(send("checkout", "/api/v1/billing/checkout", checkout_headers, json={"plan": "pro_monthly"}))
            deadline = time.monotonic() + 5
            for t in excess:
                t.join(max(0.0, deadline - time.monotonic()))
            # Answered while every slot is still held: none of them waited for Stripe.
            refused = [responses.get(name) for name in ("portal0", "portal1", "portal2", "checkout")]
            assert [r[0].status_code if r is not None else None for r in refused] == [503] * 4
            assert {r[0].json()["code"] for r in refused if r is not None} == {"DEPENDENCY_UNAVAILABLE"}
            # Refused at once: waiting for a slot, even briefly, would hold a request thread too.
            assert max(seconds for _, seconds in filter(None, refused)) < 0.5
            assert peak == bound
        finally:
            release.set()
            for t in threads:
                t.join(10)
        assert [responses[f"held{i}"][0].status_code for i in range(bound)] == [200] * bound
        assert client.post("/api/v1/billing/portal", headers=headers).status_code == 200  # slots given back
    # The refused checkout made no Stripe call: the only customer and session are user_a's.
    assert (h.billing.calls.count("create_customer"), h.billing.calls.count("create_checkout_session")) == (1, 1)


def test_a_failed_stripe_call_gives_its_slot_back(make_harness: Any) -> None:
    """A Stripe outage never uses up the slots: once Stripe answers again, checkout and the portal work again."""
    bound = 2
    h = make_harness({"billing_max_concurrent_stripe_calls": bound})
    headers = h.login("user_a")
    assert start_checkout(h, headers).status_code == 200
    h.billing.fail_with = lambda: DependencyUnavailableError("Billing is temporarily unavailable.")
    portal = [h.client.post("/api/v1/billing/portal", headers=headers).status_code for _ in range(bound + 1)]
    assert portal == [503] * (bound + 1)
    assert h.billing.calls.count("create_portal_session") == bound + 1  # each one reached Stripe: no slot was kept
    h.billing.fail_with = lambda: InvalidRequestError("The billing request was rejected.")
    assert [start_checkout(h, headers).status_code for _ in range(bound + 1)] == [400] * (bound + 1)
    h.billing.fail_with = None
    assert h.client.post("/api/v1/billing/portal", headers=headers).status_code == 200
    assert start_checkout(h, headers).status_code == 200


def test_a_user_past_their_allowance_gets_429_without_taking_a_stripe_slot(make_harness: Any) -> None:
    """The per-user limits are counted before a Stripe slot is taken, never while one is held, so a request
    past its allowance is told when to retry even while every slot is busy."""
    limits = {"checkouts_per_user_per_hour": 1, "portal_sessions_per_user_per_hour": 1}
    h = make_harness({"billing_max_concurrent_stripe_calls": 1, **limits})
    holder, user = h.login("user_a"), h.login("user_b")
    for headers in (holder, user):
        assert start_checkout(h, headers).status_code == 200  # each user's one checkout, which creates the customer
    assert h.client.post("/api/v1/billing/portal", headers=user).status_code == 200  # user_b's one portal session
    original = InMemoryBillingProvider.create_portal_session
    entered, release = threading.Event(), threading.Event()

    def held_portal(self: InMemoryBillingProvider, *, customer_id: str, return_url: str) -> str:
        entered.set()
        release.wait(10)  # Stripe answers only once the test lets it
        return original(self, customer_id=customer_id, return_url=return_url)

    h.billing.create_portal_session = held_portal.__get__(h.billing)  # type: ignore[method-assign]
    held: list[int] = []
    holding = threading.Thread(
        target=lambda: held.append(h.client.post("/api/v1/billing/portal", headers=holder).status_code)
    )
    holding.start()
    try:
        assert entered.wait(10)  # user_a's portal session holds the only slot
        for r in (start_checkout(h, user), h.client.post("/api/v1/billing/portal", headers=user)):
            assert (r.status_code, r.json()["code"]) == (429, "RATE_LIMITED")
            assert int(r.headers["retry-after"]) >= 1
    finally:
        release.set()
        holding.join(10)
    assert held == [200]


# -- One subscription at a time (DEC-013, option B) ---------------------------


def user_id_of(h: Harness, headers: dict[str, str]) -> uuid.UUID:
    return uuid.UUID(h.client.get("/api/v1/me", headers=headers).json()["user_id"])


def stored_session(h: Harness, user: uuid.UUID) -> str | None:
    rows = superuser_query(
        h.database_url, "SELECT stripe_session_id FROM app.billing_checkout_sessions WHERE user_id = %s", (user,)
    )
    return str(rows[0][0]) if rows else None


def checkout_url(r: Any) -> str:
    assert r.status_code == 200, r.text
    return str(r.json()["url"])


def session_of(url: str) -> str:
    return url.rsplit("session_id=", 1)[1]


UNKNOWN_PRICE = {"plan_key": PlanKey.UNKNOWN, "price_id": "price_replaced"}


@pytest.mark.parametrize(
    ("status", "extra", "state", "fragment"),
    [
        (SubscriptionStatus.UNPAID, {}, "payment_required", "needs a payment"),
        (SubscriptionStatus.PAUSED, {}, "payment_required", "needs a payment"),
        (SubscriptionStatus.PAST_DUE, UNKNOWN_PRICE, "payment_required", "needs a payment"),
        (SubscriptionStatus.ACTIVE, UNKNOWN_PRICE, "subscription_exists", "already have a subscription"),
        (SubscriptionStatus.TRIALING, {}, "subscription_exists", "already have a subscription"),
        (SubscriptionStatus.INCOMPLETE, {}, "payment_pending", "still being confirmed"),
        (SubscriptionStatus.ACTIVE, {}, "already_pro", "You already have Pro"),
        (SubscriptionStatus.PAST_DUE, {}, "already_pro", "You already have Pro"),
    ],
    ids=lambda v: v.value if isinstance(v, SubscriptionStatus) else None,
)
def test_checkout_is_refused_with_no_stripe_call_while_a_subscription_is_not_over(
    harness: Harness, status: SubscriptionStatus, extra: dict[str, Any], state: str, fragment: str
) -> None:
    headers = harness.login("user_a")
    start_checkout(harness, headers)
    customer = customer_of(harness, "user_a")
    harness.billing.set_subscriptions(customer, [snapshot(customer, status, **extra)])
    sync(harness, headers)
    assert harness.client.get("/api/v1/billing/status", headers=headers).json()["checkout_blocked"] == state
    calls = list(harness.billing.calls)

    r = start_checkout(harness, headers)

    assert (r.status_code, r.json()["code"]) == (409, "CONFLICT")  # the page reloads on CONFLICT (BILL-07)
    assert fragment in r.json()["detail"]
    assert harness.billing.calls == calls  # refused from the stored state: nothing was asked of Stripe


@pytest.mark.parametrize("status", [SubscriptionStatus.CANCELED, SubscriptionStatus.INCOMPLETE_EXPIRED])
def test_checkout_is_offered_again_once_the_subscription_is_over(harness: Harness, status: SubscriptionStatus) -> None:
    headers = harness.login("user_a")
    start_checkout(harness, headers)
    customer = customer_of(harness, "user_a")
    harness.billing.set_subscriptions(customer, [snapshot(customer, status)])
    sync(harness, headers)
    assert harness.client.get("/api/v1/billing/status", headers=headers).json()["checkout_blocked"] is None
    assert start_checkout(harness, headers).status_code == 200


def test_status_says_checkout_is_allowed_for_a_free_user_and_blocked_for_pro(harness: Harness) -> None:
    headers = harness.login("user_a")
    assert harness.client.get("/api/v1/billing/status", headers=headers).json()["checkout_blocked"] is None
    make_pro(harness, "user_b")
    assert (
        harness.client.get("/api/v1/billing/status", headers=harness.login("user_b")).json()["checkout_blocked"]
        == "already_pro"
    )


def test_a_first_checkout_makes_no_call_about_earlier_sessions(harness: Harness) -> None:
    headers = harness.login("user_a")
    assert start_checkout(harness, headers).status_code == 200
    assert harness.billing.calls == ["create_customer", "create_checkout_session"]
    assert stored_session(harness, user_id_of(harness, headers)) == harness.billing.open_sessions()[0]


def test_two_tabs_leave_one_session_that_can_be_completed(harness: Harness) -> None:
    """The earlier session is expired at Stripe before the next one is created, and the next one is remembered."""
    headers = harness.login("user_a")
    user = user_id_of(harness, headers)
    tab_one = session_of(checkout_url(start_checkout(harness, headers, plan="pro_monthly")))
    assert harness.billing.open_sessions(user) == [tab_one]

    tab_two = session_of(checkout_url(start_checkout(harness, headers, plan="pro_yearly")))

    assert tab_two != tab_one
    assert harness.billing.open_sessions(user) == [tab_two]
    assert stored_session(harness, user) == tab_two
    calls = harness.billing.calls
    # Closed first, then Stripe's own subscriptions read, and only then the new session.
    assert calls[calls.index("expire_checkout_session") :] == [
        "expire_checkout_session",
        "list_customer_subscriptions",
        "create_checkout_session",
    ]


def test_a_session_completed_in_one_tab_stops_a_second_checkout_before_the_webhook_lands(harness: Harness) -> None:
    """The slow-webhook path: the local record still says Free, but Stripe already holds the subscription."""
    headers = harness.login("user_a")
    user = user_id_of(harness, headers)
    session = session_of(checkout_url(start_checkout(harness, headers)))
    customer = customer_of(harness, "user_a")
    harness.billing.complete_session(session, snapshot(customer, SubscriptionStatus.ACTIVE))
    assert harness.client.get("/api/v1/billing/status", headers=headers).json()["plan"] == "free"
    created = len(harness.billing.checkout_requests)

    r = start_checkout(harness, headers, plan="pro_yearly")

    assert (r.status_code, r.json()["code"]) == (409, "CONFLICT")
    assert "You already have Pro" in r.json()["detail"]
    assert len(harness.billing.checkout_requests) == created  # no second session was created
    assert harness.billing.open_sessions(user) == []
    # The page asks the server to re-read, and then shows Pro.
    assert sync(harness, headers).json()["plan"] == "pro"


def test_other_users_sessions_are_left_alone(harness: Harness) -> None:
    first, second = harness.login("user_a"), harness.login("user_b")
    others = session_of(checkout_url(start_checkout(harness, second)))
    start_checkout(harness, first)
    start_checkout(harness, first)
    assert others in harness.billing.open_sessions()
    assert stored_session(harness, user_id_of(harness, second)) == others


def test_the_same_request_again_returns_its_session_instead_of_closing_it(harness: Harness) -> None:
    """Idempotency-Key: a retry of one request is not a second checkout."""
    headers = {**harness.login("user_a"), "Idempotency-Key": "retry-me-0001"}
    body = {"plan": "pro_monthly"}
    first = harness.client.post("/api/v1/billing/checkout", json=body, headers=headers)
    second = harness.client.post("/api/v1/billing/checkout", json=body, headers=headers)
    assert checkout_url(first) == checkout_url(second)
    assert len(harness.billing.open_sessions()) == 1
    assert "expire_checkout_session" not in harness.billing.calls
    assert harness.billing.calls.count("create_checkout_session") == 1


def test_a_retry_after_its_session_was_closed_starts_a_new_one(harness: Harness) -> None:
    headers = {**harness.login("user_a"), "Idempotency-Key": "retry-me-0002"}
    body = {"plan": "pro_monthly"}
    first = session_of(checkout_url(harness.client.post("/api/v1/billing/checkout", json=body, headers=headers)))
    harness.billing.expire_checkout_session(first)  # for example, Stripe expired it
    second = harness.client.post("/api/v1/billing/checkout", json=body, headers=headers)
    assert second.status_code == 200
    assert harness.billing.open_sessions() == [session_of(checkout_url(second))]


def test_when_stripe_cannot_close_the_earlier_session_no_second_one_is_created(harness: Harness) -> None:
    headers = harness.login("user_a")
    user = user_id_of(harness, headers)
    first = session_of(checkout_url(start_checkout(harness, headers)))
    created = len(harness.billing.checkout_requests)
    original = InMemoryBillingProvider.expire_checkout_session

    def down(self: InMemoryBillingProvider, session_id: str) -> None:
        raise DependencyUnavailableError("Billing is temporarily unavailable.")

    harness.billing.expire_checkout_session = down.__get__(harness.billing)  # type: ignore[method-assign]
    r = start_checkout(harness, headers, plan="pro_yearly")

    assert (r.status_code, r.json()["code"]) == (503, "DEPENDENCY_UNAVAILABLE")  # retryable
    assert "could not be closed" in r.json()["detail"]
    assert len(harness.billing.checkout_requests) == created
    assert harness.billing.open_sessions(user) == [first]
    assert stored_session(harness, user) == first  # still remembered, so the retry closes it

    harness.billing.expire_checkout_session = original.__get__(harness.billing)  # type: ignore[method-assign]
    second = session_of(checkout_url(start_checkout(harness, headers, plan="pro_yearly")))
    assert harness.billing.open_sessions(user) == [second]


def test_a_session_that_has_expired_by_itself_is_not_expired_again(harness: Harness) -> None:
    harness.billing.session_expires_at = datetime(2026, 5, 31, tzinfo=UTC)  # before the test clock
    headers = harness.login("user_a")
    start_checkout(harness, headers)
    harness.billing.session_expires_at = datetime(2100, 1, 1, tzinfo=UTC)
    start_checkout(harness, headers)
    assert "expire_checkout_session" not in harness.billing.calls
    assert len(harness.billing.open_sessions()) == 2  # the fake does not expire by itself; the point is no call


def test_a_session_that_cannot_be_remembered_is_closed_rather_than_left_completable(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.modules.billing.adapters.repository import PgBillingRepository

    def broken(self: PgBillingRepository, user_id: uuid.UUID, checkout: Any) -> None:
        raise RuntimeError("database down")

    monkeypatch.setattr(PgBillingRepository, "save_open_checkout", broken)
    r = start_checkout(harness, harness.login("user_a"))
    assert r.status_code == 500
    assert harness.billing.open_sessions() == []


def test_two_requests_saving_at_once_leave_only_the_later_session_open(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both read 'no session', both create one; whichever saves second replaces and closes the other."""
    headers = harness.login("user_a")
    user = user_id_of(harness, headers)
    start_checkout(harness, headers)  # a customer exists, so the next request goes through the full flow
    rival = harness.billing.create_checkout_session(
        customer_id=customer_of(harness, "user_a"),
        price_id=PRICE_MONTHLY,
        user_id=user,
        success_url="https://app.test/s",
        cancel_url="https://app.test/c",
        idempotency_key="rival-0001",
    )
    original = InMemoryBillingProvider.create_checkout_session

    def rival_saves_first(self: InMemoryBillingProvider, **kw: Any) -> Any:
        created = original(self, **kw)
        superuser_query(
            harness.database_url,
            "INSERT INTO app.billing_checkout_sessions (user_id, stripe_session_id, idempotency_key) "
            "VALUES (%s, %s, 'rival-0001') ON CONFLICT (user_id) DO UPDATE SET stripe_session_id = EXCLUDED.stripe_session_id",
            (user, rival.session_id),
        )
        return created

    monkeypatch.setattr(InMemoryBillingProvider, "create_checkout_session", rival_saves_first)
    mine = session_of(checkout_url(start_checkout(harness, headers, plan="pro_yearly")))
    assert harness.billing.open_sessions(user) == [mine]
    assert stored_session(harness, user) == mine


# -- Re-reading Stripe on request ---------------------------------------------


def sync(h: Harness, headers: dict[str, str], **kw: Any) -> Any:
    return h.client.post("/api/v1/billing/sync", headers=headers, **kw)


def plan_changes(h: Harness) -> list[Any]:
    rows = superuser_query(
        h.database_url,
        "SELECT payload->>'plan' FROM app.outbox WHERE event_type = 'billing.entitlement_changed' ORDER BY occurred_at",
    )
    return [r[0] for r in rows]


def test_a_user_sync_grants_pro_when_the_webhook_has_not_landed(harness: Harness) -> None:
    """BILL-06: Stripe's own guidance is to fulfil from the landing page too; the server reads Stripe itself."""
    headers = harness.login("user_a")
    start_checkout(harness, headers)
    customer = customer_of(harness, "user_a")
    harness.billing.set_subscriptions(customer, [snapshot(customer, SubscriptionStatus.ACTIVE)])
    assert harness.client.get("/api/v1/billing/status", headers=headers).json()["plan"] == "free"

    r = sync(harness, headers)

    assert r.status_code == 200
    assert (r.json()["plan"], r.json()["state"], r.json()["plan_key"]) == ("pro", "active", "pro_monthly")
    assert harness.client.get("/api/v1/billing/status", headers=headers).json()["plan"] == "pro"
    assert plan_changes(harness) == ["pro"]


def test_a_user_sync_follows_a_cancellation_made_in_the_portal(harness: Harness) -> None:
    customer = make_pro(harness, "user_a")
    harness.billing.set_subscriptions(customer, [snapshot(customer, SubscriptionStatus.CANCELED)])
    r = sync(harness, harness.login("user_a"))
    assert (r.json()["plan"], r.json()["state"]) == ("free", "canceled")


def test_a_user_with_no_billing_account_gets_the_status_and_no_stripe_call(harness: Harness) -> None:
    r = sync(harness, harness.login("user_a"))
    assert (r.status_code, r.json()["plan"], r.json()["state"]) == (200, "free", "none")
    assert harness.billing.calls == []


def test_a_forged_success_url_plus_sync_grants_nothing_without_a_stripe_subscription(harness: Harness) -> None:
    """NT-10: neither the session ID in the URL nor a body sent with the sync is read."""
    headers = harness.login("user_a")
    start_checkout(harness, headers)
    r = sync(harness, headers, json={"session_id": "cs_forged", "plan": "pro"})
    assert (r.status_code, r.json()["plan"]) == (200, "free")
    assert harness.client.get("/api/v1/pro/insight", headers=headers).status_code == 403


def test_a_user_sync_reads_only_the_callers_own_customer(harness: Harness) -> None:
    other = make_pro(harness, "user_b")
    harness.billing.set_subscriptions(other, [snapshot(other, SubscriptionStatus.CANCELED)])
    headers = harness.login("user_a")
    start_checkout(harness, headers)
    sync(harness, headers)
    assert harness.client.get("/api/v1/billing/status", headers=harness.login("user_b")).json()["plan"] == "pro"


def test_user_syncs_are_rate_limited_before_any_stripe_call(make_harness: Any) -> None:
    h = make_harness({"billing_syncs_per_user_per_hour": 2})
    headers = h.login("user_a")
    start_checkout(h, headers)
    reads = h.billing.calls.count("list_customer_subscriptions")
    assert [sync(h, headers).status_code for _ in range(4)] == [200, 200, 429, 429]
    assert h.billing.calls.count("list_customer_subscriptions") == reads + 2
    assert int(sync(h, headers).headers["retry-after"]) >= 1
    assert sync(h, h.login("user_b")).status_code == 200  # the allowance is the user's own


class HeldRead:
    """Makes the first Stripe subscription read of one customer wait until the test lets it finish."""

    def __init__(self, h: Harness, customer_id: str) -> None:
        self.entered, self.release = threading.Event(), threading.Event()
        original = InMemoryBillingProvider.list_customer_subscriptions

        def read(provider: InMemoryBillingProvider, customer: str) -> list[SubscriptionSnapshot]:
            if customer == customer_id and not self.entered.is_set():
                self.entered.set()
                self.release.wait(10)
            return original(provider, customer)

        h.billing.list_customer_subscriptions = read.__get__(h.billing)  # type: ignore[method-assign]


def in_thread(call: Any, results: list[Any]) -> threading.Thread:
    thread = threading.Thread(target=lambda: results.append(call().status_code))
    thread.start()
    return thread


def test_user_syncs_never_take_the_last_synchronization_slot(make_harness: Any) -> None:
    """Webhooks and reconciliation keep a slot however many users ask: users cannot make webhooks 503.

    The default worker pool gives two slots, so one is the most users may hold, and the next user is
    refused at once instead of queuing. A webhook for another customer is still processed meanwhile.
    """
    h = make_harness()
    first, second, third = h.login("user_a"), h.login("user_b"), h.login("user_c")
    for headers in (first, second, third):
        start_checkout(h, headers)
    held = HeldRead(h, customer_of(h, "user_a"))
    holding: list[int] = []
    thread = in_thread(lambda: sync(h, first), holding)
    try:
        assert held.entered.wait(10)  # user_a's re-read holds a slot, in Stripe
        started = time.monotonic()
        refused = sync(h, second)
        assert (refused.status_code, refused.json()["code"]) == (503, "DEPENDENCY_UNAVAILABLE")
        assert time.monotonic() - started < 0.5  # refused at once: waiting would hold a request thread too
        # The slot users cannot take serves a webhook meanwhile.
        customer = customer_of(h, "user_c")
        h.billing.set_subscriptions(customer, [snapshot(customer, SubscriptionStatus.ACTIVE)])
        delivered = send_webhook(h, event_payload(customer))
        assert (delivered.status_code, delivered.json()["status"]) == (200, "processed")
    finally:
        held.release.set()
        thread.join(10)
    assert holding == [200]
    assert sync(h, second).status_code == 200  # the slot is given back


def test_with_two_worker_connections_there_is_one_slot_and_users_are_always_refused(make_harness: Any) -> None:
    h = make_harness({"database_worker_pool_max_size": 2})
    headers = h.login("user_a")
    start_checkout(h, headers)
    reads = h.billing.calls.count("list_customer_subscriptions")
    r = sync(h, headers)
    assert (r.status_code, r.json()["code"]) == (503, "DEPENDENCY_UNAVAILABLE")
    assert h.billing.calls.count("list_customer_subscriptions") == reads  # nothing was asked of Stripe
    customer = customer_of(h, "user_a")
    h.billing.set_subscriptions(customer, [snapshot(customer, SubscriptionStatus.ACTIVE)])
    assert send_webhook(h, event_payload(customer)).json()["status"] == "processed"  # the one slot is the webhook's


def test_a_user_sync_counts_against_the_per_instance_bound_that_checkout_and_the_portal_share(
    make_harness: Any,
) -> None:
    h = make_harness({"billing_max_concurrent_stripe_calls": 1, "database_worker_pool_max_size": 4})
    first, second = h.login("user_a"), h.login("user_b")
    for headers in (first, second):
        start_checkout(h, headers)
    held = HeldRead(h, customer_of(h, "user_a"))
    holding: list[int] = []
    thread = in_thread(lambda: sync(h, first), holding)
    try:
        assert held.entered.wait(10)
        # Synchronization slots remain, but the one Stripe call this instance allows is in use.
        for r in (sync(h, second), h.client.post("/api/v1/billing/portal", headers=second), start_checkout(h, second)):
            assert (r.status_code, r.json()["code"]) == (503, "DEPENDENCY_UNAVAILABLE")
    finally:
        held.release.set()
        thread.join(10)
    assert holding == [200]
    assert sync(h, second).status_code == 200


def sync_buckets(h: Harness) -> list[str]:
    rows = superuser_query(
        h.database_url,
        "SELECT DISTINCT bucket_key FROM app.rate_limit_counters WHERE bucket_key LIKE 'billing-sync:%%' ORDER BY 1",
    )
    return [r[0] for r in rows]


def test_no_user_sync_limit_is_shared_so_a_crowd_of_accounts_cannot_lock_others_out(make_harness: Any) -> None:
    """Whatever a sync is refused for, and whether or not the user has a billing account, it counts only
    against the caller's own allowance: a bucket shared by all users is one any crowd of free accounts could fill."""
    h = make_harness({"billing_syncs_per_user_per_hour": 1, "database_worker_pool_max_size": 2})
    with_account = [h.login("user_a"), h.login("user_b")]
    without_account = [h.login(f"crowd_{i}") for i in range(3)]
    for headers in with_account:
        start_checkout(h, headers)
    # Two worker connections leave one slot, which is the webhooks': a sync from a user with an account is refused.
    answers = [sync(h, headers).status_code for headers in [*with_account, *without_account]]
    assert answers == [503, 503, 200, 200, 200]
    assert sync(h, without_account[0]).status_code == 429  # over their own allowance
    users = {r[0] for r in superuser_query(h.database_url, "SELECT id::text FROM app.users")}
    assert set(sync_buckets(h)) == {f"billing-sync:{user}" for user in users}  # one per user, none shared
    legit = h.login("legit")
    assert sync(h, legit).status_code == 200  # none of the above counted against them
    assert sync(h, legit).status_code == 429


def test_a_sync_and_a_webhook_for_the_same_customer_converge_and_record_the_plan_change_once(harness: Harness) -> None:
    headers = harness.login("user_a")
    start_checkout(harness, headers)
    customer = customer_of(harness, "user_a")
    harness.billing.set_subscriptions(customer, [snapshot(customer, SubscriptionStatus.ACTIVE)])
    results: list[int] = []
    gate = threading.Barrier(2)

    def run(call: Any) -> None:
        gate.wait(10)
        results.append(call().status_code)

    threads = [
        threading.Thread(target=run, args=(lambda: sync(harness, headers),)),
        threading.Thread(target=run, args=(lambda: send_webhook(harness, event_payload(customer)),)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
    assert results == [200, 200]
    assert plan_changes(harness) == ["pro"]  # whichever came second found nothing to change


def test_a_slow_user_re_read_cannot_overwrite_a_cancellation_that_landed_meanwhile(harness: Harness) -> None:
    """BILL-10, BILL-22: the user's own re-read takes the customer lock, like a webhook does.

    The re-read has read ``active`` from Stripe and is slow to store it. Stripe cancels, and the webhook for that
    arrives. With the lock the webhook queues behind the re-read and then reads the cancellation. Without it the
    webhook finishes first and the older read is stored over it: Pro would come back after a cancellation.
    """
    customer = make_pro(harness, "user_a")
    headers = harness.login("user_a")
    provider = harness.billing
    original = InMemoryBillingProvider.list_customer_subscriptions
    has_read, may_store = threading.Event(), threading.Event()

    def slow_first_read(self: InMemoryBillingProvider, customer_id: str) -> list[SubscriptionSnapshot]:
        seen = original(self, customer_id)  # Stripe still says active
        if not has_read.is_set():
            has_read.set()
            assert may_store.wait(30)
        return seen

    provider.list_customer_subscriptions = slow_first_read.__get__(provider)  # type: ignore[method-assign]
    answers: dict[str, Any] = {}
    delivered = threading.Event()

    def deliver() -> None:
        answers["webhook"] = send_webhook(harness, event_payload(customer, "customer.subscription.deleted"))
        delivered.set()

    resync = threading.Thread(target=lambda: answers.__setitem__("sync", sync(harness, headers)))
    webhook = threading.Thread(target=deliver)
    resync.start()
    assert has_read.wait(10)
    provider.set_subscriptions(customer, [snapshot(customer, SubscriptionStatus.CANCELED)])
    webhook.start()
    wait_for_lock_wait(harness.database_url, or_until=delivered)  # queued behind the re-read, or already finished
    may_store.set()
    resync.join(30)
    webhook.join(30)

    assert (answers["sync"].status_code, answers["webhook"].status_code) == (200, 200)
    plan = harness.client.get("/api/v1/billing/status", headers=headers).json()["plan"]
    assert plan == "free", "a re-read that Stripe's cancellation overtook was stored over it"


def test_a_sync_stripe_rejects_keeps_the_stored_state(harness: Harness, caplog: pytest.LogCaptureFixture) -> None:
    """Reconciliation owns a customer Stripe no longer lets us read; the user's click changes nothing."""
    customer = make_pro(harness, "user_a")
    harness.billing.delete_customer(customer)
    harness.billing.set_subscriptions(customer, [])  # the stored subscription cannot be read by ID either
    with caplog.at_level("WARNING"):
        r = sync(harness, harness.login("user_a"))
    assert (r.status_code, r.json()["plan"]) == (200, "pro")
    assert "billing_user_sync_rejected" in {rec.getMessage() for rec in caplog.records}


def test_a_stripe_outage_during_a_sync_is_503_and_changes_nothing(harness: Harness) -> None:
    make_pro(harness, "user_a")
    harness.billing.fail_with = lambda: DependencyUnavailableError("Billing is temporarily unavailable.")
    r = sync(harness, harness.login("user_a"))
    assert (r.status_code, r.json()["code"]) == (503, "DEPENDENCY_UNAVAILABLE")
    harness.billing.fail_with = None
    assert harness.client.get("/api/v1/billing/status", headers=harness.login("user_a")).json()["plan"] == "pro"


def test_sync_is_not_available_without_billing_or_in_an_organization(make_harness: Any) -> None:
    h = make_harness(billing_provider=None)
    r = sync(h, h.login("user_a"))
    assert (r.status_code, r.json()["code"]) == (503, "NOT_CONFIGURED")
    assert h.client.post("/api/v1/billing/sync").status_code == 401


# -- A customer Stripe deleted (BILL-04) --------------------------------------


def customer_gone(h: Harness, name: str = "user_a") -> tuple[dict[str, str], str]:
    """A user with a customer (made by a first checkout) that Stripe has since deleted."""
    headers = h.login(name)
    assert start_checkout(h, headers).status_code == 200
    customer = customer_of(h, name)
    h.billing.delete_customer(customer)
    return headers, customer


def flagged_events(h: Harness) -> int:
    rows = superuser_query(
        h.database_url, "SELECT count(*) FROM app.billing_events WHERE status = 'needs_reconciliation'"
    )
    return int(rows[0][0])


def test_a_user_whose_stripe_customer_was_deleted_gets_a_new_one_at_the_next_checkout(harness: Harness) -> None:
    """Before: every checkout was rejected (400), for good: the stored customer could not be replaced."""
    headers, old = customer_gone(harness)
    user = user_id_of(harness, headers)
    assert send_webhook(harness, event_payload(old, event_id="evt_before")).json()["status"] == "needs_reconciliation"
    assert flagged_events(harness) == 1

    r = start_checkout(harness, headers, plan="pro_yearly")

    assert r.status_code == 200, r.text
    new = customer_of(harness, "user_a")
    assert new != old
    assert harness.billing.customer_requests[-1] == {"user_id": user, "replaces": old}
    assert harness.billing.checkout_requests[-1]["customer_id"] == new  # the session is the new customer's
    assert r.json()["url"] in {
        harness.billing.open_checkout_session_url(s) for s in harness.billing.open_sessions(user)
    }
    assert flagged_events(harness) == 0  # events about the deleted customer cannot be reconciled any more
    assert superuser_query(
        harness.database_url, "SELECT reconciled_at FROM app.billing_customers WHERE user_id = %s", (user,)
    ) == [(harness.clock.now(),)]  # the new link joins the back of the reconciliation rotation
    # The new customer works like any other: its subscription grants Pro.
    harness.billing.set_subscriptions(new, [snapshot(new, SubscriptionStatus.ACTIVE)])
    assert send_webhook(harness, event_payload(new)).json()["status"] == "processed"
    assert harness.client.get("/api/v1/billing/status", headers=headers).json()["plan"] == "pro"


def test_the_subscriptions_that_ended_under_the_deleted_customer_stay_as_history(harness: Harness) -> None:
    headers = harness.login("user_a")
    old = make_pro(harness, "user_a")
    harness.billing.set_subscriptions(old, [snapshot(old, SubscriptionStatus.CANCELED)])
    assert sync(harness, headers).json()["state"] == "canceled"
    harness.billing.delete_customer(old)

    assert start_checkout(harness, headers).status_code == 200

    new = customer_of(harness, "user_a")
    assert new != old
    assert superuser_query(harness.database_url, "SELECT stripe_customer_id, status FROM app.subscriptions") == [
        (old, "canceled")
    ]
    # Nothing is reported about it every day: the old customer is out of the rotation, and the history is not
    # compared with the new customer's subscriptions.
    report = harness.container.billing.service.reconcile(user_id=None, apply=False)
    assert (report.customers_checked, report.divergences, report.rejected_customers) == (1, [], [])


def test_two_checkouts_at_once_after_a_deletion_link_one_new_customer(harness: Harness) -> None:
    """Both replace the customer with the one Stripe makes for the same key; the later link finds it already made."""
    headers, old = customer_gone(harness)
    both_created = threading.Barrier(2)
    original = InMemoryBillingProvider.create_customer

    def meet_then_create(self: InMemoryBillingProvider, user_id: uuid.UUID, *, replaces: str | None = None) -> str:
        created = original(self, user_id, replaces=replaces)
        both_created.wait(10)  # neither has linked yet
        return created

    harness.billing.create_customer = meet_then_create.__get__(harness.billing)  # type: ignore[method-assign]
    answers: list[int] = []
    threads = [
        threading.Thread(target=lambda: answers.append(start_checkout(harness, headers).status_code)) for _ in "ab"
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)

    assert answers == [200, 200]
    assert customer_of(harness, "user_a") != old
    assert superuser_query(harness.database_url, "SELECT count(*) FROM app.billing_customers") == [(1,)]
    assert len(harness.billing.open_sessions()) == 1  # DEC-013: the later session closed the earlier one


def test_a_link_that_another_request_already_replaced_is_kept(harness: Harness) -> None:
    headers, old = customer_gone(harness)
    user = user_id_of(harness, headers)
    transactions = harness.container.kernel.transactions(PgBillingRepository)
    with transactions.as_system() as t:
        assert t.repo.relink_customer(user, old, "cus_first", harness.clock.now()) == "cus_first"
    with transactions.as_system() as t:
        assert t.repo.relink_customer(user, old, "cus_second", harness.clock.now()) == "cus_first"
    assert customer_of(harness, "user_a") == "cus_first"


def test_a_customer_stripe_still_has_is_never_replaced(harness: Harness) -> None:
    """A rejection that is not a deleted customer stays a rejection: replacing a live customer could orphan its subscription."""
    headers = harness.login("user_a")
    assert start_checkout(harness, headers).status_code == 200
    old = customer_of(harness, "user_a")

    def rejected(customer_id: str) -> list[SubscriptionSnapshot]:
        raise InvalidRequestError("The billing request was rejected.")

    harness.billing.list_customer_subscriptions = rejected  # type: ignore[method-assign]
    r = start_checkout(harness, headers)

    assert (r.status_code, r.json()["code"]) == (400, "INVALID_REQUEST")
    assert customer_of(harness, "user_a") == old
    assert len(harness.billing.customer_requests) == 1  # only the first checkout made a customer


def test_a_deleted_customer_with_a_subscription_that_is_not_over_is_never_replaced(
    harness: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    """Keys of another Stripe account (a new sandbox, the database kept): nothing there can end the stored subscription."""
    old = make_pro(harness, "user_a")
    headers = harness.login("user_a")
    harness.billing.delete_customer(old)
    harness.billing.set_subscriptions(old, [])  # the subscription cannot be read by ID either
    calls = list(harness.billing.calls)

    with caplog.at_level("ERROR"):
        r = harness.client.post("/api/v1/billing/portal", headers=headers)

    assert (r.status_code, r.json()["code"]) == (409, "CONFLICT")
    assert "contact support" in r.json()["detail"]
    [record] = [rec for rec in caplog.records if rec.getMessage() == "billing_customer_gone_with_subscription"]
    assert (
        (record.levelname, record.stripe_customer_id, record.stripe_subscription_ids)
        == (  # type: ignore[attr-defined]
            "ERROR",
            old,
            f"sub_{old[4:]}",
        )
    )
    assert customer_of(harness, "user_a") == old
    assert "create_customer" not in harness.billing.calls[len(calls) :]


def test_a_subscription_that_lands_while_the_customer_is_replaced_stops_the_replacement(
    harness: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    """Read again under the customer's lock: a webhook stored a live subscription after the first look."""
    headers, old = customer_gone(harness)
    original = InMemoryBillingProvider.create_customer

    def create_then_store_a_live_subscription(
        self: InMemoryBillingProvider, user_id: uuid.UUID, *, replaces: str | None = None
    ) -> str:
        created = original(self, user_id, replaces=replaces)
        superuser_query(
            harness.database_url,
            "INSERT INTO app.subscriptions (stripe_subscription_id, user_id, stripe_customer_id, status, plan_key) "
            "VALUES ('sub_landed', %s, %s, 'active', 'pro_monthly')",
            (user_id, replaces),
        )
        return created

    harness.billing.create_customer = create_then_store_a_live_subscription.__get__(harness.billing)  # type: ignore[method-assign]
    with caplog.at_level("ERROR"):
        r = start_checkout(harness, headers)

    assert (r.status_code, r.json()["code"]) == (409, "CONFLICT")
    assert customer_of(harness, "user_a") == old
    [record] = [rec for rec in caplog.records if rec.getMessage() == "billing_customer_gone_with_subscription"]
    assert record.stripe_subscription_ids == "sub_landed"  # type: ignore[attr-defined]


def test_the_portal_of_a_customer_stripe_deleted_says_there_is_nothing_to_manage(harness: Harness) -> None:
    headers = harness.login("user_a")
    old = make_pro(harness, "user_a")
    harness.billing.set_subscriptions(old, [snapshot(old, SubscriptionStatus.CANCELED)])
    assert sync(harness, headers).json()["state"] == "canceled"
    harness.billing.delete_customer(old)

    r = harness.client.post("/api/v1/billing/portal", headers=headers)

    assert (r.status_code, r.json()["code"]) == (409, "CONFLICT")
    assert "Start a new subscription" in r.json()["detail"]
    assert customer_of(harness, "user_a") == old  # the portal replaces nothing: the next checkout does


def test_a_portal_stripe_rejects_for_a_customer_it_has_stays_a_rejection(harness: Harness) -> None:
    headers = harness.login("user_a")
    make_pro(harness, "user_a")

    def rejected(*, customer_id: str, return_url: str) -> str:
        raise InvalidRequestError("The billing request was rejected.")

    harness.billing.create_portal_session = rejected  # type: ignore[method-assign]
    r = harness.client.post("/api/v1/billing/portal", headers=headers)
    assert (r.status_code, r.json()["code"]) == (400, "INVALID_REQUEST")


def test_only_the_worker_may_change_which_customer_a_user_has(harness: Harness) -> None:
    """The API role (requests) can read and create its own link, never move it; the worker moves it for a replacement."""

    def can_update(role: str) -> bool:
        rows = superuser_query(
            harness.database_url,
            "SELECT has_column_privilege(%s, 'app.billing_customers', 'stripe_customer_id', 'UPDATE')",
            (role,),
        )
        return bool(rows[0][0])

    assert (can_update("app_worker"), can_update("app_api")) == (True, False)
    assert superuser_query(
        harness.database_url, "SELECT has_column_privilege('app_worker', 'app.billing_customers', 'user_id', 'UPDATE')"
    ) == [(False,)]  # only the customer ID, never the owner of the link


# -- Entitlement and presentation ---------------------------------------------


def test_success_redirect_alone_does_not_grant_pro(harness: Harness) -> None:
    headers = harness.login("user_a")
    start_checkout(harness, headers)
    assert harness.client.get("/api/v1/billing/status", headers=headers).json()["plan"] == "free"
    assert harness.client.get("/api/v1/pro/insight", headers=headers).status_code == 403


def test_webhook_sync_grants_pro_and_status_reflects_it(harness: Harness) -> None:
    make_pro(harness, "user_a")
    headers = harness.login("user_a")
    status = harness.client.get("/api/v1/billing/status", headers=headers).json()
    assert status["plan"] == "pro"
    assert status["state"] == "active"
    assert status["plan_key"] == "pro_monthly"
    assert status["available_plans"] == ["pro_monthly", "pro_yearly"]
    assert harness.client.get("/api/v1/pro/insight", headers=headers).status_code == 200


def test_a_feature_the_plan_does_not_list_is_refused_even_to_a_pro_user(harness: Harness) -> None:
    """Gating asks for a feature, not for the plan: a name no plan lists fails closed (a typo is never a free pass)."""
    make_pro(harness, "user_a")
    ctx = TenantContext(user_id_of(harness, harness.login("user_a")), "user_a", ContextKind.PERSONAL)
    service = harness.container.billing.service
    service.require_feature(ctx, PRO_INSIGHT)
    with pytest.raises(EntitlementRequiredError):
        service.require_feature(ctx, "no_such_feature")


@pytest.mark.parametrize(
    ("status", "plan", "state"),
    [
        (SubscriptionStatus.PAST_DUE, "pro", "past_due"),
        (SubscriptionStatus.UNPAID, "free", "payment_required"),
        (SubscriptionStatus.INCOMPLETE, "free", "processing"),
        (SubscriptionStatus.CANCELED, "free", "canceled"),
    ],
)
def test_status_transitions_via_webhooks(harness: Harness, status: SubscriptionStatus, plan: str, state: str) -> None:
    customer = make_pro(harness, "user_a")
    harness.billing.set_subscriptions(customer, [snapshot(customer, status)])
    assert send_webhook(harness, event_payload(customer)).json()["status"] == "processed"
    body = harness.client.get("/api/v1/billing/status", headers=harness.login("user_a")).json()
    assert (body["plan"], body["state"]) == (plan, state)


@pytest.mark.parametrize(
    ("status", "state", "blocked"),
    [
        (SubscriptionStatus.ACTIVE, "unrecognized", "subscription_exists"),
        (SubscriptionStatus.PAST_DUE, "payment_required", "payment_required"),
    ],
)
def test_a_paying_subscription_no_plan_matches_is_not_shown_as_an_ended_plan(
    harness: Harness, status: SubscriptionStatus, state: str, blocked: str
) -> None:
    """A price of another product grants no Pro (BILL-03) but the user pays: the page must not say the plan ended."""
    customer = make_pro(harness, "user_a")
    harness.billing.set_subscriptions(
        customer, [snapshot(customer, status, plan_key=PlanKey.UNKNOWN, price_id="price_other_product")]
    )
    assert send_webhook(harness, event_payload(customer)).json()["status"] == "processed"

    body = harness.client.get("/api/v1/billing/status", headers=harness.login("user_a")).json()

    assert (body["plan"], body["state"], body["checkout_blocked"]) == ("free", state, blocked)
    assert harness.client.get("/api/v1/pro/insight", headers=harness.login("user_a")).status_code == 403


def test_end_of_period_cancellation_keeps_pro_until_period_end(harness: Harness) -> None:
    customer = make_pro(harness, "user_a")
    harness.billing.set_subscriptions(
        customer, [snapshot(customer, SubscriptionStatus.ACTIVE, cancel_at_period_end=True)]
    )
    send_webhook(harness, event_payload(customer))
    headers = harness.login("user_a")
    body = harness.client.get("/api/v1/billing/status", headers=headers).json()
    assert body["state"] == "canceling"
    assert body["cancels_at"] is not None
    harness.clock.current = FUTURE + timedelta(seconds=1)
    assert harness.client.get("/api/v1/billing/status", headers=headers).json()["plan"] == "free"


def test_reactivation_restores_active_state(harness: Harness) -> None:
    customer = make_pro(harness, "user_a")
    harness.billing.set_subscriptions(
        customer, [snapshot(customer, SubscriptionStatus.ACTIVE, cancel_at_period_end=True)]
    )
    send_webhook(harness, event_payload(customer))
    harness.billing.set_subscriptions(customer, [snapshot(customer, SubscriptionStatus.ACTIVE)])
    send_webhook(harness, event_payload(customer))
    assert harness.client.get("/api/v1/billing/status", headers=harness.login("user_a")).json()["state"] == "active"


SWITCHABLE = {
    "pro_monthly": {"plan_key": PlanKey.PRO_MONTHLY, "price_id": PRICE_MONTHLY, "current_period_end": FUTURE},
    "pro_yearly": {
        "plan_key": PlanKey.PRO_YEARLY,
        "price_id": PRICE_YEARLY,
        "current_period_end": FUTURE + timedelta(days=365),
    },
}


@pytest.mark.parametrize(("start", "end"), [("pro_monthly", "pro_yearly"), ("pro_yearly", "pro_monthly")])
def test_switching_between_monthly_and_yearly_follows_stripe_and_never_interrupts_pro(
    harness: Harness, start: str, end: str
) -> None:
    """BILL-15: the Customer Portal switches the price; entitlement follows the synchronized state, Pro throughout."""
    customer = make_pro(harness, "user_a")
    headers = harness.login("user_a")
    harness.billing.set_subscriptions(customer, [snapshot(customer, SubscriptionStatus.ACTIVE, **SWITCHABLE[start])])
    send_webhook(harness, event_payload(customer))
    assert harness.client.get("/api/v1/billing/status", headers=headers).json()["plan_key"] == start

    harness.billing.set_subscriptions(customer, [snapshot(customer, SubscriptionStatus.ACTIVE, **SWITCHABLE[end])])
    updated = send_webhook(harness, event_payload(customer, "customer.subscription.updated"))
    assert updated.json()["status"] == "processed"

    body = harness.client.get("/api/v1/billing/status", headers=headers).json()
    assert (body["plan"], body["state"], body["plan_key"]) == ("pro", "active", end)
    assert datetime.fromisoformat(body["current_period_end"]) == SWITCHABLE[end]["current_period_end"]
    assert harness.client.get("/api/v1/pro/insight", headers=headers).status_code == 200
    assert plan_changes(harness) == ["pro"]  # one grant, and no "free" in between


def test_a_failed_renewal_keeps_pro_and_the_payment_that_follows_clears_it_with_one_plan_change(
    harness: Harness,
) -> None:
    """BILL-18, DEC-010: past_due keeps Pro and asks for a payment method; invoice.paid recovers, with no second event."""
    customer = make_pro(harness, "user_a")
    headers = harness.login("user_a")

    harness.billing.set_subscriptions(customer, [snapshot(customer, SubscriptionStatus.PAST_DUE)])
    assert send_webhook(harness, event_payload(customer, "invoice.payment_failed")).json()["status"] == "processed"
    failed = harness.client.get("/api/v1/billing/status", headers=headers).json()
    assert (failed["plan"], failed["state"]) == ("pro", "past_due")

    harness.billing.set_subscriptions(customer, [snapshot(customer, SubscriptionStatus.ACTIVE)])
    assert send_webhook(harness, event_payload(customer, "invoice.paid")).json()["status"] == "processed"
    recovered = harness.client.get("/api/v1/billing/status", headers=headers).json()
    assert (recovered["plan"], recovered["state"], recovered["checkout_blocked"]) == ("pro", "active", "already_pro")
    assert plan_changes(harness) == ["pro"]  # Pro never lapsed, so the plan changed once, at the first grant


# -- Webhook trust model ------------------------------------------------------


def test_forged_webhook_rejected_without_side_effects(harness: Harness, caplog: pytest.LogCaptureFixture) -> None:
    customer = make_pro(harness, "user_a")
    harness.billing.set_subscriptions(customer, [snapshot(customer, SubscriptionStatus.CANCELED)])
    payload = event_payload(customer, event_id="evt_forged_canary")
    forged = sign(payload, secret="whsec_attacker_secret_00000000000")
    caplog.clear()
    with caplog.at_level("INFO"):
        for signature in ("", "t=1,v1=abc", forged):
            r = send_webhook(harness, payload, signature)
            assert r.status_code == 400
            assert r.json()["code"] == "WEBHOOK_SIGNATURE_INVALID"
    assert harness.client.get("/api/v1/billing/status", headers=harness.login("user_a")).json()["plan"] == "pro"

    # BILL-08: the refusal is logged, with its reason, and never with the body, its customer or the header.
    rejected = [r for r in caplog.records if r.getMessage() == "stripe_webhook_rejected"]
    assert [r.reason for r in rejected] == [
        "missing_signature",
        "SignatureVerificationError",
        "SignatureVerificationError",
    ]  # type: ignore[attr-defined]
    secrets = [customer, "evt_forged_canary", "t=1,v1=abc", forged, forged.split("v1=")[1], payload.decode()]
    leaked = [
        (record.getMessage(), name)
        for record in caplog.records
        for name, value in vars(record).items()
        if any(secret in str(value) for secret in secrets)
    ]
    assert leaked == []


def test_a_signed_thin_delivery_is_answered_with_the_payload_style_to_choose_and_writes_nothing(
    harness: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    """A Thin event destination (Stripe's recommended default) is not forgery: the response names the fix."""
    thin = json.dumps(
        {"id": "evt_thin", "object": "v2.core.event", "type": "v1.customer.subscription.updated", "created": "2026"}
    ).encode()
    with caplog.at_level("WARNING"):
        r = send_webhook(harness, thin)
    assert r.status_code == 400
    assert r.json()["code"] == "INVALID_REQUEST"
    assert "Snapshot" in r.json()["detail"]
    assert [rec.levelname for rec in caplog.records if rec.getMessage() == "stripe_webhook_unsupported_payload"] == [
        "ERROR"
    ]
    assert superuser_query(harness.database_url, "SELECT count(*) FROM app.billing_events") == [(0,)]


def test_replayed_old_signature_rejected(harness: Harness) -> None:
    payload = event_payload("cus_any")
    r = send_webhook(harness, payload, sign(payload, ts=int(time.time()) - 3600))
    assert r.status_code == 400


def test_tampered_body_rejected(harness: Harness) -> None:
    payload = event_payload("cus_victim")
    signature = sign(payload)
    r = send_webhook(harness, payload.replace(b"cus_victim", b"cus_attack"), signature)
    assert r.status_code == 400


def test_duplicate_event_processed_once(harness: Harness) -> None:
    customer = make_pro(harness, "user_a")
    payload = event_payload(customer, event_id="evt_dup")
    assert send_webhook(harness, payload).json()["status"] == "processed"
    calls_before = harness.billing.calls.count("list_customer_subscriptions")
    assert send_webhook(harness, payload).json()["status"] == "processed"
    assert harness.billing.calls.count("list_customer_subscriptions") == calls_before
    rows = superuser_query(
        harness.database_url, "SELECT attempts FROM app.billing_events WHERE stripe_event_id='evt_dup'"
    )
    assert rows == [(2,)]


def test_out_of_order_events_converge_on_current_stripe_state(harness: Harness) -> None:
    """An older 'active' event arriving after cancellation must not resurrect Pro."""
    customer = make_pro(harness, "user_a")
    harness.billing.set_subscriptions(customer, [snapshot(customer, SubscriptionStatus.CANCELED)])
    stale = {
        "id": f"sub_{customer[4:]}",
        "object": "subscription",
        "customer": customer,
        "status": "active",
        "items": {"data": [{"price": {"id": PRICE_MONTHLY}, "current_period_end": int(FUTURE.timestamp())}]},
    }
    # Applied as it is, the late payload would grant Pro.
    assert decide([to_snapshot(stale, harness.container.billing.catalog)], harness.clock.now()).plan is Plan.PRO
    newer = event_payload(customer, "customer.subscription.deleted")
    older = event_payload(customer, "customer.subscription.updated", obj=stale)
    send_webhook(harness, newer)
    send_webhook(harness, older)  # delivered late; triggers a re-read, not a blind apply
    assert harness.client.get("/api/v1/billing/status", headers=harness.login("user_a")).json()["plan"] == "free"


def test_irrelevant_event_ignored(harness: Harness) -> None:
    r = send_webhook(harness, event_payload("cus_any", "payment_intent.created"))
    assert r.json()["status"] == "ignored"


def test_unknown_customer_marked_for_reconciliation(harness: Harness) -> None:
    r = send_webhook(harness, event_payload("cus_unknown"))
    assert r.status_code == 200
    assert r.json()["status"] == "needs_reconciliation"


def test_provider_failure_during_webhook_returns_5xx_for_retry(harness: Harness) -> None:
    customer = make_pro(harness, "user_a")
    harness.billing.fail_with = lambda: DependencyUnavailableError()
    payload = event_payload(customer, event_id="evt_retry")
    assert send_webhook(harness, payload).status_code == 503
    # Nothing recorded, so Stripe's retry is processed normally.
    assert superuser_query(
        harness.database_url, "SELECT count(*) FROM app.billing_events WHERE stripe_event_id='evt_retry'"
    ) == [(0,)]
    harness.billing.fail_with = None
    assert send_webhook(harness, payload).json()["status"] == "processed"


def test_concurrent_duplicate_deliveries_are_serialized(harness: Harness) -> None:
    customer = make_pro(harness, "user_a")
    harness.billing.set_subscriptions(customer, [snapshot(customer, SubscriptionStatus.PAST_DUE)])
    payload = event_payload(customer, event_id="evt_parallel")
    results: list[int] = []
    errors: list[BaseException] = []

    def deliver() -> None:
        try:
            results.append(send_webhook(harness, payload).status_code)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=deliver) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
    assert not errors
    assert results == [200] * 8
    assert superuser_query(
        harness.database_url, "SELECT status, attempts FROM app.billing_events WHERE stripe_event_id='evt_parallel'"
    ) == [("processed", 8)]
    assert harness.client.get("/api/v1/billing/status", headers=harness.login("user_a")).json()["state"] == "past_due"


# -- Reconciliation -----------------------------------------------------------


def test_reconciliation_dry_run_then_apply(harness: Harness) -> None:
    customer = make_pro(harness, "user_a")
    harness.billing.set_subscriptions(customer, [snapshot(customer, SubscriptionStatus.CANCELED)])  # webhook "lost"
    report = harness.container.billing.service.reconcile(user_id=None, apply=False)
    assert report.customers_checked == 1
    assert [(d.kind, d.local_status, d.remote_status) for d in report.divergences] == [
        ("differs", "active", "canceled")
    ]
    assert harness.client.get("/api/v1/billing/status", headers=harness.login("user_a")).json()["plan"] == "pro"

    harness.container.billing.service.reconcile(user_id=None, apply=True)
    assert harness.client.get("/api/v1/billing/status", headers=harness.login("user_a")).json()["plan"] == "free"
    assert harness.container.billing.service.reconcile(user_id=None, apply=False).divergences == []


def test_concurrent_distinct_events_converge_on_the_latest_stripe_state(harness: Harness) -> None:
    """BILL-10/BILL-14: a slow sync that read older state cannot overwrite a newer one.

    The first delivery reads 'active' and is slow to finish. Meanwhile the
    subscription is canceled and a second, distinct event arrives. The
    customer lock makes the second sync wait and then re-read Stripe.
    """
    customer = make_pro(harness, "user_a")
    provider = harness.billing
    original = InMemoryBillingProvider.list_customer_subscriptions
    first_read = threading.Event()
    delivered: list[Any] = []

    def slow_first_read(self: InMemoryBillingProvider, customer_id: str) -> list[SubscriptionSnapshot]:
        seen = original(self, customer_id)
        if not first_read.is_set():
            first_read.set()
            wait_for_lock_wait(harness.database_url)  # the second delivery queues behind the customer lock
        return seen

    provider.list_customer_subscriptions = slow_first_read.__get__(provider)  # type: ignore[method-assign]
    first = threading.Thread(target=lambda: delivered.append(send_webhook(harness, event_payload(customer))))
    first.start()
    assert first_read.wait(10)
    provider.set_subscriptions(customer, [snapshot(customer, SubscriptionStatus.CANCELED)])
    second = send_webhook(harness, event_payload(customer, "customer.subscription.deleted"))
    first.join(20)
    # The wait runs inside the first delivery, so a failed wait shows only as that delivery's 500.
    assert delivered[0].status_code == 200
    assert delivered[0].json()["status"] == "processed"
    assert second.json()["status"] == "processed"
    assert harness.client.get("/api/v1/billing/status", headers=harness.login("user_a")).json()["plan"] == "free"


def test_webhook_for_a_customer_stripe_rejects_is_acknowledged_and_flagged(harness: Harness) -> None:
    """BILL-11: a permanent Stripe rejection is not retried forever."""
    customer = make_pro(harness, "user_a")
    harness.billing.fail_with = lambda: InvalidRequestError("The billing request was rejected.")
    r = send_webhook(harness, event_payload(customer, event_id="evt_rejected"))
    assert r.status_code == 200
    assert r.json()["status"] == "needs_reconciliation"
    assert superuser_query(
        harness.database_url, "SELECT status FROM app.billing_events WHERE stripe_event_id='evt_rejected'"
    ) == [("needs_reconciliation",)]


def test_webhook_adopts_a_customer_whose_local_link_was_lost(harness: Harness) -> None:
    headers = harness.login("user_a")
    user_id = harness.client.get("/api/v1/me", headers=headers).json()["user_id"]
    harness.billing.register_customer("cus_lost", uuid.UUID(user_id))
    harness.billing.set_subscriptions("cus_lost", [snapshot("cus_lost", SubscriptionStatus.ACTIVE)])
    assert send_webhook(harness, event_payload("cus_lost")).json()["status"] == "processed"
    assert customer_of(harness, "user_a") == "cus_lost"
    assert harness.client.get("/api/v1/billing/status", headers=headers).json()["plan"] == "pro"
    # Linked now, so it joins the back of the reconciliation rotation, not the front.
    assert superuser_query(
        harness.database_url, "SELECT reconciled_at FROM app.billing_customers WHERE stripe_customer_id = 'cus_lost'"
    ) == [(harness.clock.now(),)]


def test_webhook_never_adopts_a_customer_for_a_user_linked_elsewhere(harness: Harness) -> None:
    customer = make_pro(harness, "user_a")
    user_id = harness.client.get("/api/v1/me", headers=harness.login("user_a")).json()["user_id"]
    # A second customer claims the same user in its metadata.
    harness.billing.register_customer("cus_other", uuid.UUID(user_id))
    harness.billing.set_subscriptions("cus_other", [snapshot("cus_other", SubscriptionStatus.CANCELED)])
    assert send_webhook(harness, event_payload("cus_other")).json()["status"] == "needs_reconciliation"
    assert customer_of(harness, "user_a") == customer
    assert harness.client.get("/api/v1/billing/status", headers=harness.login("user_a")).json()["plan"] == "pro"


def test_webhook_bursts_leave_worker_connections_for_other_work(harness: Harness) -> None:
    """Synchronization holds a worker connection across Stripe calls, so it is
    admitted through fewer slots than the worker pool has connections."""
    customer = make_pro(harness, "user_a")
    provider = harness.billing
    original = InMemoryBillingProvider.list_customer_subscriptions

    def slow_list(self: InMemoryBillingProvider, customer_id: str) -> list[SubscriptionSnapshot]:
        time.sleep(1.5)
        return original(self, customer_id)

    provider.list_customer_subscriptions = slow_list.__get__(provider)  # type: ignore[method-assign]
    codes: list[int] = []
    threads = [
        threading.Thread(target=lambda: codes.append(send_webhook(harness, event_payload(customer)).status_code))
        for _ in range(5)
    ]
    for t in threads:
        t.start()
    time.sleep(0.3)
    started = time.monotonic()
    ready = harness.client.get("/api/v1/ready")
    assert ready.status_code == 200
    assert time.monotonic() - started < 1.0
    for t in threads:
        t.join(30)
    # Deliveries that found no slot in time asked Stripe to retry; none failed otherwise.
    assert set(codes) <= {200, 503}
    assert codes.count(200) >= 2


def test_reconciliation_never_overwrites_a_concurrent_webhook(harness: Harness) -> None:
    """BILL-10: apply-mode reconciliation reads Stripe under the customer lock."""
    make_pro(harness, "user_a")
    provider = harness.billing
    original = InMemoryBillingProvider.list_customer_subscriptions
    reconcile_read = threading.Event()
    delivered: list[Any] = []

    def racing_list(self: InMemoryBillingProvider, customer_id: str) -> list[SubscriptionSnapshot]:
        seen = original(self, customer_id)
        if not reconcile_read.is_set():
            reconcile_read.set()
            # The user cancels while reconciliation is between its read and its write.
            self.set_subscriptions(customer_id, [snapshot(customer_id, SubscriptionStatus.CANCELED)])
            webhook = threading.Thread(
                target=lambda: delivered.append(
                    send_webhook(harness, event_payload(customer_id, "customer.subscription.deleted"))
                )
            )
            webhook.start()
            wait_for_lock_wait(harness.database_url)  # the webhook waits on the customer lock
        return seen

    provider.list_customer_subscriptions = racing_list.__get__(provider)  # type: ignore[method-assign]
    harness.container.billing.service.reconcile(user_id=None, apply=True)
    deadline = time.monotonic() + 20
    while not delivered and time.monotonic() < deadline:
        time.sleep(0.05)
    assert delivered[0].json()["status"] == "processed"
    assert harness.client.get("/api/v1/billing/status", headers=harness.login("user_a")).json()["plan"] == "free"


def test_reconciliation_apply_records_the_plan_change(harness: Harness) -> None:
    customer = make_pro(harness, "user_a")
    harness.billing.set_subscriptions(customer, [snapshot(customer, SubscriptionStatus.CANCELED)])
    harness.container.billing.service.reconcile(user_id=None, apply=True)
    rows = superuser_query(
        harness.database_url,
        "SELECT payload->>'plan' FROM app.outbox WHERE event_type = 'billing.entitlement_changed' ORDER BY occurred_at",
    )
    assert rows[-1] == ("free",)


def test_reconciliation_gives_pro_back_to_a_subscriber_a_price_change_dropped(harness: Harness) -> None:
    """Stored as unknown while the replaced price had no plan; with the Pro product set, the next apply restores it."""
    customer = make_pro(harness, "user_a")
    dropped = snapshot(customer, SubscriptionStatus.ACTIVE, plan_key=PlanKey.UNKNOWN, price_id="price_replaced")
    harness.billing.set_subscriptions(customer, [dropped])
    send_webhook(harness, event_payload(customer))
    headers = harness.login("user_a")
    assert harness.client.get("/api/v1/billing/status", headers=headers).json()["plan"] == "free"

    # What Stripe holds for the same subscription: the replaced price, still a monthly price of the Pro product.
    stripe_subscription = {
        "id": dropped.stripe_subscription_id,
        "customer": customer,
        "status": "active",
        "cancel_at_period_end": False,
        "items": {
            "data": [
                {
                    "price": {
                        "id": "price_replaced",
                        "product": PRODUCT_PRO,
                        "recurring": {"interval": "month", "interval_count": 1},
                    },
                    "current_period_end": int(FUTURE.timestamp()),
                }
            ]
        },
    }
    harness.billing.set_subscriptions(customer, [to_snapshot(stripe_subscription, harness.container.billing.catalog)])
    service = harness.container.billing.service
    assert [d.kind for d in service.reconcile(user_id=None, apply=False).divergences] == ["differs"]
    assert (
        harness.client.get("/api/v1/billing/status", headers=headers).json()["plan"] == "free"
    )  # a dry run changes nothing

    service.reconcile(user_id=None, apply=True)
    status = harness.client.get("/api/v1/billing/status", headers=headers).json()
    assert (status["plan"], status["plan_key"]) == ("pro", "pro_monthly")
    assert harness.client.get("/api/v1/pro/insight", headers=headers).status_code == 200


def test_reconciliation_links_flagged_customers_and_resolves_their_events(harness: Harness) -> None:
    """BILL-19: events that could not be associated are reconciled once the link can be made."""
    headers = harness.login("user_a")
    user_id = harness.client.get("/api/v1/me", headers=headers).json()["user_id"]
    harness.billing.set_subscriptions("cus_late", [snapshot("cus_late", SubscriptionStatus.ACTIVE)])
    assert send_webhook(harness, event_payload("cus_late")).json()["status"] == "needs_reconciliation"

    service = harness.container.billing.service
    assert service.reconcile(user_id=None, apply=False).unlinked_customers == ["cus_late"]
    # Stripe now shows the customer was created for this user (for example, after a restore).
    harness.billing.register_customer("cus_late", uuid.UUID(user_id))
    report = service.reconcile(user_id=None, apply=True)
    assert report.unlinked_customers == []
    assert [d.kind for d in report.divergences] == ["missing_locally"]
    assert harness.client.get("/api/v1/billing/status", headers=headers).json()["plan"] == "pro"
    assert superuser_query(
        harness.database_url, "SELECT count(*) FROM app.billing_events WHERE status = 'needs_reconciliation'"
    ) == [(0,)]


def test_reconcile_job_checks_a_bounded_batch_least_recently_checked_first(harness: Harness) -> None:
    customers = [make_pro(harness, f"user_{i}") for i in range(3)]
    service = harness.container.billing.service
    checked: list[str] = []
    original = InMemoryBillingProvider.list_customer_subscriptions

    def recording(self: InMemoryBillingProvider, customer_id: str) -> list[SubscriptionSnapshot]:
        checked.append(customer_id)
        return original(self, customer_id)

    harness.billing.list_customer_subscriptions = recording.__get__(harness.billing)  # type: ignore[method-assign]
    for _ in range(2):
        harness.clock.advance(seconds=1)  # each run comes after the links and checks before it
        assert service.reconcile_job(apply=False, batch_size=2)["customers"] == 2
    # Two runs of two cover all three customers: none waits behind the others.
    assert sorted(set(checked)) == sorted(customers)


def test_reconcile_job_skips_customers_stripe_rejects(harness: Harness) -> None:
    make_pro(harness, "user_a")
    harness.billing.fail_with = lambda: InvalidRequestError("The billing request was rejected.")
    summary = harness.container.billing.service.reconcile_job(apply=True, batch_size=10)
    assert (summary["customers"], summary["rejected"]) == (1, 1)


RECONCILE_CRON_SECRET = "cron-secret-test_only-billing-reconcile-0123456789"  # test-only value
SANDBOX_MODE = {"billing_mode": "sandbox", "stripe_secret_key": "sk_test_" + "o" * 24}


@pytest.mark.parametrize(
    ("settings", "applies"),
    [
        ({}, False),  # mock mode: nothing to compare with, so the job reports only
        (SANDBOX_MODE, True),  # outside mock mode the default is to apply
        ({**SANDBOX_MODE, "billing_reconcile_apply": False}, False),
        ({"billing_reconcile_apply": True}, True),
    ],
    ids=["mock", "sandbox", "sandbox-report-only", "mock-apply"],
)
def test_the_scheduled_job_repairs_a_lost_cancellation_unless_it_is_set_to_only_report(
    make_harness: Any, settings: dict[str, Any], applies: bool
) -> None:
    """BILL-19: the daily job is what ends Pro after a `customer.subscription.deleted` that never arrived."""
    h = make_harness({"cron_secret": RECONCILE_CRON_SECRET, **settings})
    customer = make_pro(h, "user_a")
    h.billing.set_subscriptions(customer, [snapshot(customer, SubscriptionStatus.CANCELED)])  # the webhook was lost

    r = h.client.get("/api/v1/jobs/reconcile-billing", headers={"Authorization": f"Bearer {RECONCILE_CRON_SECRET}"})

    assert r.status_code == 200
    assert r.json()["summary"] == {"customers": 1, "divergences": 1, "unlinked": 0, "rejected": 0, "applied": applies}
    plan = h.client.get("/api/v1/billing/status", headers=h.login("user_a")).json()["plan"]
    assert plan == ("free" if applies else "pro")


def test_cli_reconcile_prints_divergences(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import argparse

    import app.bootstrap
    from app import cli

    customer = make_pro(harness, "user_a")
    harness.billing.set_subscriptions(customer, [snapshot(customer, SubscriptionStatus.CANCELED)])
    monkeypatch.setattr(app.bootstrap, "bootstrap", lambda: harness.container)
    monkeypatch.setattr(harness.container, "close", lambda: None)
    assert cli.cmd_reconcile(argparse.Namespace(user=None, apply=False)) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["divergences"][0]["kind"] == "differs"
    assert report["divergences"][0]["remote_status"] == "canceled"


def test_reconcile_for_one_user_checks_and_repairs_only_that_users_customer(harness: Harness) -> None:
    """BILL-19: `--user` narrows the run to one user's customer, and a user with no customer is checked for nothing."""
    customer_a = make_pro(harness, "user_a")
    customer_b = make_pro(harness, "user_b")
    for customer in (customer_a, customer_b):
        harness.billing.set_subscriptions(customer, [snapshot(customer, SubscriptionStatus.CANCELED)])
    user_a = user_id_of(harness, harness.login("user_a"))
    service = harness.container.billing.service

    report = service.reconcile(user_id=user_a, apply=True)

    assert report.customers_checked == 1
    assert [(d.user_id, d.kind) for d in report.divergences] == [(user_a, "differs")]
    assert harness.client.get("/api/v1/billing/status", headers=harness.login("user_a")).json()["plan"] == "free"
    assert harness.client.get("/api/v1/billing/status", headers=harness.login("user_b")).json()["plan"] == "pro"
    nobody = user_id_of(harness, harness.login("user_c"))  # signed in, never started a checkout
    assert service.reconcile(user_id=nobody, apply=True).customers_checked == 0


def test_cli_reconcile_with_a_user_checks_only_that_user(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import argparse

    import app.bootstrap
    from app import cli

    customers = [make_pro(harness, name) for name in ("user_a", "user_b")]
    for customer in customers:
        harness.billing.set_subscriptions(customer, [snapshot(customer, SubscriptionStatus.CANCELED)])
    user_b = user_id_of(harness, harness.login("user_b"))
    monkeypatch.setattr(app.bootstrap, "bootstrap", lambda: harness.container)
    monkeypatch.setattr(harness.container, "close", lambda: None)

    assert cli.cmd_reconcile(argparse.Namespace(user=str(user_b), apply=False)) == 0

    report = json.loads(capsys.readouterr().out)
    assert report["customers_checked"] == 1
    assert [d["user_id"] for d in report["divergences"]] == [str(user_b)]


def test_trialing_and_duplicate_subscriptions_are_logged(harness: Harness, caplog: pytest.LogCaptureFixture) -> None:
    customer = make_pro(harness, "user_a")
    harness.billing.set_subscriptions(
        customer,
        [
            snapshot(customer, SubscriptionStatus.ACTIVE),
            snapshot(customer, SubscriptionStatus.ACTIVE, stripe_subscription_id="sub_second"),
            snapshot(customer, SubscriptionStatus.TRIALING, stripe_subscription_id="sub_trial"),
        ],
    )
    with caplog.at_level("WARNING"):
        send_webhook(harness, event_payload(customer))
    messages = {r.getMessage() for r in caplog.records}
    assert {"billing_trialing_anomaly", "billing_multiple_entitling_subscriptions"} <= messages


def test_unresolvable_flags_never_stop_the_job_from_covering_every_customer(harness: Harness) -> None:
    customers = [make_pro(harness, f"user_{i}") for i in range(2)]
    for n in range(4):  # flagged customers that no user can be linked to
        assert send_webhook(harness, event_payload(f"cus_orphan{n}")).json()["status"] == "needs_reconciliation"
    checked: list[str] = []
    original = InMemoryBillingProvider.list_customer_subscriptions

    def recording(self: InMemoryBillingProvider, customer_id: str) -> list[SubscriptionSnapshot]:
        checked.append(customer_id)
        return original(self, customer_id)

    harness.billing.list_customer_subscriptions = recording.__get__(harness.billing)  # type: ignore[method-assign]
    service = harness.container.billing.service
    for _ in range(2):
        harness.clock.advance(seconds=1)  # each run comes after the links and checks before it
        service.reconcile_job(apply=True, batch_size=2)
    assert set(customers) <= set(checked)


def _record_checks(h: Harness, rejected: set[str] | None = None) -> list[str]:
    """Record every customer the provider is asked about; reject those in ``rejected``."""
    checked: list[str] = []
    original = InMemoryBillingProvider.list_customer_subscriptions

    def recording(self: InMemoryBillingProvider, customer_id: str) -> list[SubscriptionSnapshot]:
        checked.append(customer_id)
        if rejected and customer_id in rejected:
            raise InvalidRequestError("The billing request was rejected.")
        return original(self, customer_id)

    h.billing.list_customer_subscriptions = recording.__get__(h.billing)  # type: ignore[method-assign]
    return checked


@pytest.mark.parametrize("apply", [True, False])
def test_customers_stripe_rejects_move_to_the_back_of_the_rotation(harness: Harness, apply: bool) -> None:
    customers = [make_pro(harness, f"user_{i}") for i in range(3)]
    service = harness.container.billing.service
    order = [
        row[0]
        for row in superuser_query(
            harness.database_url,
            "SELECT stripe_customer_id FROM app.billing_customers ORDER BY reconciled_at NULLS FIRST, user_id",
        )
    ]
    for customer in order[:2]:  # the two the job picks first: canceled, then deleted in Stripe
        harness.billing.set_subscriptions(customer, [snapshot(customer, SubscriptionStatus.CANCELED)])
        assert send_webhook(harness, event_payload(customer)).json()["status"] == "processed"
        harness.billing.delete_customer(customer)
    checked = _record_checks(harness)
    summaries = []
    for _ in range(2):
        harness.clock.advance(seconds=1)  # each run comes after the links and checks before it
        summaries.append(service.reconcile_job(apply=apply, batch_size=2))
    assert summaries[0]["rejected"] == 2  # nothing left to read by ID: Stripe rejects the customer
    assert set(checked) == set(customers)  # the healthy customer is reached, not starved by the rejected ones


def test_a_batch_of_one_is_not_taken_by_an_unresolvable_flag(harness: Harness) -> None:
    customers = [make_pro(harness, f"user_{i}") for i in range(2)]
    assert send_webhook(harness, event_payload("cus_orphan0")).json()["status"] == "needs_reconciliation"
    checked = _record_checks(harness)
    for _ in range(2):
        harness.clock.advance(seconds=1)  # each run comes after the links and checks before it
        harness.container.billing.service.reconcile_job(apply=True, batch_size=1)
    assert set(customers) <= set(checked)


def test_reconcile_job_stops_starting_checks_when_its_time_budget_is_spent(harness: Harness) -> None:
    for i in range(3):
        make_pro(harness, f"user_{i}")
    service = harness.container.billing.service
    service._reconcile_time_budget = 0.0
    assert service.reconcile_job(apply=False, batch_size=10)["customers"] == 0


def test_new_customers_join_the_back_of_the_rotation(harness: Harness) -> None:
    """Customers created every day never keep those checked earlier from being checked again."""
    old = [make_pro(harness, f"user_old{i}") for i in range(2)]
    service = harness.container.billing.service
    harness.clock.advance(days=1)
    service.reconcile_job(apply=False, batch_size=2)
    checked = _record_checks(harness)
    for day in range(3):
        harness.clock.advance(days=1)
        for i in range(2):  # each day, as many users start a checkout as one run checks
            assert start_checkout(harness, harness.login(f"user_new{day}_{i}")).status_code == 200
        service.reconcile_job(apply=False, batch_size=2)
    assert set(old) <= set(checked)


def _slow_stripe(h: Harness, monkeypatch: pytest.MonkeyPatch, seconds: float = 0.2) -> list[str]:
    """Each Stripe read takes ``seconds`` on the job's clock. Returns the customers whose subscriptions are read."""
    import app.modules.billing.service as billing_service

    elapsed = [0.0]
    monkeypatch.setattr(billing_service, "time", SimpleNamespace(monotonic=lambda: elapsed[0]))
    checked = _record_checks(h)
    recorded_list = h.billing.list_customer_subscriptions
    original_read = InMemoryBillingProvider.get_customer_app_user_id

    def slow_list(customer_id: str) -> list[SubscriptionSnapshot]:
        elapsed[0] += seconds
        return recorded_list(customer_id)

    def slow_read(self: InMemoryBillingProvider, customer_id: str) -> uuid.UUID | None:
        elapsed[0] += seconds
        return original_read(self, customer_id)

    h.billing.list_customer_subscriptions = slow_list  # type: ignore[method-assign]
    h.billing.get_customer_app_user_id = slow_read.__get__(h.billing)  # type: ignore[method-assign]
    return checked


def _flag(h: Harness, customer: str) -> None:
    """Flag a delivery for a linked customer: Stripe rejects its re-read once."""
    h.billing.fail_with = lambda: InvalidRequestError("The billing request was rejected.")
    assert send_webhook(h, event_payload(customer)).json()["status"] == "needs_reconciliation"
    h.billing.fail_with = None


def test_flagged_customers_get_at_most_half_of_a_runs_time(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    """Unresolvable flags that are slow to check never take the whole time budget from the rotation."""
    customers = [make_pro(harness, f"user_{i}") for i in range(2)]
    for n in range(3):  # flagged customers that no user can be linked to: each costs a Stripe read, every run
        assert send_webhook(harness, event_payload(f"cus_orphan{n}")).json()["status"] == "needs_reconciliation"
    checked = _slow_stripe(harness, monkeypatch)
    service = harness.container.billing.service
    service._reconcile_time_budget = 0.3  # one check and a half
    for _ in range(2):
        harness.clock.advance(seconds=1)
        service.reconcile_job(apply=True, batch_size=4)
    assert set(customers) <= set(checked)


@pytest.mark.parametrize("apply", [True, False])
def test_flagged_customers_the_flagged_half_does_not_reach_wait_their_turn_in_the_rotation(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, apply: bool
) -> None:
    """A newer flag that uses up the flagged half never keeps a customer flagged earlier from being checked."""
    older = make_pro(harness, "user_older")
    _flag(harness, older)
    superuser_query(
        harness.database_url,
        "UPDATE app.billing_events SET received_at = received_at - interval '1 hour' WHERE stripe_customer_id = %s",
        (older,),
    )
    if apply:  # no user can be linked to the newer flag: a Stripe read every run, and the flag stays
        assert send_webhook(harness, event_payload("cus_orphan0")).json()["status"] == "needs_reconciliation"
    else:  # a dry run resolves no flag: the customer flagged later heads every run
        _flag(harness, make_pro(harness, "user_newer"))
    healthy = [make_pro(harness, f"user_{i}") for i in range(2)]
    checked = _slow_stripe(harness, monkeypatch)
    service = harness.container.billing.service
    service._reconcile_time_budget = 0.3  # the newer flag's check uses up the flagged half; one rotation check follows
    for _ in range(3):
        harness.clock.advance(seconds=1)
        assert service.reconcile_job(apply=apply, batch_size=4)["customers"] == 2
    # One rotation check per run: three runs reach each of the three, the older flagged customer among them.
    assert set(healthy) <= set(checked)
    assert older in checked, "a flagged customer the flagged half did not reach was left out of the rotation"


def test_a_run_checks_each_customer_at_most_once(harness: Harness) -> None:
    flagged = make_pro(harness, "user_flagged")  # linked first: at the front of the rotation too
    harness.clock.advance(seconds=1)
    other = make_pro(harness, "user_other")
    _flag(harness, flagged)
    checked = _record_checks(harness)
    harness.clock.advance(seconds=1)
    assert harness.container.billing.service.reconcile_job(apply=False, batch_size=2)["customers"] == 2
    assert checked == [flagged, other]  # the flagged customer is not checked again in the rotation


def test_a_run_with_flagged_customers_stays_within_its_batch(harness: Harness) -> None:
    others = [make_pro(harness, f"user_{i}") for i in range(3)]
    harness.clock.advance(seconds=1)
    flagged = make_pro(harness, "user_flagged")  # linked last: at the back of the rotation
    _flag(harness, flagged)
    checked = _record_checks(harness)
    harness.clock.advance(seconds=1)
    assert harness.container.billing.service.reconcile_job(apply=False, batch_size=2)["customers"] == 2
    # The flagged customer's check counts toward the batch: one rotation customer follows, not two.
    assert len(checked) == 2
    assert checked[0] == flagged
    assert checked[1] in others


def test_redelivered_flagged_event_during_reconciliation_does_not_deadlock(harness: Harness) -> None:
    """Both paths take the customer lock before ledger rows (lock ordering)."""
    customer = make_pro(harness, "user_a")
    payload = event_payload(customer, event_id="evt_flagged")
    harness.billing.fail_with = lambda: InvalidRequestError("rejected")
    assert send_webhook(harness, payload).json()["status"] == "needs_reconciliation"
    harness.billing.fail_with = None
    harness.billing.set_subscriptions(customer, [snapshot(customer, SubscriptionStatus.CANCELED)])

    original = InMemoryBillingProvider.list_customer_subscriptions
    delivered: list[Any] = []
    redelivery: threading.Thread | None = None

    def racing(self: InMemoryBillingProvider, customer_id: str) -> list[SubscriptionSnapshot]:
        nonlocal redelivery
        if redelivery is None:  # reconciliation holds the customer lock: redeliver the flagged event now
            redelivery = threading.Thread(target=lambda: delivered.append(send_webhook(harness, payload)))
            redelivery.start()
            wait_for_lock_wait(harness.database_url)  # the redelivery queues behind the lock
        return original(self, customer_id)

    harness.billing.list_customer_subscriptions = racing.__get__(harness.billing)  # type: ignore[method-assign]
    harness.container.billing.service.reconcile(user_id=None, apply=True)
    assert redelivery is not None
    redelivery.join(timeout=20)
    assert delivered[0].status_code == 200
    assert delivered[0].json()["status"] == "processed"  # resolved by reconciliation; acknowledged without work


def test_sync_slots_bound_the_callers_that_wait() -> None:
    """Waiting callers hold threadpool threads, so only ``max_waiting`` of them wait (BILL-10)."""
    from app.modules.billing.service import _Slots

    slots = _Slots(1, wait_seconds=5.0, max_waiting=1)
    holder, waiting = threading.Event(), threading.Event()
    results: list[str] = []

    def hold() -> None:
        with slots.admit():
            holder.set()
            waiting.wait(5)

    def wait_for_slot() -> None:
        try:
            with slots.admit():
                results.append("admitted")
        except DependencyUnavailableError:
            results.append("busy")

    first = threading.Thread(target=hold)
    first.start()
    holder.wait(5)
    second = threading.Thread(target=wait_for_slot)
    second.start()
    deadline = time.monotonic() + 5
    while slots._waiting == 0 and time.monotonic() < deadline:
        time.sleep(0.01)
    started = time.monotonic()
    wait_for_slot()  # a third caller finds the slot taken and the queue full
    assert results == ["busy"]
    assert time.monotonic() - started < 1  # refused at once, without holding its thread
    waiting.set()
    first.join(5)
    second.join(5)
    assert results == ["busy", "admitted"]
    with slots.admit():  # every slot was given back
        pass


def test_deleting_the_customer_in_stripe_ends_pro(harness: Harness) -> None:
    """A deleted customer cannot be listed; its canceled subscriptions are read one by one."""
    customer = make_pro(harness, "user_a")
    ended = snapshot(customer, SubscriptionStatus.CANCELED, ended_at=harness.clock.now())
    harness.billing.set_subscriptions(customer, [ended])
    harness.billing.delete_customer(customer)
    result = send_webhook(harness, event_payload(customer, "customer.subscription.deleted"))
    assert result.json()["status"] == "processed"
    assert harness.client.get("/api/v1/billing/status", headers=harness.login("user_a")).json()["plan"] == "free"


@pytest.mark.parametrize("apply", [False, True])
def test_reconciliation_reads_a_deleted_customers_subscriptions_by_id(harness: Harness, apply: bool) -> None:
    customer = make_pro(harness, "user_a")
    harness.billing.set_subscriptions(customer, [snapshot(customer, SubscriptionStatus.CANCELED)])
    harness.billing.delete_customer(customer)
    report = harness.container.billing.service.reconcile(user_id=None, apply=apply)
    assert (report.rejected_customers, [d.kind for d in report.divergences]) == ([], ["differs"])
    plan = harness.client.get("/api/v1/billing/status", headers=harness.login("user_a")).json()["plan"]
    assert plan == ("free" if apply else "pro")


def test_a_subscription_stripe_does_not_return_changes_nothing(harness: Harness) -> None:
    """With a key for another account, every read is rejected: Pro is kept and the event flagged, never revoked."""
    customer = make_pro(harness, "user_a")
    harness.billing.set_subscriptions(customer, [])  # the subscription cannot be found by ID either
    harness.billing.delete_customer(customer)
    result = send_webhook(harness, event_payload(customer))
    assert result.json()["status"] == "needs_reconciliation"
    assert harness.client.get("/api/v1/billing/status", headers=harness.login("user_a")).json()["plan"] == "pro"


def test_an_active_subscription_on_an_unknown_price_is_logged(
    harness: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    customer = make_pro(harness, "user_a")
    harness.billing.set_subscriptions(
        customer, [snapshot(customer, SubscriptionStatus.ACTIVE, plan_key=PlanKey.UNKNOWN, price_id="price_replaced")]
    )
    with caplog.at_level("WARNING"):
        send_webhook(harness, event_payload(customer))
    # A user who pays without Pro is an error, which reaches error reporting.
    assert [r.levelname for r in caplog.records if r.getMessage() == "billing_unknown_price"] == ["ERROR"]


@pytest.mark.parametrize("status", [SubscriptionStatus.PAST_DUE, SubscriptionStatus.UNPAID])
def test_an_unknown_price_is_an_error_only_while_the_user_is_paying(
    harness: Harness, caplog: pytest.LogCaptureFixture, status: SubscriptionStatus
) -> None:
    customer = make_pro(harness, "user_a")
    harness.billing.set_subscriptions(
        customer, [snapshot(customer, status, plan_key=PlanKey.UNKNOWN, price_id="price_replaced")]
    )
    with caplog.at_level("WARNING"):
        send_webhook(harness, event_payload(customer))
    levels = [r.levelname for r in caplog.records if r.getMessage() == "billing_unknown_price"]
    assert levels == (["ERROR"] if status is SubscriptionStatus.PAST_DUE else ["WARNING"])


def test_a_webhook_burst_waits_in_the_event_loop_not_on_threads(make_harness: Any) -> None:
    """Deliveries queue for a synchronization slot without holding threadpool threads (BILL-10)."""
    from fastapi.testclient import TestClient

    from app.bootstrap import create_app

    h = make_harness()
    customer = make_pro(h, "user_a")
    service = h.container.billing.service
    original_list = InMemoryBillingProvider.list_customer_subscriptions

    def slow_list(self: InMemoryBillingProvider, customer_id: str) -> list[SubscriptionSnapshot]:
        time.sleep(0.5)
        return original_list(self, customer_id)

    h.billing.list_customer_subscriptions = slow_list.__get__(h.billing)  # type: ignore[method-assign]
    original_process = service.process_webhook
    lock = threading.Lock()
    inside, peak = 0, 0

    def counting(event: Any) -> Any:
        nonlocal inside, peak
        with lock:
            inside += 1
            peak = max(peak, inside)
        try:
            return original_process(event)
        finally:
            with lock:
                inside -= 1

    service.process_webhook = counting  # type: ignore[method-assign]
    with TestClient(create_app(h.container), raise_server_exceptions=False) as client:  # one event loop
        codes: list[int] = []

        def deliver() -> None:
            payload = event_payload(customer)
            headers = {"Stripe-Signature": sign(payload), "Content-Type": "application/json"}
            codes.append(client.post("/api/v1/webhooks/stripe", content=payload, headers=headers).status_code)

        threads = [threading.Thread(target=deliver) for _ in range(6)]
        for t in threads:
            t.start()
        time.sleep(0.2)
        started = time.monotonic()
        assert client.get("/api/v1/ready").status_code == 200
        assert time.monotonic() - started < 1.0
        for t in threads:
            t.join(30)
    assert codes == [200] * 6  # the burst was absorbed, not refused
    assert peak <= service.sync_slots  # the others waited without a thread


def test_webhook_admission_verifies_first_and_refuses_after_the_wait(make_harness: Any) -> None:
    """With every synchronization slot taken, a forged delivery gets 400 at once, never queueing for a
    slot, and a valid one waits DATABASE_CONNECT_TIMEOUT_SECONDS for a slot, then gets 503 (BILL-10)."""
    from fastapi.testclient import TestClient

    from app.bootstrap import create_app

    h = make_harness({"database_connect_timeout_seconds": 1})
    service = h.container.billing.service
    # One customer per holder: deliveries for one customer would queue on its lock instead.
    *held_customers, spare = [make_pro(h, f"user_{i}") for i in range(service.sync_slots + 1)]
    original = InMemoryBillingProvider.list_customer_subscriptions
    entered, release = threading.Semaphore(0), threading.Event()

    def held_read(self: InMemoryBillingProvider, customer_id: str) -> list[SubscriptionSnapshot]:
        entered.release()
        release.wait(10)  # Stripe answers only once the test lets it
        return original(self, customer_id)

    h.billing.list_customer_subscriptions = held_read.__get__(h.billing)  # type: ignore[method-assign]
    with TestClient(create_app(h.container), raise_server_exceptions=False) as client:  # one event loop
        responses: dict[str, Any] = {}
        threads: list[threading.Thread] = []

        def deliver(name: str, customer: str, secret: str = TEST_STRIPE_WEBHOOK_SECRET) -> threading.Thread:
            payload = event_payload(customer)
            headers = {"Stripe-Signature": sign(payload, secret=secret), "Content-Type": "application/json"}

            def run() -> None:
                started = time.monotonic()
                r = client.post("/api/v1/webhooks/stripe", content=payload, headers=headers)
                responses[name] = (r, time.monotonic() - started)

            thread = threading.Thread(target=run)
            threads.append(thread)
            thread.start()
            return thread

        try:
            for i, customer in enumerate(held_customers):
                deliver(f"held{i}", customer)
            for _ in held_customers:
                assert entered.acquire(timeout=10)
            deliver("forged", spare, secret="whsec_attacker_secret_00000000000").join(5)
            forged, forged_seconds = responses["forged"]
            assert (forged.status_code, forged.json()["code"]) == (400, "WEBHOOK_SIGNATURE_INVALID")
            assert forged_seconds < service.sync_wait_seconds / 2
            deliver("waiting", spare).join(5)
            assert "waiting" in responses, "a delivery waited for a slot without a bound"
            waiting, waited_seconds = responses["waiting"]
            assert (waiting.status_code, waiting.json()["code"]) == (503, "DEPENDENCY_UNAVAILABLE")
            assert waited_seconds >= service.sync_wait_seconds * 0.9
        finally:
            release.set()
            for t in threads:
                t.join(10)
    assert [responses[f"held{i}"][0].status_code for i in range(len(held_customers))] == [200] * len(held_customers)
