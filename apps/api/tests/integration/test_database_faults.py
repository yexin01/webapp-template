"""Database faults between the API and PostgreSQL (ARCH-07, §16.4; found by the DEC-001 proof).

A TCP proxy sits where a connection pooler would. It can freeze, which
models a pooler that stops responding while the kernel still accepts its
traffic, and it can drop every open connection, which models a pooler
restart.
"""

from __future__ import annotations

import contextlib
import select
import socket
import threading
import time
from collections.abc import Callable, Iterator
from typing import Any
from urllib.parse import urlparse, urlunparse

import pytest

from app.kernel.errors import DependencyUnavailableError
from app.kernel.infrastructure.db import API_ROLE, Database

pytestmark = pytest.mark.db

STATEMENT_TIMEOUT_MS = 500
CONNECT_TIMEOUT = 1.0
# Upper bound for a failed call: one round-trip deadline plus scheduling slack.
BOUND_SECONDS = STATEMENT_TIMEOUT_MS / 1000 + CONNECT_TIMEOUT + 2.0


class FaultProxy:
    """A loopback TCP proxy that can freeze traffic or drop every connection."""

    def __init__(self, upstream: tuple[str, int]) -> None:
        self._upstream = upstream
        self._listener = socket.create_server(("127.0.0.1", 0))
        self.port = int(self._listener.getsockname()[1])
        self._frozen = threading.Event()
        self._closed = threading.Event()
        self._lock = threading.Lock()
        self._sockets: list[socket.socket] = []
        threading.Thread(target=self._accept, daemon=True).start()

    def freeze(self) -> None:
        self._frozen.set()

    def thaw(self) -> None:
        self._frozen.clear()

    def drop_connections(self) -> None:
        with self._lock:
            sockets, self._sockets = self._sockets, []
        for sock in sockets:
            with contextlib.suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)
            sock.close()

    def close(self) -> None:
        self._closed.set()
        self.thaw()
        self.drop_connections()
        self._listener.close()

    def _accept(self) -> None:
        while not self._closed.is_set():
            try:
                client, _ = self._listener.accept()
            except OSError:
                return
            upstream = socket.create_connection(self._upstream)
            with self._lock:
                self._sockets += [client, upstream]
            for source, target in ((client, upstream), (upstream, client)):
                threading.Thread(target=self._pump, args=(source, target), daemon=True).start()

    def _pump(self, source: socket.socket, target: socket.socket) -> None:
        try:
            while not self._closed.is_set():
                if self._frozen.is_set():
                    time.sleep(0.01)  # leave bytes unread, as a stopped process would
                    continue
                readable, _, _ = select.select([source], [], [], 0.05)
                if not readable:
                    continue
                data = source.recv(65536)
                if not data:
                    break
                target.sendall(data)
        except OSError:
            pass
        finally:
            for sock in (source, target):
                with contextlib.suppress(OSError):
                    sock.shutdown(socket.SHUT_RDWR)


@pytest.fixture
def proxy(db: str) -> Iterator[tuple[FaultProxy, str]]:
    parsed = urlparse(db)
    fault = FaultProxy((parsed.hostname or "127.0.0.1", parsed.port or 5432))
    yield fault, urlunparse(parsed._replace(netloc=f"{parsed.username}:{parsed.password}@127.0.0.1:{fault.port}"))
    fault.close()


def make_database(url: str, pool_size: int = 1) -> Database:
    return Database(
        url,
        api_pool_size=pool_size,
        worker_pool_size=1,
        connect_timeout=CONNECT_TIMEOUT,
        statement_timeout_ms=STATEMENT_TIMEOUT_MS,
    )


def select_one(database: Database) -> Any:
    with database.transaction(API_ROLE, {}) as conn:
        row = conn.execute("SELECT 1").fetchone()
    return row[0] if row else None


def finishes_within(seconds: float, fn: Callable[[], Any]) -> tuple[bool, BaseException | None, float]:
    """Run ``fn`` in a thread; report whether it finished in time, its exception, and the elapsed time."""
    outcome: dict[str, Any] = {}

    def run() -> None:
        try:
            fn()
        except BaseException as exc:  # noqa: BLE001 - reported to the test
            outcome["error"] = exc

    started = time.monotonic()
    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(seconds)
    return not worker.is_alive(), outcome.get("error"), time.monotonic() - started


def test_a_database_that_stops_responding_fails_within_bounds(proxy: tuple[FaultProxy, str]) -> None:
    fault, url = proxy
    database = make_database(url)
    try:
        assert select_one(database) == 1
        fault.freeze()
        finished, error, elapsed = finishes_within(10, lambda: select_one(database))
        assert finished, "a database call hung past every timeout"
        assert isinstance(error, DependencyUnavailableError), error
        assert elapsed < BOUND_SECONDS, f"took {elapsed:.1f} s"
        fault.thaw()
        assert select_one(database) == 1  # the abandoned connection was discarded
    finally:
        fault.thaw()
        database.close()


def test_a_request_transaction_is_bounded_too(proxy: tuple[FaultProxy, str]) -> None:
    fault, url = proxy
    database = make_database(url)

    def request() -> None:
        token = database.begin_request_scope()
        try:
            with database.transaction(API_ROLE, {}) as conn:
                conn.execute("SELECT 1")
        finally:
            commit = database.end_request_scope(token)
            if commit is not None:
                commit()

    try:
        request()
        fault.freeze()
        finished, error, elapsed = finishes_within(10, request)
        assert finished, "a request transaction hung past every timeout"
        assert isinstance(error, DependencyUnavailableError), error
        assert elapsed < BOUND_SECONDS * 2, f"took {elapsed:.1f} s"  # the open and the commit each bounded
    finally:
        fault.thaw()
        database.close()


def test_connections_that_died_while_idle_are_replaced(proxy: tuple[FaultProxy, str]) -> None:
    """After a pooler restart, the next requests succeed instead of failing once per pooled connection."""
    fault, url = proxy
    database = make_database(url, pool_size=3)
    try:
        pool = database._pool(API_ROLE)
        held = [pool.getconn() for _ in range(3)]
        for conn in held:
            pool.putconn(conn)
        fault.drop_connections()
        assert [select_one(database) for _ in range(5)] == [1] * 5
    finally:
        database.close()


def test_a_request_transaction_survives_connections_that_died_while_idle(proxy: tuple[FaultProxy, str]) -> None:
    fault, url = proxy
    database = make_database(url, pool_size=2)
    try:
        assert select_one(database) == 1
        fault.drop_connections()
        token = database.begin_request_scope()
        try:
            with database.transaction(API_ROLE, {}) as conn:
                row = conn.execute("SELECT 1").fetchone()
        finally:
            commit = database.end_request_scope(token)
            if commit is not None:
                commit()
        assert row == (1,)
    finally:
        database.close()
