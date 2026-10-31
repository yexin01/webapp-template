"""Single flight and minimum spacing for jobs started over HTTP (SEC-09).

``GET /api/v1/jobs/<name>`` runs a job on every authorized call. Whoever holds ``CRON_SECRET`` could
start the same job many times at once, or in a loop (``reconcile-billing`` makes up to
``BILLING_RECONCILE_BATCH_SIZE`` Stripe calls a run). The guard lets one run of a job proceed at a time,
across every instance, and no sooner than ``JOB_MIN_SPACING_SECONDS`` after the previous start. The
command line does not use it.

A run claims the job's row in PostgreSQL, runs, and releases the row. Claim and release are single
statements in transactions of their own; nothing is held while the job runs, so a transaction pooler is
no obstacle. The claim holds the job for ``LEASE_SECONDS``, longer than any function may run (Vercel: 300 s
by default, up to 800 s), so a lease outlives its run only when the run never ended: a crash, or the
platform stopped the function. A failed run releases the job like any other; the spacing still counts
from its start.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta

from app.kernel.errors import ConflictError, RateLimitedError
from app.kernel.ports import Clock, KernelStore, Transactions

logger = logging.getLogger(__name__)

LEASE_SECONDS = 900


class JobGuard:
    def __init__(
        self,
        tx: Transactions[KernelStore],
        clock: Clock,
        *,
        min_spacing_seconds: int,
        lease_seconds: int = LEASE_SECONDS,
    ) -> None:
        self._tx = tx
        self._clock = clock
        self._spacing = timedelta(seconds=min_spacing_seconds)
        self._lease = timedelta(seconds=lease_seconds)

    @contextmanager
    def exclusive(self, name: str) -> Iterator[None]:
        """Hold the job ``name`` for the block, or raise ``ConflictError`` (a run is in progress) or
        ``RateLimitedError`` (the last run started too recently)."""
        started_at = self._claim(name)
        try:
            yield
        finally:
            self._release(name, started_at)

    def _claim(self, name: str) -> datetime:
        now = self._clock.now()
        with self._tx.as_system() as t:
            if t.repo.claim_job_run(name, now=now, lease_until=now + self._lease, spaced_before=now - self._spacing):
                return now
            last = t.repo.job_run(name)
        if last is None or last.lease_until > now:
            logger.warning("job_refused", extra={"job": name, "reason": "running"})
            raise ConflictError("A run of this job is already in progress.")
        wait = math.ceil((last.started_at + self._spacing - now).total_seconds())
        logger.warning("job_refused", extra={"job": name, "reason": "too_soon"})
        raise RateLimitedError(wait, "This job started too recently. Try again later.")

    def _release(self, name: str, started_at: datetime) -> None:
        try:
            with self._tx.as_system() as t:
                t.repo.release_job_run(name, started_at, self._clock.now())
        except Exception:
            # The run is over and its outcome stands. The lease expires by itself.
            logger.exception("job_release_failed", extra={"job": name})
