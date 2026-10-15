"""Outbox dispatch: runs event handlers after commit, at least once.

Events are leased with ``FOR UPDATE SKIP LOCKED``, so any number of workers
(after-response dispatch in each request, plus the ``drain-outbox`` job) can
drain concurrently without double-leasing a row. Events are leased one at a
time, just before their handlers run, so a lease covers exactly one event.

A lease lasts longer than a function may run (``OUTBOX_LEASE_SECONDS``), so it
expires only once its worker is gone: the process died, or the platform
stopped it. Every drain first records each expired lease as a failed attempt,
like a failed handler: the event is retried with exponential backoff, behind
the events already due, or marked ``dead`` once it has used ``max_attempts``.
Only pending events with attempts left are leased, so no event runs more than
``max_attempts`` times. A retry re-runs every handler for that event, which is
why handlers must be idempotent.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from uuid import UUID

from app.kernel.events import EventHandlers, StoredEvent
from app.kernel.ports import Clock, KernelStore, Transactions

logger = logging.getLogger(__name__)

_MAX_BACKOFF_SECONDS = 3_600
_PRUNE_BATCH = 1_000
_EXPIRED_BATCH = 100  # expired leases recorded per drain; the next drain continues
_LEASE_EXPIRED = "lease expired: the worker stopped or timed out"


@dataclass(slots=True)
class DrainReport:
    claimed: int = 0
    processed: int = 0
    retried: int = 0
    dead: int = 0
    # Outcomes not recorded because the lease expired and the event moved on without this worker.
    superseded: int = 0
    failures: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, int]:
        return {
            "claimed": self.claimed,
            "processed": self.processed,
            "retried": self.retried,
            "dead": self.dead,
            "superseded": self.superseded,
        }


class OutboxDispatcher:
    def __init__(
        self,
        tx: Transactions[KernelStore],
        handlers: EventHandlers,
        clock: Clock,
        *,
        max_attempts: int,
        lease_seconds: int,
    ) -> None:
        self._tx = tx
        self._handlers = handlers
        self._clock = clock
        self._max_attempts = max_attempts
        self._lease_seconds = lease_seconds

    def prune(self, retention: timedelta, time_budget_seconds: float) -> int:
        """Delete processed events older than ``retention``; they hold internal user IDs.

        Deletes in batches, each its own short transaction, until none are left
        or the time budget is spent; the next run continues.
        """
        cutoff = self._clock.now() - retention

        def batch() -> int:
            with self._tx.as_system() as t:
                return t.repo.prune_outbox(cutoff, _PRUNE_BATCH)

        return prune_in_batches(batch, _PRUNE_BATCH, time_budget_seconds)

    def drain(self, ids: Sequence[UUID] | None = None, *, time_budget_seconds: float | None = None) -> DrainReport:
        """Record expired leases, then dispatch due events one at a time.

        - With ``ids`` (after-response dispatch): only those events.
        - Otherwise until no event is due or, with a time budget (the
          ``drain-outbox`` job), until the budget is spent. This path also
          dead-letters pending events that have no attempts left, which only
          happens after ``OUTBOX_MAX_ATTEMPTS`` was lowered.

        An event's outcome is committed on its own, as soon as its handlers
        finish, so a failure while leasing the next event cannot undo it.
        """
        report = DrainReport()
        if ids is not None and not ids:
            return report
        deadline = time.monotonic() + time_budget_seconds if time_budget_seconds is not None else None
        with self._tx.as_system() as t:
            self._expire_leases(t.repo, report)
            if ids is None:
                self._dead_letter_exhausted(t.repo, report)
            event = self._claim(t.repo, ids)
        while event is not None:
            report.claimed += 1
            failed = self._run_handlers(event)
            with self._tx.as_system() as t:
                self._record_outcome(t.repo, event, failed, report)
            if (ids is not None and report.claimed >= len(ids)) or (
                deadline is not None and time.monotonic() >= deadline
            ):
                break
            with self._tx.as_system() as t:
                event = self._claim(t.repo, ids)
        return report

    def _claim(self, store: KernelStore, ids: Sequence[UUID] | None) -> StoredEvent | None:
        events = store.claim_outbox(
            limit=1,
            now=self._clock.now(),
            lease_seconds=self._lease_seconds,
            max_attempts=self._max_attempts,
            ids=ids,
        )
        return events[0] if events else None

    def _dead_letter_exhausted(self, store: KernelStore, report: DrainReport) -> None:
        dead = store.dead_letter_exhausted_outbox(self._max_attempts, _EXPIRED_BATCH)
        if dead:
            report.dead += dead
            logger.error("outbox_events_exhausted", extra={"events": dead, "max_attempts": self._max_attempts})

    def _expire_leases(self, store: KernelStore, report: DrainReport) -> None:
        for event_id, attempt in store.expired_outbox_leases(self._clock.now(), _EXPIRED_BATCH):
            logger.warning("outbox_lease_expired", extra={"event_id": str(event_id), "attempt": attempt})
            self._fail(store, event_id, attempt, _LEASE_EXPIRED, report)

    def _run_handlers(self, event: StoredEvent) -> list[str]:
        """Run every handler for the event; return the names of those that failed."""
        failed: list[str] = []
        for name, handler in self._handlers.for_type(event.event_type):
            try:
                handler(event)
            except Exception:
                # Never log the payload; the event ID is enough to inspect the row.
                logger.exception(
                    "outbox_handler_failed",
                    extra={"event_id": str(event.id), "event_type": event.event_type, "handler": name},
                )
                failed.append(name)
        return failed

    def _record_outcome(self, store: KernelStore, event: StoredEvent, failed: list[str], report: DrainReport) -> None:
        if not failed:
            if store.complete_outbox(event.id, event.attempts, self._clock.now()):
                report.processed += 1
            else:
                report.superseded += 1
            return
        if self._fail(store, event.id, event.attempts, "handlers failed: " + ", ".join(failed), report):
            report.failures.append(f"{event.event_type}:{event.id}")

    def _fail(self, store: KernelStore, event_id: UUID, attempt: int, error: str, report: DrainReport) -> bool:
        """Record a failed attempt: a retry after backoff, or ``dead`` after the last attempt.

        Returns False if the lease no longer holds the event, so nothing was recorded.
        """
        last = attempt >= self._max_attempts
        retry_at = None if last else self._clock.now() + timedelta(seconds=min(2**attempt, _MAX_BACKOFF_SECONDS))
        if not store.fail_outbox(event_id, attempt, error, retry_at=retry_at):
            report.superseded += 1
            return False
        if last:
            report.dead += 1
            logger.error("outbox_event_dead", extra={"event_id": str(event_id), "attempts": attempt})
        else:
            report.retried += 1
        return True


def prune_in_batches(delete_batch: Callable[[], int], batch_size: int, time_budget_seconds: float) -> int:
    """Run ``delete_batch`` until it deletes fewer than ``batch_size`` rows or the budget is spent."""
    deadline = time.monotonic() + time_budget_seconds
    total = 0
    while True:
        deleted = delete_batch()
        total += deleted
        if deleted < batch_size or time.monotonic() >= deadline:
            return total
