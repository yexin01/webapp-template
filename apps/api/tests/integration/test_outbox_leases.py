"""Outbox leases: an event whose worker is gone is retried behind the events
already due, never runs more than ``max_attempts`` times, and a live handler
never loses its lease to another drain (ADR-0003, ARCH-15)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from app.kernel.application.outbox import OutboxDispatcher
from app.kernel.events import EventHandlers, StoredEvent
from app.kernel.tenancy import VerifiedIdentity
from app.modules.billing.domain import EntitlementChanged, Plan
from tests.conftest import Harness, superuser_query

pytestmark = pytest.mark.db

LEASE_SECONDS = 60


class WorkerDied(BaseException):
    """The process running the handlers died, or the platform stopped it."""


def record(harness: Harness, user: str) -> str:
    """Record one event outside any request, so nothing dispatches it; return the user's internal ID."""
    ctx = harness.container.identity.resolver.personal(VerifiedIdentity(user))
    with harness.container.kernel.store.as_tenant(ctx) as t:
        t.record(EntitlementChanged(user_id=ctx.user_id, plan=Plan.PRO))
    return str(ctx.user_id)


def dispatcher(harness: Harness, handler: Any, max_attempts: int = 3) -> OutboxDispatcher:
    handlers = EventHandlers()
    handlers.subscribe(EntitlementChanged, "test.handler", handler)
    return OutboxDispatcher(
        harness.container.kernel.store, handlers, harness.clock, max_attempts=max_attempts, lease_seconds=LEASE_SECONDS
    )


def rows_by_user(harness: Harness) -> dict[str, tuple[str, int]]:
    rows = superuser_query(harness.database_url, "SELECT payload->>'user_id', status, attempts FROM app.outbox")
    return {user: (status, attempts) for user, status, attempts in rows}


def test_an_event_whose_worker_died_goes_behind_the_events_already_due(harness: Harness) -> None:
    """A crash-looping event first in line must not block every drain until it is dead-lettered."""
    poison = record(harness, "user_poison")
    harness.clock.advance(seconds=1)
    good = record(harness, "user_good")
    handled: list[str] = []

    def handler(event: StoredEvent) -> None:
        if event.payload["user_id"] == poison:
            raise WorkerDied
        handled.append(str(event.payload["user_id"]))

    drain = dispatcher(harness, handler)
    with pytest.raises(WorkerDied):
        drain.drain()  # the poison event is first in line, and its worker dies
    harness.clock.advance(seconds=LEASE_SECONDS + 1)
    report = drain.drain()
    assert handled == [good]
    assert (report.retried, report.processed) == (1, 1)
    assert rows_by_user(harness) == {poison: ("pending", 1), good: ("processed", 1)}
    status = superuser_query(harness.database_url, "SELECT last_error FROM app.outbox WHERE status = 'pending'")
    assert status == [("lease expired: the worker stopped or timed out",)]


def test_a_lease_that_expires_during_a_drain_is_not_leased_past_the_attempt_limit(harness: Harness) -> None:
    """Expired leases are recorded before leasing, so an event never runs more than ``max_attempts`` times."""
    stale = record(harness, "user_stale")
    harness.clock.advance(seconds=1)
    fresh = record(harness, "user_fresh")
    # A worker leased the first event on its last attempt (3 of 3) and is gone; the lease runs for 10 s more.
    superuser_query(
        harness.database_url,
        "UPDATE app.outbox SET status = 'processing', attempts = 3, locked_until = %s WHERE payload->>'user_id' = %s",
        (harness.clock.now() + timedelta(seconds=10), stale),
    )
    runs: list[str] = []

    def handler(event: StoredEvent) -> None:
        runs.append(str(event.payload["user_id"]))
        harness.clock.advance(seconds=11)  # meanwhile, the stale lease expires

    drain = dispatcher(harness, handler, max_attempts=3)
    assert drain.drain().processed == 1
    assert runs == [fresh]  # the stale event already had its three attempts
    report = drain.drain()
    assert (report.dead, report.claimed) == (1, 0)
    assert runs == [fresh]
    assert rows_by_user(harness)[stale] == ("dead", 3)


def test_after_response_dispatch_records_expired_leases_too(harness: Harness) -> None:
    """The after-response path (``ids``) never leases an event whose lease expired on its last attempt."""
    user = record(harness, "user_a")
    event_id = superuser_query(harness.database_url, "SELECT id FROM app.outbox")[0][0]
    superuser_query(
        harness.database_url,
        "UPDATE app.outbox SET status = 'processing', attempts = 3, locked_until = %s",
        (harness.clock.now() - timedelta(seconds=1),),
    )
    runs: list[StoredEvent] = []
    report = dispatcher(harness, runs.append, max_attempts=3).drain([event_id])
    assert runs == []
    assert (report.dead, report.claimed) == (1, 0)
    assert rows_by_user(harness)[user] == ("dead", 3)


def test_a_superseded_failure_is_not_reported_as_a_failure(harness: Harness) -> None:
    """A worker whose lease was taken over records nothing, and reports only that it was superseded."""
    record(harness, "user_a")

    def handler(event: StoredEvent) -> None:
        # While the handler runs, its lease expires and another drain records it as a retry.
        superuser_query(
            harness.database_url,
            "UPDATE app.outbox SET status = 'pending', locked_until = NULL, available_at = %s WHERE id = %s",
            (harness.clock.now() + timedelta(seconds=2), event.id),
        )
        raise RuntimeError("downstream down")

    report = dispatcher(harness, handler).drain()
    assert (report.superseded, report.retried, report.failures) == (1, 0, [])


def test_a_lease_outlasts_the_longest_function_run(harness: Harness) -> None:
    """A live handler never loses its lease: the default lease is longer than any Vercel function may run."""
    lease = harness.container.settings.outbox_lease_seconds
    assert lease > 800  # Vercel: 300 s by default, at most 800 s
    record(harness, "user_a")
    harness.container.kernel.handlers.subscribe(EntitlementChanged, "test.crash", _crash)
    with pytest.raises(WorkerDied):
        harness.container.kernel.outbox.drain()
    locked_until = superuser_query(harness.database_url, "SELECT locked_until FROM app.outbox")[0][0]
    assert locked_until == harness.clock.now() + timedelta(seconds=lease)


def _crash(event: StoredEvent) -> None:
    raise WorkerDied


def test_events_left_without_attempts_by_a_lower_limit_are_dead_lettered(harness: Harness) -> None:
    """After ``OUTBOX_MAX_ATTEMPTS`` is lowered, a pending event that already used the new limit can never be
    leased. The drain job marks it dead, where it is visible, instead of leaving it pending forever."""
    user = record(harness, "user_a")
    superuser_query(harness.database_url, "UPDATE app.outbox SET attempts = 5")
    runs: list[StoredEvent] = []
    drain = dispatcher(harness, runs.append, max_attempts=3)
    event_id = superuser_query(harness.database_url, "SELECT id FROM app.outbox")[0][0]
    assert drain.drain([event_id]).dead == 0  # after-response dispatch leaves this to the job
    report = drain.drain()
    assert (report.dead, report.claimed, runs) == (1, 0, [])
    assert rows_by_user(harness)[user] == ("dead", 5)


def test_an_outcome_is_kept_when_leasing_the_next_event_fails(harness: Harness) -> None:
    """Each outcome is committed on its own: a failure while leasing the next event cannot undo it."""
    user = record(harness, "user_a")
    drain = dispatcher(harness, lambda event: None)
    real_claim = drain._claim
    claims = 0

    def claim_then_fail(repo: Any, ids: Any) -> StoredEvent | None:
        nonlocal claims
        claims += 1
        if claims == 2:
            raise RuntimeError("the database went away while leasing the next event")
        return real_claim(repo, ids)

    drain._claim = claim_then_fail  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        drain.drain()
    assert rows_by_user(harness)[user] == ("processed", 1)
