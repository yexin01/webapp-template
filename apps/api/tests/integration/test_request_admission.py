"""Admission of request-scoped transactions, and the bound on threads waiting
for request connections (ADR-0003).

The dependency is driven as FastAPI drives it: entered before the handler,
and left with the handler's exception, if any.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager, nullcontext, suppress
from typing import Any
from unittest import mock

import anyio
import pytest
from fastapi.testclient import TestClient

from app.kernel.errors import DependencyUnavailableError, ForbiddenError
from app.kernel.http.app import build_fastapi
from app.kernel.http.request_scope import request_transaction
from app.kernel.infrastructure.db import API_ROLE, Database, RequestScope
from tests.conftest import make_settings, superuser_query

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
        assert outcomes.count("ok") == database.api_waiters_limit  # the waiters got the connection afterwards
        assert outcomes.count("503") == 6 - database.api_waiters_limit  # the rest failed at once
    finally:
        database.close()


def test_the_app_runs_blocking_work_on_the_thread_count_it_was_sized_for() -> None:
    app = build_fastapi(settings=make_settings(), title="t", routers=[], worker_threads=7)
    with TestClient(app) as client:
        tokens: Any = client.portal.call(lambda: anyio.to_thread.current_default_thread_limiter().total_tokens)
    assert tokens == 7
