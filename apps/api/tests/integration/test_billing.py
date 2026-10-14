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
from typing import Any

import pytest

from app.kernel.errors import DependencyUnavailableError, InvalidRequestError
from app.modules.billing.adapters.fakes import InMemoryBillingProvider
from app.modules.billing.domain import PlanKey, SubscriptionSnapshot, SubscriptionStatus
from tests.conftest import PRICE_MONTHLY, TEST_STRIPE_WEBHOOK_SECRET, Harness, superuser_query

pytestmark = pytest.mark.db

FUTURE = datetime(2026, 7, 1, tzinfo=UTC)


def sign(payload: bytes, secret: str = TEST_STRIPE_WEBHOOK_SECRET, ts: int | None = None) -> str:
    stamp = ts if ts is not None else int(time.time())
    digest = hmac.new(secret.encode(), f"{stamp}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={stamp},v1={digest}"


def event_payload(
    customer_id: str, event_type: str = "customer.subscription.updated", event_id: str | None = None
) -> bytes:
    return json.dumps(
        {
            "id": event_id or f"evt_{uuid.uuid4().hex}",
            "object": "event",
            "type": event_type,
            "created": int(time.time()),
            "data": {"object": {"id": "sub_x", "object": "subscription", "customer": customer_id}},
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
    newer = event_payload(customer, "customer.subscription.deleted")
    older = event_payload(customer, "customer.subscription.updated")
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

    def slow_first_read(self: InMemoryBillingProvider, customer_id: str) -> list[SubscriptionSnapshot]:
        seen = original(self, customer_id)
        if not first_read.is_set():
            first_read.set()
            time.sleep(1.0)
        return seen

    provider.list_customer_subscriptions = slow_first_read.__get__(provider)  # type: ignore[method-assign]
    first = threading.Thread(target=lambda: send_webhook(harness, event_payload(customer)))
    first.start()
    assert first_read.wait(10)
    provider.set_subscriptions(customer, [snapshot(customer, SubscriptionStatus.CANCELED)])
    second = send_webhook(harness, event_payload(customer, "customer.subscription.deleted"))
    first.join(20)
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
            time.sleep(0.5)  # the webhook waits on the customer lock
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
        assert service.reconcile_job(apply=False, batch_size=2)["customers"] == 2
        harness.clock.advance(seconds=1)
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
        service.reconcile_job(apply=True, batch_size=2)
        harness.clock.advance(seconds=1)
    assert set(customers) <= set(checked)
