"""Fixed-window rate limiting backed by shared PostgreSQL counters (DEC-011)."""

from __future__ import annotations

from datetime import UTC, datetime

from app.domain.errors import RateLimitedError
from app.ports import Clock, UnitOfWork


class RateLimiter:
    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    def hit(self, bucket: str, limit: int, window_seconds: int) -> None:
        """Count one hit; raise ``RateLimitedError`` once ``limit`` is exceeded.

        The counter is committed in its own transaction so that a failing
        request still consumes its allowance.
        """
        epoch = int(self._clock.now().timestamp())
        start = epoch - epoch % window_seconds
        with self._uow.as_system() as repo:
            count = repo.increment_counter(bucket, datetime.fromtimestamp(start, UTC))
        if count > limit:
            raise RateLimitedError(retry_after_seconds=start + window_seconds - epoch)
