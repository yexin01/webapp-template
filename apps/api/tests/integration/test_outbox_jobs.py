"""Transactional outbox and scheduled jobs (ADR-0003)."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest

from app.kernel.application.outbox import OutboxDispatcher
from app.kernel.events import EventHandlers, StoredEvent
from app.kernel.infrastructure.config import REPO_ROOT
from app.kernel.tenancy import Role
from app.modules.billing.domain import EntitlementChanged, Plan, SubscriptionStatus
from app.modules.identity.domain import ExternalMembership
from tests.conftest import Harness, superuser_query
from tests.integration.test_billing import customer_of, event_payload, make_pro, send_webhook, snapshot

pytestmark = pytest.mark.db

CRON_SECRET = "cron-secret-test_only-0123456789abcdef0123"  # test-only value


def outbox_rows(h: Harness) -> list[tuple[Any, ...]]:
    return superuser_query(
        h.database_url, "SELECT event_type, status, attempts, payload FROM app.outbox ORDER BY occurred_at"
    )


# -- Outbox ------------------------------------------------------------------------


def test_event_is_recorded_with_the_change_and_dispatched_after_the_response(harness: Harness) -> None:
    headers = harness.login("user_a")
    r = harness.client.post("/api/v1/projects", json={"name": "evented"}, headers=headers)
    assert r.status_code == 201
    rows = outbox_rows(harness)
    assert [(t, s, a) for t, s, a, _ in rows] == [("projects.created", "processed", 1)]
    assert rows[0][3]["project_id"] == r.json()["id"]
    assert ("project_created", {"context": "personal"}) in [(e, p) for _, e, p in harness.analytics.events]


def recording_responses(app: Any, order: list[str]) -> Any:
    """``app``, noting in ``order`` when each response starts."""

    async def recording(scope: Any, receive: Any, send: Any) -> None:
        async def spy(message: Any) -> None:
            if message["type"] == "http.response.start":
                order.append(f"response {message['status']}")
            await send(message)

        await app(scope, receive, spy)

    return recording


# The conflict is raised by a patched service: the project routes cannot answer 409 (not evidence about the contract).
@pytest.mark.parametrize("outcome", ["created", pytest.param("conflict", marks=pytest.mark.contract_exempt)])
def test_events_are_dispatched_after_the_response(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    """Handlers run after the response is sent, for error responses too: a domain error after a
    write commits the write and its event, and its 4xx must not wait for the event's handlers."""
    from fastapi.testclient import TestClient

    from app.kernel.errors import ConflictError
    from app.modules.projects.domain import ProjectCreated

    order: list[str] = []
    harness.container.kernel.handlers.subscribe(ProjectCreated, "test.order", lambda event: order.append("handler"))
    if outcome == "conflict":
        service = harness.container.projects.service
        create = service.create

        def create_then_conflict(*args: Any, **kwargs: Any) -> Any:
            create(*args, **kwargs)
            raise ConflictError()

        monkeypatch.setattr(service, "create", create_then_conflict)
    client = TestClient(recording_responses(harness.client.app, order), raise_server_exceptions=False)
    r = client.post("/api/v1/projects", json={"name": "ordered"}, headers=harness.login("user_a"))
    assert r.status_code == (201 if outcome == "created" else 409)
    assert order == [f"response {r.status_code}", "handler"]


@pytest.mark.parametrize("committed", [False, True], ids=["rolled-back", "committed-before-the-error"])
def test_an_unhandled_error_dispatches_nothing_and_its_500_never_waits(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, committed: bool
) -> None:
    """An unhandled error passes the after-response dispatch before any response exists, so nothing
    is dispatched and the 500 is sent at once. The request transaction rolls back with its event;
    an event committed outside it (after ``release()``) is left to drain-outbox, not lost."""
    from fastapi.testclient import TestClient

    from app.modules.projects.domain import ProjectCreated

    kernel = harness.container.kernel
    order: list[str] = []
    kernel.handlers.subscribe(ProjectCreated, "test.order", lambda event: order.append("handler"))
    drain = kernel.outbox.drain

    def spied_drain(*args: Any, **kwargs: Any) -> Any:
        order.append("drain")
        return drain(*args, **kwargs)

    monkeypatch.setattr(kernel.outbox, "drain", spied_drain)
    service = harness.container.projects.service
    create = service.create

    def create_then_fail(*args: Any, **kwargs: Any) -> Any:
        if committed:
            kernel.database.release_request_transaction()  # as before a slow external call
        create(*args, **kwargs)
        raise RuntimeError("a bug after the write")

    monkeypatch.setattr(service, "create", create_then_fail)
    client = TestClient(recording_responses(harness.client.app, order), raise_server_exceptions=False)
    r = client.post("/api/v1/projects", json={"name": "failed"}, headers=harness.login("user_a"))
    assert r.status_code == 500
    assert order == ["response 500"]  # no dispatch at all, neither before the 500 nor after it
    left = [("projects.created", "pending")] if committed else []
    assert [(t, s) for t, s, _, _ in outbox_rows(harness)] == left
    assert kernel.outbox.drain().processed == len(left)  # what drain-outbox does
    assert order == ["response 500"] + (["drain", "handler"] if committed else ["drain"])


def test_after_response_dispatch_never_holds_request_threads(
    make_harness: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """Handlers run after the response on threads of their own, at most one fewer at a time than the
    worker pool has connections. A dispatch that finds no free slot within the connect timeout is
    left to drain-outbox. Slow handlers never take the request threads that routes need (ADR-0003)."""
    import logging
    import threading
    import time

    import anyio
    from fastapi.testclient import TestClient

    from app.bootstrap import create_app
    from app.modules.projects.domain import ProjectCreated

    h = make_harness({"database_connect_timeout_seconds": 1})
    slots = h.container.settings.database_worker_pool_max_size - 1
    writes = slots + 4
    lock = threading.Lock()
    running = peak = 0
    release = threading.Event()

    def slow_delivery(event: StoredEvent) -> None:  # e.g. an adopter's handler calling a slow service
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        try:
            release.wait(10)
        finally:
            with lock:
                running -= 1

    h.container.kernel.handlers.subscribe(ProjectCreated, "test.slow_delivery", slow_delivery)
    headers = h.login("user_a")
    statuses: list[int] = []

    def deferred() -> int:
        return sum(r.getMessage() == "after_response_dispatch_deferred" for r in list(caplog.records))

    def post(name: str) -> None:
        statuses.append(client.post("/api/v1/projects", json={"name": name}, headers=headers).status_code)

    with caplog.at_level(logging.WARNING), TestClient(create_app(h.container), raise_server_exceptions=False) as client:
        posts = [threading.Thread(target=post, args=(f"p{i}",)) for i in range(writes)]
        for thread in posts:
            thread.start()
        try:
            give_up = time.monotonic() + 10
            while running + deferred() < writes:  # every dispatch is running, or was deferred
                assert time.monotonic() < give_up, (running, deferred())
                time.sleep(0.01)
            borrowed = client.portal.call(lambda: anyio.to_thread.current_default_thread_limiter().borrowed_tokens)
            started = time.monotonic()
            assert client.get("/api/v1/health").status_code == 200
            health_seconds = time.monotonic() - started
        finally:
            release.set()
            for thread in posts:
                thread.join(10)
    assert borrowed == 0, f"event handlers held {borrowed} request threads"
    assert peak == slots
    assert health_seconds < 0.5
    assert statuses == [201] * writes
    rows = [(t, s) for t, s, _, _ in outbox_rows(h)]
    assert (
        sorted(rows)
        == [("projects.created", "pending")] * (writes - slots) + [("projects.created", "processed")] * slots
    )
    assert h.container.kernel.outbox.drain().processed == writes - slots  # deferred, not lost


def test_no_event_without_the_change(harness: Harness) -> None:
    headers = harness.login("user_a")
    assert harness.client.post("/api/v1/projects", json={"name": "   "}, headers=headers).status_code in (400, 422)
    assert outbox_rows(harness) == []


def test_request_role_can_only_append_to_the_outbox(harness: Harness) -> None:
    with psycopg.connect(harness.database_url) as conn, conn.transaction():
        conn.execute("SET LOCAL ROLE app_api")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT * FROM app.outbox")
    with psycopg.connect(harness.database_url) as conn, conn.transaction():
        conn.execute("SET LOCAL ROLE app_api")
        # Without a user context, even appending is refused (RLS).
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("INSERT INTO app.outbox (id, event_type) VALUES (gen_random_uuid(), 'projects.created')")


LEASE_SECONDS = 60


def _dispatcher(harness: Harness, handlers: EventHandlers, max_attempts: int = 3) -> OutboxDispatcher:
    kernel = harness.container.kernel
    return OutboxDispatcher(
        kernel.store, handlers, harness.clock, max_attempts=max_attempts, lease_seconds=LEASE_SECONDS
    )


def _record(harness: Harness, user: str = "user_a") -> None:
    """Record one event directly, outside any request, so nothing dispatches it."""
    from app.kernel.tenancy import VerifiedIdentity

    ctx = harness.container.identity.resolver.personal(VerifiedIdentity(user))
    with harness.container.kernel.store.as_tenant(ctx) as t:
        t.record(EntitlementChanged(user_id=ctx.user_id, plan=Plan.PRO))


def test_failed_handler_retries_with_backoff_then_dead_letters(harness: Harness) -> None:
    calls: list[StoredEvent] = []

    def flaky(event: StoredEvent) -> None:
        calls.append(event)
        raise RuntimeError("downstream down")

    handlers = EventHandlers()
    handlers.subscribe(EntitlementChanged, "test.flaky", flaky)
    dispatcher = _dispatcher(harness, handlers, max_attempts=3)
    _record(harness)

    assert dispatcher.drain().retried == 1
    assert dispatcher.drain().claimed == 0  # backoff: not due yet
    harness.clock.advance(seconds=2)
    assert dispatcher.drain().retried == 1
    harness.clock.advance(seconds=4)
    report = dispatcher.drain()
    assert report.dead == 1
    status, attempts, error = superuser_query(
        harness.database_url, "SELECT status, attempts, last_error FROM app.outbox"
    )[0]
    assert (status, attempts) == ("dead", 3)
    assert "test.flaky" in error
    assert len(calls) == 3


def test_expired_lease_is_reclaimed(harness: Harness) -> None:
    seen: list[StoredEvent] = []
    handlers = EventHandlers()
    handlers.subscribe(EntitlementChanged, "test.ok", seen.append)
    _record(harness)
    # Simulate a worker that leased the event and died.
    superuser_query(
        harness.database_url,
        "UPDATE app.outbox SET status = 'processing', attempts = 1, locked_until = %s",
        (harness.clock.now() + timedelta(seconds=60),),
    )
    dispatcher = _dispatcher(harness, handlers)
    assert dispatcher.drain().claimed == 0
    harness.clock.advance(seconds=61)
    # The expired lease counts as a failed attempt: retried after backoff, behind events already due.
    assert dispatcher.drain().retried == 1
    harness.clock.advance(seconds=2)
    assert dispatcher.drain().processed == 1
    assert len(seen) == 1


def test_billing_sync_emits_entitlement_change_once(harness: Harness) -> None:
    customer = make_pro(harness, "user_a")
    rows = [r for r in outbox_rows(harness) if r[0] == "billing.entitlement_changed"]
    assert [(r[1], r[3]["plan"]) for r in rows] == [("processed", "pro")]
    assert ("plan_changed", {"plan": "pro"}) in [(e, p) for _, e, p in harness.analytics.events]

    # A duplicate or no-op sync changes nothing, so no new event.
    assert send_webhook(harness, event_payload(customer)).json()["status"] == "processed"
    assert len([r for r in outbox_rows(harness) if r[0] == "billing.entitlement_changed"]) == 1

    harness.billing.set_subscriptions(customer, [snapshot(customer, SubscriptionStatus.CANCELED)])
    assert send_webhook(harness, event_payload(customer)).json()["status"] == "processed"
    plans = [r[3]["plan"] for r in outbox_rows(harness) if r[0] == "billing.entitlement_changed"]
    assert plans == ["pro", "free"]
    assert customer_of(harness, "user_a") == customer


# -- Jobs ----------------------------------------------------------------------------


@pytest.fixture
def cron(make_harness: Any) -> Harness:
    return make_harness(settings_overrides={"cron_secret": CRON_SECRET})


def test_jobs_endpoint_is_off_without_cron_secret(harness: Harness) -> None:
    r = harness.client.get("/api/v1/jobs/drain-outbox", headers={"Authorization": f"Bearer {CRON_SECRET}"})
    assert r.status_code == 404


@pytest.mark.parametrize("header", [None, "Bearer wrong-secret", f"Basic {CRON_SECRET}", CRON_SECRET])
def test_jobs_endpoint_requires_the_secret(cron: Harness, header: str | None) -> None:
    headers = {"Authorization": header} if header else {}
    r = cron.client.get("/api/v1/jobs/drain-outbox", headers=headers)
    assert r.status_code == 401
    assert CRON_SECRET not in r.text


def test_unknown_job_is_not_found(cron: Harness) -> None:
    r = cron.client.get("/api/v1/jobs/nope", headers={"Authorization": f"Bearer {CRON_SECRET}"})
    assert r.status_code == 404


def test_job_runs_and_reports_a_summary(cron: Harness) -> None:
    r = cron.client.get("/api/v1/jobs/prune-expired", headers={"Authorization": f"Bearer {CRON_SECRET}"})
    assert r.status_code == 200
    body = r.json()
    assert body["job"] == "prune-expired"
    assert body["ok"] is True
    assert body["summary"] == {"rate_limit_windows": 0, "outbox_events": 0}


def test_jobs_are_not_in_the_public_contract(cron: Harness) -> None:
    assert not any("/jobs" in path for path in cron.client.app.openapi()["paths"])  # type: ignore[attr-defined]


def test_drain_outbox_job_dispatches_leftovers(cron: Harness) -> None:
    _record(cron)
    r = cron.client.get("/api/v1/jobs/drain-outbox", headers={"Authorization": f"Bearer {CRON_SECRET}"})
    assert r.json()["summary"]["processed"] == 1


def test_reconcile_memberships_job_refreshes_stale_mirrors(cron: Harness) -> None:
    cron.directory.set("user_a", [ExternalMembership("org_q", Role.MEMBER)])
    headers = cron.login("user_a")
    assert cron.client.get("/api/v1/orgs/org_q/projects", headers=headers).status_code == 200
    cron.directory.set("user_a", [])  # removed in the identity provider
    cron.clock.advance(seconds=cron.container.settings.membership_max_staleness_seconds + 1)
    r = cron.client.get("/api/v1/jobs/reconcile-memberships", headers={"Authorization": f"Bearer {CRON_SECRET}"})
    assert r.json()["summary"] == {"checked": 1, "refreshed": 1, "failed": 0}
    assert superuser_query(cron.database_url, "SELECT count(*) FROM app.organization_memberships")[0][0] == 0


def test_reconcile_billing_job_reports_without_applying_by_default(cron: Harness) -> None:
    customer = make_pro(cron, "user_a")
    cron.billing.set_subscriptions(customer, [snapshot(customer, SubscriptionStatus.PAST_DUE)])
    r = cron.client.get("/api/v1/jobs/reconcile-billing", headers={"Authorization": f"Bearer {CRON_SECRET}"})
    assert r.json()["summary"] == {"customers": 1, "divergences": 1, "unlinked": 0, "rejected": 0, "applied": False}
    status = superuser_query(cron.database_url, "SELECT status FROM app.subscriptions")[0][0]
    assert status == "active"


def test_vercel_cron_config_matches_the_job_registry(harness: Harness) -> None:
    """The root vercel.json must schedule exactly the registered jobs, at their declared schedules.

    Under Vercel Services, crons are read from the root file only; a service's own vercel.json is ignored.
    """
    config = json.loads((REPO_ROOT / "vercel.json").read_text())
    scheduled = {c["path"]: c["schedule"] for c in config["crons"]}
    registered = {f"/api/v1/jobs/{j.name}": j.schedule for j in harness.container.kernel.jobs.all()}
    assert scheduled == registered


def test_cli_lists_jobs(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, harness: Harness) -> None:
    from app import cli

    monkeypatch.setattr("app.bootstrap.bootstrap", lambda: harness.container)
    monkeypatch.setattr(harness.container, "close", lambda: None)
    assert cli.main(["job"]) == 0
    names = {j["name"] for j in json.loads(capsys.readouterr().out)}
    assert names == {"drain-outbox", "prune-expired", "reconcile-billing", "reconcile-memberships"}


def test_repo_root_is_resolved() -> None:
    assert (Path(REPO_ROOT) / "supabase" / "migrations").is_dir()


def test_prune_deletes_processed_events_past_retention_only(cron: Harness) -> None:
    cron.client.post("/api/v1/projects", json={"name": "old"}, headers=cron.login("user_a"))
    _record(cron)  # stays pending
    auth = {"Authorization": f"Bearer {CRON_SECRET}"}
    assert cron.client.get("/api/v1/jobs/prune-expired", headers=auth).json()["summary"]["outbox_events"] == 0
    cron.clock.advance(days=cron.container.settings.outbox_retention_days, seconds=1)
    assert cron.client.get("/api/v1/jobs/prune-expired", headers=auth).json()["summary"]["outbox_events"] == 1
    assert [r[1] for r in outbox_rows(cron)] == ["pending"]


def test_completion_is_fenced_to_the_current_lease(harness: Harness) -> None:
    """A worker whose lease expired cannot overwrite the outcome of the worker that re-leased the event."""
    _record(harness)
    store = harness.container.kernel.store
    with store.as_system() as t:
        first = t.repo.claim_outbox(limit=1, now=harness.clock.now(), lease_seconds=1, max_attempts=3, ids=None)[0]
    harness.clock.advance(seconds=2)  # the first lease expires
    handlers = EventHandlers()
    handlers.subscribe(EntitlementChanged, "test.ok", lambda event: None)
    dispatcher = _dispatcher(harness, handlers)
    assert dispatcher.drain().retried == 1  # the expired lease is recorded as a failed attempt
    harness.clock.advance(seconds=2)
    assert dispatcher.drain().processed == 1  # a second worker leases the event again and completes it
    with store.as_system() as t:
        # The stale worker finishes late: nothing changes.
        assert t.repo.fail_outbox(first.id, first.attempts, "late failure", retry_at=None) is False
        assert t.repo.complete_outbox(first.id, first.attempts, harness.clock.now()) is False
    assert [(r[1], r[2]) for r in outbox_rows(harness)] == [("processed", 2)]


def test_an_event_whose_worker_keeps_dying_is_dead_lettered(harness: Harness) -> None:
    """ARCH-15: attempts stay bounded even when the handler never returns (a crash or a platform timeout)."""
    _record(harness)

    class WorkerDied(BaseException):
        pass

    def crash(event: StoredEvent) -> None:
        raise WorkerDied

    handlers = EventHandlers()
    handlers.subscribe(EntitlementChanged, "test.crash", crash)
    dispatcher = _dispatcher(harness, handlers, max_attempts=3)
    for attempt in (1, 2, 3):
        with pytest.raises(WorkerDied):
            dispatcher.drain()  # the worker leases the event and dies
        harness.clock.advance(seconds=LEASE_SECONDS + 1)
        report = dispatcher.drain()  # the next drain records the expired lease
        if attempt < 3:
            assert (report.retried, report.claimed) == (1, 0)  # retried after backoff
            harness.clock.advance(seconds=2**attempt)
    assert (report.dead, report.claimed) == (1, 0)
    status, attempts, error = superuser_query(
        harness.database_url, "SELECT status, attempts, last_error FROM app.outbox"
    )[0]
    assert (status, attempts) == ("dead", 3)
    assert "lease expired" in error


def test_a_crashing_handler_never_costs_other_events_an_attempt(harness: Harness) -> None:
    """Events are leased one at a time, so a crash holds only the event being handled."""
    for user in ("user_a", "user_b", "user_c"):
        _record(harness, user)

    class WorkerDied(BaseException):
        pass

    def crash(event: StoredEvent) -> None:
        raise WorkerDied

    handlers = EventHandlers()
    handlers.subscribe(EntitlementChanged, "test.crash", crash)
    with pytest.raises(WorkerDied):
        _dispatcher(harness, handlers).drain()
    rows = superuser_query(harness.database_url, "SELECT status, attempts FROM app.outbox ORDER BY status")
    assert sorted(rows) == [("pending", 0), ("pending", 0), ("processing", 1)]


def test_the_drain_job_stops_starting_work_when_its_budget_is_spent(harness: Harness) -> None:
    for i in range(5):
        _record(harness, f"user_{i}")
    handlers = EventHandlers()
    handlers.subscribe(EntitlementChanged, "test.ok", lambda event: None)
    dispatcher = _dispatcher(harness, handlers)
    assert dispatcher.drain(time_budget_seconds=0).processed == 1  # spent after the first event
    assert dispatcher.drain(time_budget_seconds=30).processed == 4  # the job: until nothing is due


def test_prune_deletes_in_bounded_batches(cron: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.kernel.application import outbox

    monkeypatch.setattr(outbox, "_PRUNE_BATCH", 2)
    for i in range(5):
        cron.client.post("/api/v1/projects", json={"name": f"p{i}"}, headers=cron.login("user_a"))
    cron.clock.advance(days=cron.container.settings.outbox_retention_days, seconds=1)
    assert cron.container.kernel.outbox.prune(timedelta(days=1), time_budget_seconds=30) == 5
