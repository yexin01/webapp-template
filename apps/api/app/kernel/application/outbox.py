"""Outbox dispatch: runs event handlers after commit, at least once.

Events are leased with ``FOR UPDATE SKIP LOCKED``, so any number of workers
(after-response dispatch in each request, plus the ``drain-outbox`` job) can
drain concurrently without double-leasing a row. A lease that is never
completed, because the process died, expires and the event is retried.

A failed event is retried with exponential backoff and marked ``dead`` after
``max_attempts``. A retry re-runs every handler for that event, which is why
handlers must be idempotent.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from uuid import UUID

from app.kernel.events import EventHandlers, StoredEvent
from app.kernel.ports import Clock, KernelStore, Transactions

logger = logging.getLogger(__name__)

_MAX_BACKOFF_SECONDS = 3_600


@dataclass(slots=True)
class DrainReport:
    claimed: int = 0
    processed: int = 0
    retried: int = 0
    dead: int = 0
    failures: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, int]:
        return {"claimed": self.claimed, "processed": self.processed, "retried": self.retried, "dead": self.dead}


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

    def prune(self, retention: timedelta) -> int:
        """Delete processed events older than ``retention``; they hold internal user IDs."""
        with self._tx.as_system() as t:
            return t.repo.prune_outbox(self._clock.now() - retention)

    def drain(self, ids: Sequence[UUID] | None = None) -> DrainReport:
        """Dispatch due events: only ``ids`` if given, otherwise up to one batch."""
        report = DrainReport()
        if ids is not None and not ids:
            return report
        with self._tx.as_system() as t:
            events = t.repo.claim_outbox(
                limit=self._batch_size, now=self._clock.now(), lease_seconds=self._lease_seconds, ids=ids
            )
        report.claimed = len(events)
        for event in events:
            self._dispatch(event, report)
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
                t.repo.complete_outbox(event.id, event.attempts, self._clock.now())
                report.processed += 1
                return
            error = "handlers failed: " + ", ".join(failed)
            report.failures.append(f"{event.event_type}:{event.id}")
            if event.attempts >= self._max_attempts:
                t.repo.fail_outbox(event.id, event.attempts, error, retry_at=None)
                report.dead += 1
                logger.error("outbox_event_dead", extra={"event_id": str(event.id), "event_type": event.event_type})
            else:
                delay = min(2**event.attempts, _MAX_BACKOFF_SECONDS)
                t.repo.fail_outbox(
                    event.id, event.attempts, error, retry_at=self._clock.now() + timedelta(seconds=delay)
                )
                report.retried += 1
