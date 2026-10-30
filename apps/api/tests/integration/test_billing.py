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

from app.kernel.errors import DependencyUnavailableError, InvalidRequestError
from app.modules.billing.adapters.fakes import InMemoryBillingProvider
from app.modules.billing.adapters.stripe import to_snapshot
from app.modules.billing.domain import Plan, PlanKey, SubscriptionSnapshot, SubscriptionStatus, decide
from tests.conftest import PRICE_MONTHLY, PRICE_YEARLY, TEST_STRIPE_WEBHOOK_SECRET, Harness, superuser_query
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
    assert harness.billing.checkout_requests == [
        {**sent, "price_id": PRICE_MONTHLY, "idempotency_key": f"checkout-{user_id}-pro_monthly-key-pro_monthly"},
        {**sent, "price_id": PRICE_YEARLY, "idempotency_key": f"checkout-{user_id}-pro_yearly-key-pro_yearly"},
    ]
    assert harness.client.post("/api/v1/billing/portal", headers=headers).status_code == 200
    assert harness.billing.portal_requests == [{"customer_id": customer, "return_url": "http://localhost:3000/billing"}]


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
    assert r.json()["url"] == "http://localhost:3000/billing"


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


# -- Webhook trust model ------------------------------------------------------


def test_forged_webhook_rejected_without_side_effects(harness: Harness) -> None:
    customer = make_pro(harness, "user_a")
    harness.billing.set_subscriptions(customer, [snapshot(customer, SubscriptionStatus.CANCELED)])
    payload = event_payload(customer)
    for signature in ("", "t=1,v1=abc", sign(payload, secret="whsec_attacker_secret_00000000000")):
        r = send_webhook(harness, payload, signature)
        assert r.status_code == 400
        assert r.json()["code"] == "WEBHOOK_SIGNATURE_INVALID"
    assert harness.client.get("/api/v1/billing/status", headers=harness.login("user_a")).json()["plan"] == "pro"


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
