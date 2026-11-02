"""Database faults between the API and PostgreSQL (ARCH-07, §16.4; found by the DEC-001 proof).

A TCP proxy sits where a connection pooler would. It can freeze, which
models a pooler that stops responding while the kernel still accepts its
traffic; it can drop every open connection, which models a pooler restart;
and it can refuse new connections, which models a pooler that is down.
"""

from __future__ import annotations

import contextlib
import select
import socket
import sys
import threading
import time
from collections.abc import Callable, Iterator
from typing import Any
from urllib.parse import urlparse, urlunparse

import psycopg
import pytest

from app.kernel.errors import DependencyUnavailableError
from app.kernel.infrastructure.db import API_ROLE, Database
from tests.conftest import superuser_query

pytestmark = pytest.mark.db

STATEMENT_TIMEOUT_MS = 500
CONNECT_TIMEOUT = 1.0
# Upper bound for a failed call: one round-trip deadline plus scheduling slack.
BOUND_SECONDS = STATEMENT_TIMEOUT_MS / 1000 + CONNECT_TIMEOUT + 2.0


class FaultProxy:
    """A loopback TCP proxy that can freeze traffic, on demand or at a given message, or drop every connection."""

    def __init__(self, upstream: tuple[str, int]) -> None:
        self._upstream = upstream
        self._listener = socket.create_server(("127.0.0.1", 0))
        self.port = int(self._listener.getsockname()[1])
        self._frozen = threading.Event()
        self._closed = threading.Event()
        self._refusing = threading.Event()
        self._lock = threading.Lock()
        self._sockets: list[socket.socket] = []
        self._freeze_on: bytes | None = None
        self.connections = 0  # accepted client connections
        self.refused = 0  # connection attempts closed while refusing
        threading.Thread(target=self._accept, daemon=True).start()

    def refuse(self) -> None:
        """Close every new connection at once, as a pooler that is down or restarting does."""
        self._refusing.set()

    def allow(self) -> None:
        self._refusing.clear()

    def freeze(self) -> None:
        self._frozen.set()

    def freeze_on(self, marker: bytes) -> None:
        """Freeze when a client message containing ``marker`` arrives; that message never reaches the server."""
        self._freeze_on = marker

    def thaw(self) -> None:
        self._freeze_on = None
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
            if self._refusing.is_set():
                with self._lock:
                    self.refused += 1
                client.close()
                continue
            upstream = socket.create_connection(self._upstream)
            with self._lock:
                self.connections += 1
                self._sockets += [client, upstream]
            for source, target, from_client in ((client, upstream, True), (upstream, client, False)):
                threading.Thread(target=self._pump, args=(source, target, from_client), daemon=True).start()

    def _pump(self, source: socket.socket, target: socket.socket, from_client: bool) -> None:
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
                marker = self._freeze_on
                if from_client and marker is not None and marker in data:
                    self._frozen.set()
                    continue
                while self._frozen.is_set() and not self._closed.is_set():
                    time.sleep(0.01)  # bytes read just as the freeze began wait for the thaw
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
        pool = database._pool(API_ROLE)
        checkouts = pool.get_stats()["requests_num"]
        fault.freeze()
        finished, error, elapsed = finishes_within(10, lambda: select_one(database))
        assert finished, "a database call hung past every timeout"
        assert isinstance(error, DependencyUnavailableError), error
        assert elapsed < BOUND_SECONDS, f"took {elapsed:.1f} s"
        # One checkout is one attempt. The proxy's connection count cannot tell an attempt from the pool opening a
        # replacement in the background for the connection it discarded, which it does within a second (it won
        # this race on a quiet machine and lost it in a full test run).
        assert pool.get_stats()["requests_num"] - checkouts == 1, "a timed-out call was retried on a new connection"
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
            commit = database.end_request_scope(token, error=sys.exc_info()[1])
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


def test_the_pool_reconnects_as_soon_as_the_database_is_back(proxy: tuple[FaultProxy, str]) -> None:
    """After an outage long enough for several failed reconnects, the next calls succeed once the
    database answers again, instead of failing until a reconnect scheduled with exponential
    backoff (psycopg_pool's default gives up only after 300 s) happens to come due."""
    fault, url = proxy
    database = make_database(url)
    try:
        assert select_one(database) == 1
        fault.refuse()
        fault.drop_connections()
        give_up = time.monotonic() + 30
        while fault.refused < 3:  # the pool has tried to reconnect three times: the outage is not brief
            assert time.monotonic() < give_up, "the pool stopped trying to reconnect"
            with contextlib.suppress(DependencyUnavailableError):
                select_one(database)
            time.sleep(0.05)
        fault.allow()
        restored = time.monotonic()
        while True:
            try:
                assert select_one(database) == 1
                break
            except DependencyUnavailableError:
                assert time.monotonic() - restored < 20, "the pool never reconnected"
                time.sleep(0.05)
        lag = time.monotonic() - restored
        assert lag < CONNECT_TIMEOUT + 1.0, f"calls kept failing for {lag:.1f} s after the database came back"
    finally:
        fault.allow()
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
            commit = database.end_request_scope(token, error=sys.exc_info()[1])
            if commit is not None:
                commit()
        assert row == (1,)
    finally:
        database.close()


@pytest.mark.parametrize("scoped", [False, True], ids=["transaction", "request-scope"])
def test_a_rollback_that_never_completes_reports_unavailable(proxy: tuple[FaultProxy, str], scoped: bool) -> None:
    """A block raises a non-database error, and the pooler stops responding at the cleanup ROLLBACK."""
    fault, url = proxy
    database = make_database(url)

    def request() -> None:
        token = database.begin_request_scope() if scoped else None
        try:
            with database.transaction(API_ROLE, {}) as conn:
                conn.execute("SELECT 1")
                fault.freeze_on(b"ROLLBACK")
                raise LookupError("not found")
        finally:
            if token is not None:
                commit = database.end_request_scope(token, error=sys.exc_info()[1])
                if commit is not None:
                    commit()

    try:
        finished, error, elapsed = finishes_within(10, request)
        assert finished, "a rollback hung past every timeout"
        assert isinstance(error, DependencyUnavailableError), repr(error)
        assert elapsed < BOUND_SECONDS, f"took {elapsed:.1f} s"
        fault.thaw()
        assert select_one(database) == 1
    finally:
        fault.thaw()
        database.close()


@pytest.mark.parametrize("scoped", [False, True], ids=["transaction", "request-scope"])
def test_a_commit_that_never_completes_reports_unavailable(proxy: tuple[FaultProxy, str], scoped: bool) -> None:
    fault, url = proxy
    database = make_database(url)

    def request() -> None:
        token = database.begin_request_scope() if scoped else None
        try:
            with database.transaction(API_ROLE, {}) as conn:
                conn.execute("SELECT 1")
                fault.freeze_on(b"COMMIT")
        finally:
            if token is not None:
                commit = database.end_request_scope(token, error=sys.exc_info()[1])
                if commit is not None:
                    commit()

    try:
        finished, error, elapsed = finishes_within(10, request)
        assert finished, "a commit hung past every timeout"
        assert isinstance(error, DependencyUnavailableError), repr(error)
        assert elapsed < BOUND_SECONDS, f"took {elapsed:.1f} s"
        fault.thaw()
        assert select_one(database) == 1
    finally:
        fault.thaw()
        database.close()


def _users_named(url: str, name: str) -> int:
    return int(superuser_query(url, "SELECT count(*) FROM app.users WHERE clerk_user_id = %s", (name,))[0][0])


@pytest.mark.parametrize("pattern", ["caught", "savepoint"])
@pytest.mark.parametrize("scoped", [False, True], ids=["transaction", "request-scope"])
def test_a_connection_lost_inside_a_block_is_never_reported_as_success(db: str, scoped: bool, pattern: str) -> None:
    """The server ends the session mid-block (a pooler restart, a failover, an operator), and the block
    catches the error, directly or around a savepoint. psycopg then leaves the block without a COMMIT
    and without an error, so the block must fail instead of reporting work that never committed."""
    database = make_database(db)

    def lose_the_connection_and_carry_on() -> None:
        with database.transaction(API_ROLE, {"app.clerk_user_id": "user_lost"}) as conn:
            conn.execute("INSERT INTO app.users (clerk_user_id) VALUES ('user_lost')")
            superuser_query(db, "SELECT pg_terminate_backend(%s)", (conn.info.backend_pid,))
            with contextlib.suppress(psycopg.OperationalError):
                if pattern == "savepoint":
                    with conn.transaction():  # try a statement and carry on if it fails
                        conn.execute("SELECT 1")
                else:
                    conn.execute("SELECT 1")

    try:
        token = database.begin_request_scope() if scoped else None
        with pytest.raises(DependencyUnavailableError):
            lose_the_connection_and_carry_on()
        if token is not None:
            with pytest.raises(RuntimeError, match="rolled back"):
                database.end_request_scope(token, error=None)
        assert _users_named(db, "user_lost") == 0
        assert select_one(database) == 1  # the lost connection was replaced
    finally:
        database.close()


def test_a_request_commit_on_a_lost_connection_reports_unavailable(db: str) -> None:
    """The commit checks the connection too: code that noticed the loss outside a block cannot hide it."""
    database = make_database(db)
    try:
        token = database.begin_request_scope()
        with database.transaction(API_ROLE, {"app.clerk_user_id": "user_lost"}) as conn:
            conn.execute("INSERT INTO app.users (clerk_user_id) VALUES ('user_lost')")
        superuser_query(db, "SELECT pg_terminate_backend(%s)", (conn.info.backend_pid,))
        with contextlib.suppress(psycopg.OperationalError):
            conn.execute("SELECT 1")
        commit = database.end_request_scope(token, error=None)
        assert commit is not None
        with pytest.raises(DependencyUnavailableError):
            commit()
        assert _users_named(db, "user_lost") == 0
    finally:
        database.close()
