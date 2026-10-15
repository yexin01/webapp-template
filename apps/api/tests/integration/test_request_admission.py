"""Admission of request-scoped transactions, and the bound on threads waiting
for request connections (ADR-0003).

The dependency is driven as FastAPI drives it: entered before the handler,
and left with the handler's exception, if any.
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager, contextmanager, nullcontext, suppress
from typing import Any
from unittest import mock

import anyio
import httpx
import pytest
from fastapi.testclient import TestClient

from app.kernel.errors import DependencyUnavailableError, ForbiddenError
from app.kernel.http.app import build_fastapi
from app.kernel.http.request_scope import request_transaction
from app.kernel.infrastructure.db import API_ROLE, WORKER_ROLE, Database, RequestScope
from tests.conftest import Harness, make_settings, superuser_query

pytestmark = pytest.mark.db

POOL = 2

Scope = Callable[[], AbstractAsyncContextManager[None]]


def make_database(url: str, *, pool: int = POOL, threads: int = 40, connect_timeout: float = 3) -> Database:
    return Database(
        url,
        api_pool_size=pool,
        worker_pool_size=1,
        connect_timeout=connect_timeout,
        statement_timeout_ms=5000,
        request_threads=threads,
    )


@contextmanager
def silent_database() -> Iterator[str]:
    """The URL of a server that accepts connections and never answers, like a hung database or pooler."""
    listener = socket.create_server(("127.0.0.1", 0))
    held: list[socket.socket] = []

    def accept() -> None:
        while True:
            try:
                held.append(listener.accept()[0])
            except OSError:
                return

    threading.Thread(target=accept, daemon=True).start()
    try:
        yield f"postgresql://u:p@127.0.0.1:{listener.getsockname()[1]}/app"
    finally:
        listener.close()
        for conn in held:
            conn.close()


def insert(database: Database, user: str) -> None:
    with database.transaction(API_ROLE, {"app.clerk_user_id": user}) as conn:
        conn.execute("INSERT INTO app.users (clerk_user_id) VALUES (%s)", (user,))


def users(url: str, prefix: str) -> int:
    return int(
        superuser_query(url, "SELECT count(*) FROM app.users WHERE clerk_user_id LIKE %s", (prefix + "%",))[0][0]
    )


async def peak_inside(scope: Scope, requests: int) -> int:
    """Start ``requests`` scoped requests at once; return how many were inside together.

    A place that was never given back makes this time out instead of hanging.
    """
    inside = peak = 0
    leave = anyio.Event()

    async def request() -> None:
        nonlocal inside, peak
        async with scope():
            inside += 1
            peak = max(peak, inside)
            await leave.wait()
            inside -= 1

    with anyio.fail_after(5):
        async with anyio.create_task_group() as tg:
            for _ in range(requests):
                tg.start_soon(request)
            await anyio.sleep(0.2)
            leave.set()
    return peak


def test_at_most_pool_size_scoped_requests_run_at_once(db: str) -> None:
    database = make_database(db)
    try:
        assert anyio.run(peak_inside, asynccontextmanager(request_transaction(database)), POOL + 1) == POOL
    finally:
        database.close()


def test_each_event_loop_has_its_own_admission_gate() -> None:
    """An AnyIO semaphore cannot wake a waiter that belongs to another event loop. Starlette's
    TestClient runs every request on its own loop, so one gate shared across loops could leave a
    request waiting forever after the place it waited for came back."""
    database = make_database("postgresql://u:p@127.0.0.1:1/unused", pool=1)  # no block opens a connection
    scope = asynccontextmanager(request_transaction(database))
    holding, leave, admitted = threading.Event(), threading.Event(), threading.Event()

    async def holder() -> None:
        async with scope():
            holding.set()
            await anyio.to_thread.run_sync(leave.wait, 5)

    async def other_loop() -> None:
        async with scope():
            admitted.set()

    first = threading.Thread(target=anyio.run, args=(holder,), daemon=True)
    first.start()
    try:
        assert holding.wait(5)
        threading.Thread(target=anyio.run, args=(other_loop,), daemon=True).start()
        assert admitted.wait(3)
    finally:
        leave.set()
        first.join(5)
        database.close()


@pytest.mark.parametrize(
    ("ending", "committed"),
    [("returned", True), ("released", True), ("domain-error", True), ("error", False), ("cancelled", False)],
)
def test_every_way_a_request_ends_gives_its_place_back(db: str, ending: str, committed: bool) -> None:
    database = make_database(db)
    scope = asynccontextmanager(request_transaction(database))

    async def request(user: str) -> None:
        async with scope():
            await anyio.to_thread.run_sync(insert, database, user)
            if ending == "released":
                await anyio.to_thread.run_sync(database.release_request_transaction)
            elif ending == "domain-error":
                raise ForbiddenError()
            elif ending == "error":
                raise LookupError("a bug")
            elif ending == "cancelled":
                await anyio.sleep(10)  # the client disconnects meanwhile

    async def main() -> int:
        with anyio.fail_after(5):  # a place that was never given back blocks the next request
            for i in range(POOL + 1):  # more requests than places, one after another
                with (
                    anyio.move_on_after(0.2) if ending == "cancelled" else nullcontext(),
                    suppress(ForbiddenError, LookupError),
                ):
                    await request(f"user_{ending}_{i}")
        return await peak_inside(scope, POOL + 1)

    try:
        assert anyio.run(main) == POOL  # every place came back, and none twice
        assert users(db, f"user_{ending}_") == (POOL + 1 if committed else 0)
    finally:
        database.close()


def test_release_gives_the_place_back_before_the_request_ends(db: str) -> None:
    """With one place, a second request is admitted while the first is still running after
    ``release()``, as during a slow external call. Giving the place back only when the request
    ends would pass the test above, but not this one."""
    database = make_database(db, pool=1)
    scope = asynccontextmanager(request_transaction(database))

    async def main() -> bool:
        released, finish = anyio.Event(), anyio.Event()
        admitted = False

        async def first() -> None:
            async with scope():
                await anyio.to_thread.run_sync(insert, database, "user_first")
                await anyio.to_thread.run_sync(database.release_request_transaction)
                released.set()
                await finish.wait()

        async def second() -> None:
            nonlocal admitted
            await released.wait()
            try:
                with anyio.move_on_after(1):
                    async with scope():
                        admitted = True
            finally:
                finish.set()

        async with anyio.create_task_group() as tg:
            tg.start_soon(first)
            tg.start_soon(second)
        return admitted

    try:
        assert anyio.run(main)
        assert users(db, "user_first") == 1
    finally:
        database.close()


def test_a_request_cancelled_while_committing_still_commits(db: str) -> None:
    """The commit is shielded: a client that disconnects cannot leave the transaction open or undone."""
    database = make_database(db)
    scope = asynccontextmanager(request_transaction(database))
    committing = threading.Event()
    commit = RequestScope.commit

    def slow_commit(self: RequestScope) -> None:
        committing.set()
        time.sleep(0.3)
        commit(self)

    async def request() -> None:
        async with scope():
            await anyio.to_thread.run_sync(insert, database, "user_cancelled")

    async def main() -> int:
        with mock.patch.object(RequestScope, "commit", slow_commit):
            async with anyio.create_task_group() as tg:
                tg.start_soon(request)
                await anyio.to_thread.run_sync(committing.wait, 5)
                tg.cancel_scope.cancel()
        return await peak_inside(scope, POOL + 1)

    try:
        assert anyio.run(main) == POOL
        assert users(db, "user_cancelled") == 1
    finally:
        database.close()


def test_threads_waiting_for_connections_never_starve_a_scoped_request(db: str) -> None:
    """With every request connection held by a scoped request between two hops, a burst of unscoped
    calls must not take every thread: past the waiter bound they fail at once, so the scoped request
    gets a thread for its next hop and finishes, instead of waiting out the connection timeout."""
    threads, connect_timeout = 3, 3.0
    database = make_database(db, pool=1, threads=threads, connect_timeout=connect_timeout)
    scope = asynccontextmanager(request_transaction(database))
    outcomes: list[str] = []

    async def scoped(between_hops: anyio.Event) -> float:
        started = time.monotonic()
        async with scope():
            await anyio.to_thread.run_sync(insert, database, "user_scoped_1")  # takes the only connection
            between_hops.set()
            await anyio.sleep(0.3)
            await anyio.to_thread.run_sync(insert, database, "user_scoped_2")  # needs a thread again
        return time.monotonic() - started

    async def unscoped(i: int) -> None:
        try:
            await anyio.to_thread.run_sync(insert, database, f"user_unscoped_{i}")
            outcomes.append("ok")
        except DependencyUnavailableError:
            outcomes.append("503")

    async def main() -> float:
        anyio.to_thread.current_default_thread_limiter().total_tokens = threads  # as the app's lifespan does
        elapsed: list[float] = []
        between_hops = anyio.Event()

        async def run_scoped() -> None:
            elapsed.append(await scoped(between_hops))

        async with anyio.create_task_group() as tg:
            tg.start_soon(run_scoped)
            await between_hops.wait()
            for i in range(6):
                tg.start_soon(unscoped, i)
        return elapsed[0]

    try:
        elapsed = anyio.run(main)
        assert elapsed < connect_timeout / 2, f"the scoped request took {elapsed:.1f} s"
        assert users(db, "user_scoped_") == 2
        limit = database.role_waiters_limits[API_ROLE]
        assert outcomes.count("ok") == limit  # the waiters got the connection afterwards
        assert outcomes.count("503") == 6 - limit  # the rest failed at once
    finally:
        database.close()


def test_threads_waiting_for_worker_connections_never_starve_a_scoped_request(
    db: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Worker-pool waiters (rate limits, membership refreshes, readiness, webhook and job work) are
    bounded too: with every worker connection busy, a burst of worker calls past the bound fails at
    once, so a scoped request still gets a thread for its next hop."""
    threads, connect_timeout, burst = 4, 3.0, 8
    database = Database(
        db,
        api_pool_size=1,
        worker_pool_size=2,
        connect_timeout=connect_timeout,
        statement_timeout_ms=5000,
        request_threads=threads,
    )
    scope = asynccontextmanager(request_transaction(database))
    workers = database._pool(WORKER_ROLE)
    busy = [workers.getconn() for _ in range(workers.max_size)]  # e.g. webhook synchronization
    outcomes: list[str] = []

    def worker_call() -> None:
        with database.transaction(WORKER_ROLE, {}) as conn:
            conn.execute("SELECT 1")

    async def worker() -> None:
        try:
            await anyio.to_thread.run_sync(worker_call)
            outcomes.append("ok")
        except DependencyUnavailableError:
            outcomes.append("503")

    async def main() -> float:
        anyio.to_thread.current_default_thread_limiter().total_tokens = threads  # as the app's lifespan does
        between_hops = anyio.Event()
        elapsed: list[float] = []

        async def scoped() -> None:
            started = time.monotonic()
            async with scope():
                await anyio.to_thread.run_sync(insert, database, "user_scoped_1")
                between_hops.set()
                await anyio.sleep(0.3)
                await anyio.to_thread.run_sync(insert, database, "user_scoped_2")  # needs a thread again
            elapsed.append(time.monotonic() - started)
            for conn in busy:  # the busy worker connections come back
                workers.putconn(conn)

        async with anyio.create_task_group() as tg:
            tg.start_soon(scoped)
            await between_hops.wait()
            for _ in range(burst):
                tg.start_soon(worker)
        return elapsed[0]

    try:
        with caplog.at_level(logging.WARNING):
            elapsed = anyio.run(main)
        assert elapsed < connect_timeout / 2, f"the scoped request took {elapsed:.1f} s"
        assert users(db, "user_scoped_") == 2
        limit = database.role_waiters_limits[WORKER_ROLE]
        assert outcomes.count("ok") == limit  # the waiters got a connection afterwards
        assert outcomes.count("503") == burst - limit  # the rest failed at once
        exhausted = [r for r in caplog.records if r.getMessage() == "database_waiters_exhausted"]
        assert {(r.role, r.limit) for r in exhausted} == {(WORKER_ROLE, limit)}
    finally:
        database.close()


@pytest.mark.parametrize(
    ("pool", "burst"),
    [(5, 12), (28, 6)],  # a large request pool leaves 12 places, and keeps 6 of them for itself
)
def test_a_burst_of_short_worker_transactions_is_served_not_refused(db: str, pool: int, burst: int) -> None:
    """Waiters of both pools share one budget: a burst of rate-limit hits larger than the worker pool,
    on a pool that still has to open its connections, waits briefly instead of failing with 503s. The
    places the request pool keeps for itself never take more than half the budget."""
    database = make_database(db, pool=pool)  # one worker connection, opened on first use
    outcomes: list[str] = []

    def worker_call() -> None:
        try:
            with database.transaction(WORKER_ROLE, {}) as conn:
                conn.execute("SELECT pg_sleep(0.05)")
            outcomes.append("ok")
        except DependencyUnavailableError:
            outcomes.append("503")

    async def main() -> None:
        async with anyio.create_task_group() as tg:
            for _ in range(burst):
                tg.start_soon(anyio.to_thread.run_sync, worker_call)

    try:
        anyio.run(main)
        assert outcomes == ["ok"] * burst
    finally:
        database.close()


def wait_until(condition: Callable[[], bool], what: str, seconds: float = 5) -> None:
    give_up = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < give_up, what
        time.sleep(0.01)


def waiting(database: Database, role: str) -> int:
    """Threads blocked in the pool, waiting for a connection of ``role``."""
    return database._pool(role).get_stats().get("requests_waiting", 0)


def unscoped_call(database: Database, role: str, refused: list[str]) -> None:
    try:
        with database.transaction(role, {}) as conn:
            conn.execute("SELECT 1")
    except DependencyUnavailableError:
        refused.append(role)


@pytest.mark.parametrize("stalled", [WORKER_ROLE, API_ROLE])
def test_a_stalled_pool_never_refuses_calls_on_the_other(db: str, stalled: str) -> None:
    """Waiters of both pools share one budget, but neither pool can take all of it. While every
    connection of one pool is held (billing synchronization across slow Stripe calls, say) and its
    callers queue, a call on the other, idle pool still finds a waiter place and succeeds."""
    database = Database(
        db, api_pool_size=2, worker_pool_size=2, connect_timeout=5, statement_timeout_ms=5000, request_threads=6
    )
    healthy = API_ROLE if stalled == WORKER_ROLE else WORKER_ROLE
    pool = database._pool(stalled)
    held = [pool.getconn() for _ in range(pool.max_size)]
    refused: list[str] = []
    callers = [
        threading.Thread(target=unscoped_call, args=(database, stalled, refused), daemon=True)
        for _ in range(database.waiters_limit)  # enough to take every place, were nothing reserved
    ]
    try:
        for caller in callers:
            caller.start()
        wait_until(
            lambda: len(refused) + waiting(database, stalled) == len(callers), "callers neither queued nor refused"
        )
        unscoped_call(database, healthy, refused)
        assert healthy not in refused, f"an idle {healthy} pool refused a call while the {stalled} pool stalled"
        assert len(refused) == database.waiters_limit - database.role_waiters_limits[stalled]
    finally:
        for conn in held:
            pool.putconn(conn)
        for caller in callers:
            caller.join(10)
        database.close()


def test_the_waiters_of_both_pools_together_stay_within_one_budget(db: str) -> None:
    """Each pool's own limit leaves room for the other, but together they never exceed the shared
    budget, so scoped holders plus all waiters still fit in the request threads."""
    database = Database(
        db, api_pool_size=1, worker_pool_size=1, connect_timeout=5, statement_timeout_ms=5000, request_threads=5
    )
    held = {role: database._pool(role).getconn() for role in (API_ROLE, WORKER_ROLE)}
    refused: list[str] = []
    callers: list[threading.Thread] = []

    def start(role: str, count: int) -> None:
        for _ in range(count):
            callers.append(threading.Thread(target=unscoped_call, args=(database, role, refused), daemon=True))
            callers[-1].start()

    try:
        assert database.role_waiters_limits[WORKER_ROLE] + database.role_waiters_limits[API_ROLE] > (
            database.waiters_limit
        )
        start(WORKER_ROLE, database.role_waiters_limits[WORKER_ROLE])
        wait_until(
            lambda: waiting(database, WORKER_ROLE) == database.role_waiters_limits[WORKER_ROLE],
            "the worker calls did not all queue",
        )
        start(API_ROLE, database.role_waiters_limits[API_ROLE])
        wait_until(
            lambda: len(refused) + waiting(database, API_ROLE) == database.role_waiters_limits[API_ROLE],
            "the request-pool calls neither queued nor refused",
        )
        assert waiting(database, WORKER_ROLE) + waiting(database, API_ROLE) == database.waiters_limit
        assert refused == [API_ROLE] * (
            database.role_waiters_limits[WORKER_ROLE] + database.role_waiters_limits[API_ROLE] - database.waiters_limit
        )
    finally:
        for role, conn in held.items():
            database._pool(role).putconn(conn)
        for caller in callers:
            caller.join(10)
        database.close()


def test_waiting_for_admission_is_bounded_by_the_connect_timeout(make_harness: Callable[..., Harness]) -> None:
    """While the database hangs, each admitted request holds its place until the pool timeout. A request
    queued behind them gets its 503 within the connect timeout, instead of waiting for every request
    ahead of it in turn (§16.4). All requests share one event loop, and so one gate, as in production."""
    connect_timeout, requests = 1.0, 12
    results: list[tuple[int, float]] = []
    with silent_database() as url:
        h = make_harness(database=make_database(url, pool=2, connect_timeout=connect_timeout))
        logins = [h.login(f"user_{i}") for i in range(requests)]

        async def main() -> None:
            transport = httpx.ASGITransport(app=h.client.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://api.test") as client:

                async def call(headers: dict[str, str]) -> None:
                    started = time.monotonic()
                    response = await client.get("/api/v1/me", headers=headers)
                    results.append((response.status_code, time.monotonic() - started))

                async with anyio.create_task_group() as tg:
                    for headers in logins:
                        tg.start_soon(call, headers)

        anyio.run(main)
    assert [status for status, _ in results] == [503] * requests
    slowest = max(elapsed for _, elapsed in results)
    # An admitted request fails after one pool timeout; one admitted just before its bound, after two.
    assert slowest < 2 * connect_timeout + 1, f"the slowest request took {slowest:.1f} s"


def test_the_app_runs_blocking_work_on_the_thread_count_it_was_sized_for() -> None:
    app = build_fastapi(settings=make_settings(), title="t", routers=[], worker_threads=7)
    with TestClient(app) as client:
        tokens: Any = client.portal.call(lambda: anyio.to_thread.current_default_thread_limiter().total_tokens)
    assert tokens == 7
