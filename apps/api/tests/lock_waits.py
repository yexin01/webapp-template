"""Waiting for a concurrent transaction to block on a lock (TEST-01).

A lock-ordering test proves something only once the other transaction is
queued behind the lock under test. A fixed sleep cannot show that: on a
loaded machine the other thread may not have reached the database yet, and
the test passes without exercising the race. ``wait_for_lock_wait`` polls
``pg_stat_activity`` instead.
"""

from __future__ import annotations

import threading
import time

import psycopg
import pytest


def wait_for_lock_wait(url: str, *, timeout: float = 5.0, or_until: threading.Event | None = None) -> None:
    """Return once another session in ``url``'s database waits on a lock, or once ``or_until`` is set.

    Fails the test after ``timeout`` seconds. In a worker thread that failure ends only the
    thread (pytest reports it as a warning), so the test must check what the thread did.
    """
    stop = threading.Event() if or_until is None else or_until
    deadline = time.monotonic() + timeout
    # Autocommit: each poll is its own transaction, so it reads a fresh pg_stat_activity snapshot.
    with psycopg.connect(url, autocommit=True) as conn:
        while not stop.is_set():
            row = conn.execute(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = current_database() AND wait_event_type = 'Lock' AND pid <> pg_backend_pid()"
            ).fetchone()
            if row is not None and row[0] > 0:
                return
            if time.monotonic() >= deadline:
                pytest.fail(f"no other transaction blocked on a lock within {timeout} s")
            stop.wait(0.01)
