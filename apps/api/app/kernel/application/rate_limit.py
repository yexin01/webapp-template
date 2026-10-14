"""Fixed-window rate limiting backed by shared PostgreSQL counters (DEC-011)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.kernel.errors import RateLimitedError
from app.kernel.ports import Clock, KernelStore, Transactions

_RETENTION = timedelta(days=2)


class RateLimiter:
    def __init__(self, tx: Transactions[KernelStore], clock: Clock) -> None:
        self._tx = tx
        self._clock = clock

    def hit(self, bucket: str, limit: int, window_seconds: int) -> None:
        """Count one hit; raise ``RateLimitedError`` once ``limit`` is exceeded.

        The counter is committed in its own worker transaction, so a failing
        request still consumes its allowance.
        """
        epoch = int(self._clock.now().timestamp())
        start = epoch - epoch % window_seconds
        with self._tx.as_system() as t:
            count = t.repo.increment_counter(bucket, datetime.fromtimestamp(start, UTC))
        if count > limit:
            raise RateLimitedError(retry_after_seconds=start + window_seconds - epoch)

    def prune(self) -> int:
        """Delete windows older than two days (longer than any configured window)."""
        with self._tx.as_system() as t:
            return t.repo.prune_counters(self._clock.now() - _RETENTION)
