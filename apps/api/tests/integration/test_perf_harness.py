"""PERF-06 harness oracle (perf/server.py): it must tell exactly-once processing from reprocessing.

The webhook ledger alone cannot: a duplicate delivery that runs the
synchronization again leaves the same final row (status and attempts). The
harness therefore also counts Stripe re-reads. These tests drive the harness's
own ``seed``, ``build_app``, and ``webhook_report`` with concurrent duplicate
deliveries, in this suite's throwaway database (never ``app_perf``). Stripe is
the harness's in-memory mock.
"""

from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
import time
from collections import Counter
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from types import ModuleType
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.kernel.infrastructure.config import REPO_ROOT
from app.kernel.webhooks import EventStatus
from app.modules.billing.adapters.repository import PgBillingRepository

pytestmark = pytest.mark.db

USERS = 8  # four billing customers: cus_perf0000, cus_perf0002, ...
ORGS = 2
EVENTS = 12
COPIES = 3  # concurrent deliveries of each event, as PERF-06 sends them
EVENTS_PER_CUSTOMER = 2  # distinct events that contend for one customer's lock
# One sync slot per copy (slots = worker pool - 1), so the copies of an event
# reach the ledger row together instead of queueing for admission.
WORKER_POOL = COPIES + 1


@pytest.fixture(scope="module")
def perf_server() -> ModuleType:
    assert REPO_ROOT is not None  # a repository checkout, not a bundle
    spec = importlib.util.spec_from_file_location("perf_server", REPO_ROOT / "perf" / "server.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def harness(perf_server: ModuleType, db: str) -> Iterator[tuple[TestClient, Any]]:
    perf_server.seed(db, USERS, ORGS, 1)
    app, billing = perf_server.build_app(db, USERS, ORGS, 4, worker_pool_size=WORKER_POOL)
    yield TestClient(app, raise_server_exceptions=False), billing
    app.state.container.close()


def _payload(n: int) -> bytes:
    """The event perf/k6/scenarios.js sends for event ``n``."""
    customer = f"cus_perf{2 * ((n // EVENTS_PER_CUSTOMER) % (USERS // 2)):04d}"
    return json.dumps(
        {
            "id": f"evt_perf_{n:05d}",
            "object": "event",
            "type": "customer.subscription.updated",
            "created": int(time.time()),
            "data": {"object": {"id": f"sub_{customer[4:]}", "object": "subscription", "customer": customer}},
        }
    ).encode()


def _deliver_all(perf_server: ModuleType, client: TestClient) -> Counter[int]:
    """Every event COPIES times, consecutive copies at once. A 503 is retried, as Stripe would."""
    secret = perf_server.WEBHOOK_SECRET.encode()

    def deliver(iteration: int) -> int:
        payload = _payload(iteration // COPIES)
        for _ in range(20):
            stamp = int(time.time())
            digest = hmac.new(secret, f"{stamp}.".encode() + payload, hashlib.sha256).hexdigest()
            response = client.post(
                "/api/v1/webhooks/stripe",
                content=payload,
                headers={"Content-Type": "application/json", "Stripe-Signature": f"t={stamp},v1={digest}"},
            )
            if response.status_code != 503:
                return int(response.status_code)
            time.sleep(0.05)
        return 503

    with ThreadPoolExecutor(max_workers=2 * COPIES) as pool:
        return Counter(pool.map(deliver, range(EVENTS * COPIES)))


def test_concurrent_duplicates_resync_each_event_exactly_once(
    perf_server: ModuleType, harness: tuple[TestClient, Any], db: str
) -> None:
    client, billing = harness
    assert _deliver_all(perf_server, client) == {200: EVENTS * COPIES}
    report = perf_server.webhook_report(db, billing)
    assert report["billing_events"] == {"processed": {"events": EVENTS, "claims": EVENTS * COPIES}}
    assert report["stripe_resyncs"] == EVENTS
    assert report["stripe_calls"] == {"list_customer_subscriptions": EVENTS}
    assert report["exactly_once"] is True


def test_reprocessed_duplicates_are_reported_although_the_ledger_looks_identical(
    perf_server: ModuleType, harness: tuple[TestClient, Any], db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Control: a ledger claim that never sees the earlier delivery must fail the report."""
    claim = PgBillingRepository.claim_billing_event

    def forgetful_claim(self: PgBillingRepository, event: Any) -> EventStatus:
        claim(self, event)
        return EventStatus.RECEIVED

    monkeypatch.setattr(PgBillingRepository, "claim_billing_event", forgetful_claim)
    client, billing = harness
    assert _deliver_all(perf_server, client) == {200: EVENTS * COPIES}
    report = perf_server.webhook_report(db, billing)
    # Exactly what the correct run leaves behind ...
    assert report["billing_events"] == {"processed": {"events": EVENTS, "claims": EVENTS * COPIES}}
    # ... but every copy re-synchronized.
    assert report["stripe_resyncs"] == EVENTS * COPIES
    assert report["exactly_once"] is False
