"""Outbox dispatch: runs event handlers after commit, at least once.

Events are leased with ``FOR UPDATE SKIP LOCKED``, so any number of workers
(after-response dispatch in each request, plus the ``drain-outbox`` job) can
drain concurrently without double-leasing a row. Events are leased one at a
time, just before their handlers run, so a lease covers exactly one event. A
lease that is never completed, because the process died or the platform
stopped it, expires and the event is retried.

A failed event is retried with exponential backoff and marked ``dead`` after
``max_attempts``, and so is an event whose lease keeps expiring (its handler
crashes or outlives the function). A retry re-runs every handler for that
event, which is why handlers must be idempotent.
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


@dataclass(slots=True)
class DrainReport:
    claimed: int = 0
    processed: int = 0
    retried: int = 0
    dead: int = 0
    # Outcomes not recorded because the lease expired and another worker took the event.
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
        batch_size: int,
        max_attempts: int,
        lease_seconds: int = 120,
    ) -> None:
        self._tx = tx
        self._handlers = handlers
        self._clock = clock
        self._batch_size = batch_size
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
        """Dispatch due events.

        - With ``ids`` (after-response dispatch): only those events.
        - Otherwise up to ``batch_size`` events, or, with a time budget (the
          ``drain-outbox`` job), until no event is due or the budget is spent.
        """
        report = DrainReport()
        if ids is not None and not ids:
            return report
        if ids is None:
            with self._tx.as_system() as t:
                expired = t.repo.expire_outbox_leases(self._clock.now(), self._max_attempts)
            if expired:
                report.dead += expired
                logger.error("outbox_lease_expired_on_last_attempt", extra={"events": expired})
        limit = len(ids) if ids is not None else self._batch_size
        deadline = time.monotonic() + time_budget_seconds if time_budget_seconds is not None else None
        while (deadline is not None and time.monotonic() < deadline) or (deadline is None and report.claimed < limit):
            with self._tx.as_system() as t:
                events = t.repo.claim_outbox(limit=1, now=self._clock.now(), lease_seconds=self._lease_seconds, ids=ids)
            if not events:
                break
            report.claimed += 1
            self._dispatch(events[0], report)
        return report

    def _dispatch(self, event: StoredEvent, report: DrainReport) -> None:
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

        with self._tx.as_system() as t:
            if not failed:
                if t.repo.complete_outbox(event.id, event.attempts, self._clock.now()):
                    report.processed += 1
                else:
                    report.superseded += 1
                return
            error = "handlers failed: " + ", ".join(failed)
            report.failures.append(f"{event.event_type}:{event.id}")
            if event.attempts >= self._max_attempts:
                if not t.repo.fail_outbox(event.id, event.attempts, error, retry_at=None):
                    report.superseded += 1
                    return
                report.dead += 1
                logger.error("outbox_event_dead", extra={"event_id": str(event.id), "event_type": event.event_type})
            else:
                delay = min(2**event.attempts, _MAX_BACKOFF_SECONDS)
                retry_at = self._clock.now() + timedelta(seconds=delay)
                if not t.repo.fail_outbox(event.id, event.attempts, error, retry_at=retry_at):
                    report.superseded += 1
                    return
                report.retried += 1


def prune_in_batches(delete_batch: Callable[[], int], batch_size: int, time_budget_seconds: float) -> int:
    """Run ``delete_batch`` until it deletes fewer than ``batch_size`` rows or the budget is spent."""
    deadline = time.monotonic() + time_budget_seconds
    total = 0
    while True:
        deleted = delete_batch()
        total += deleted
        if deleted < batch_size or time.monotonic() >= deadline:
            return total
