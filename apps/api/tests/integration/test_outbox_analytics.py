"""Outbox analytics handlers: a redelivery is the same PostHog event, and an event counts as
processed only once its export is confirmed, which never waits long (ARCH-15, ADR-0003, OBS-06)."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.bootstrap import create_app
from app.kernel.events import StoredEvent
from app.kernel.infrastructure.observability import PostHogAnalytics
from app.kernel.tenancy import VerifiedIdentity
from app.modules.billing.domain import EntitlementChanged, Plan
from app.modules.projects.domain import ProjectCreated
from tests.conftest import Harness, superuser_query
from tests.stub_server import Recorded, stub_server

pytestmark = pytest.mark.db


class WorkerDied(BaseException):
    """The process running the handlers died, or the platform stopped it."""


def record_plan_change(harness: Harness, user: str = "user_a") -> None:
    """Record one event outside any request, so nothing dispatches it."""
    ctx = harness.container.identity.resolver.personal(VerifiedIdentity(user))
    with harness.container.kernel.store.as_tenant(ctx) as t:
        t.record(EntitlementChanged(user_id=ctx.user_id, plan=Plan.PRO))


def outbox_row(harness: Harness) -> tuple[Any, ...]:
    """The only outbox event: (id, occurred_at, status, attempts, available_at, last_error)."""
    (row,) = superuser_query(
        harness.database_url,
        "SELECT id, occurred_at, status, attempts, available_at, last_error FROM app.outbox",
    )
    return row


def test_an_unconfirmed_export_is_retried_with_backoff_then_processed(harness: Harness) -> None:
    """The handler waits for PostHog to confirm the export. Until it does, the event is a failed
    attempt, retried with backoff, and the retry is the same PostHog event: the outbox event's ID
    and the time it occurred, never the time of the retry."""
    record_plan_change(harness)
    event_id, occurred_at, *_ = outbox_row(harness)
    outbox = harness.container.kernel.outbox
    harness.analytics.export_ok = False

    assert outbox.drain().summary() == {"claimed": 1, "processed": 0, "retried": 1, "dead": 0, "superseded": 0}
    _, _, status, attempts, available_at, error = outbox_row(harness)
    assert (status, attempts) == ("pending", 1)
    assert available_at == harness.clock.now() + timedelta(seconds=2)
    assert "billing.analytics" in error
    assert outbox.drain().claimed == 0  # backoff: not due yet

    harness.analytics.export_ok = True
    harness.clock.advance(seconds=2)
    assert outbox.drain().processed == 1
    assert outbox_row(harness)[2:4] == ("processed", 2)
    assert [e[1:] for e in harness.analytics.events] == [("plan_changed", {"plan": "pro"})] * 2
    assert harness.analytics.identities == [(event_id, occurred_at)] * 2


def test_a_project_whose_export_is_unconfirmed_is_exported_later_as_the_same_event(harness: Harness) -> None:
    """After-response dispatch cannot confirm the export: the request still succeeds (OBS-06), the
    event stays pending, and drain-outbox sends it later, stamped with the time it occurred."""
    harness.analytics.export_ok = False
    r = harness.client.post("/api/v1/projects", json={"name": "tracked"}, headers=harness.login("user_a"))
    assert r.status_code == 201
    event_id, occurred_at, status, attempts, _, error = outbox_row(harness)
    assert (status, attempts) == ("pending", 1)
    assert "projects.analytics" in error

    harness.analytics.export_ok = True
    harness.clock.advance(minutes=5)
    assert harness.container.kernel.outbox.drain().processed == 1
    assert [e[1:] for e in harness.analytics.events] == [("project_created", {"context": "personal"})] * 2
    assert harness.analytics.identities == [(event_id, occurred_at)] * 2


def test_a_redelivery_after_the_worker_died_sends_the_same_event(harness: Harness) -> None:
    """The worker exported the event and died before recording the outcome. Once the lease expires
    the event is delivered again, as the same PostHog event, which PostHog deduplicates."""
    kernel = harness.container.kernel
    died: list[StoredEvent] = []

    def die_once(event: StoredEvent) -> None:
        if not died:
            died.append(event)
            raise WorkerDied

    kernel.handlers.subscribe(EntitlementChanged, "test.worker_dies", die_once)  # runs after billing.analytics
    record_plan_change(harness)
    event_id, occurred_at, *_ = outbox_row(harness)
    with pytest.raises(WorkerDied):
        kernel.outbox.drain()
    harness.clock.advance(seconds=harness.container.settings.outbox_lease_seconds + 1)
    assert kernel.outbox.drain().retried == 1  # the expired lease is a failed attempt
    harness.clock.advance(seconds=2)
    assert kernel.outbox.drain().processed == 1
    assert [e[1] for e in harness.analytics.events] == ["plan_changed", "plan_changed"]
    assert harness.analytics.identities == [(event_id, occurred_at)] * 2


def test_a_burst_well_above_the_dispatch_slots_is_dispatched_while_posthog_hangs(
    make_harness: Callable[..., Harness], caplog: pytest.LogCaptureFixture
) -> None:
    """While PostHog does not answer, only the first exports wait out the confirm timeout (2 s):
    the next ones fail fast until the cooldown is over. So the exports never hold the after-response
    dispatch slots (DATABASE_WORKER_POOL_MAX_SIZE - 1 per process, each waited for at most
    DATABASE_CONNECT_TIMEOUT_SECONDS) for more than one confirm timeout. At the default sizes and
    timeouts, a burst of eight times the slots has every event dispatched right after its response:
    every other handler runs, and each unconfirmed export is a failed attempt the outbox retries,
    instead of an event left for drain-outbox without an attempt."""
    answer = threading.Event()

    def hang(_request: Recorded) -> tuple[int, Any]:
        answer.wait(30)
        return 200, {"status": 1}

    with stub_server(hang) as stub:
        h = make_harness(analytics=PostHogAnalytics(api_key="phc_test_only", host=stub.url))
        slots = h.container.settings.database_worker_pool_max_size - 1
        writes = 8 * slots
        delivered: list[UUID] = []
        h.container.kernel.handlers.subscribe(ProjectCreated, "test.other_module", lambda e: delivered.append(e.id))
        headers = [h.login(f"user_{i}") for i in range(writes)]
        statuses: list[int] = []
        try:
            # One event loop for every request, as in one server process: they share the dispatch slots.
            with (
                caplog.at_level(logging.WARNING),
                TestClient(create_app(h.container), raise_server_exceptions=False) as client,
            ):
                together = threading.Barrier(writes)

                def write(i: int) -> None:
                    together.wait(10)
                    r = client.post("/api/v1/projects", json={"name": f"p{i}"}, headers=headers[i])
                    statuses.append(r.status_code)

                threads = [threading.Thread(target=write, args=(i,)) for i in range(writes)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(30)
        finally:
            answer.set()
    assert statuses == [201] * writes
    rows = superuser_query(h.database_url, "SELECT status, attempts, last_error FROM app.outbox")
    assert [(status, attempts) for status, attempts, _ in rows] == [("pending", 1)] * writes
    assert all("projects.analytics" in error for _, _, error in rows)
    assert len(delivered) == writes
    assert [r for r in caplog.records if r.getMessage() == "after_response_dispatch_deferred"] == []
    assert len(stub.requests) <= slots  # one send per slot at most: the others failed fast
