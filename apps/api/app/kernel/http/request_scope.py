"""Request-level dependencies: the request-scoped transaction and
after-response event dispatch (ADR-0003).

``request_transaction`` is opt-in per router. Use it only for routes that
touch nothing but the database. A route that calls an external service must
not use it, or must call ``Transactions.release()`` before the call, so a
pooled connection is never held idle in a transaction.
"""

from __future__ import annotations

import contextvars
import logging
import threading
from collections.abc import AsyncIterator, Callable
from uuid import UUID

import anyio
from starlette.concurrency import run_in_threadpool

from app.kernel.application.outbox import OutboxDispatcher
from app.kernel.infrastructure.db import Database

logger = logging.getLogger(__name__)

_recorded: contextvars.ContextVar[list[UUID] | None] = contextvars.ContextVar("recorded_events", default=None)


def note_recorded_event(event_id: UUID) -> None:
    """Called when an event is written to the outbox during a request."""
    recorded = _recorded.get()
    if recorded is not None:
        recorded.append(event_id)


def request_transaction(database: Database) -> Callable[[], AsyncIterator[None]]:
    """Dependency: one ``app_api`` transaction for the whole request.

    Register it with ``Depends(..., scope="function")``, so the commit happens
    after the handler returns but **before** the response is sent. A failed
    commit therefore surfaces as an error, never as a false success.

    A request-scoped request holds a pooled connection across several
    threadpool hops (dependencies, endpoint, commit). Three measures keep
    connection holders from starving behind threads that are waiting for a
    connection:

    - **Admission:** at most ``api_pool_size`` scoped requests run at once.
      Others wait asynchronously, holding neither a thread nor a connection.
      ``release()`` ends the scope and gives the place back at once, so a
      request waiting on an external service never blocks other requests.
    - **Commit:** runs on its own one-off thread limiter, the pattern FastAPI
      uses for dependency exits, so it never queues behind the shared
      threadpool. It is shielded from cancellation, so a client that
      disconnects cannot leave the transaction open.
    """
    admission: anyio.Semaphore | None = None

    async def dependency() -> AsyncIterator[None]:
        nonlocal admission
        if admission is None:
            admission = anyio.Semaphore(database.api_pool_size)
        gate = admission
        await gate.acquire()
        loop_thread = threading.get_ident()
        given_back = threading.Event()

        def give_back() -> None:
            # Called by ``release()``: from a worker thread, or from the event loop.
            if given_back.is_set():
                return
            try:
                if threading.get_ident() == loop_thread:
                    gate.release()
                else:
                    anyio.from_thread.run_sync(gate.release)
            except RuntimeError:
                return  # not an anyio worker thread: the teardown below gives the place back
            given_back.set()

        token = database.begin_request_scope(on_release=give_back)
        completed = False
        try:
            yield
            completed = True
        finally:
            try:
                commit = database.end_request_scope(token, completed=completed)
                if commit is not None:
                    with anyio.CancelScope(shield=True):
                        await anyio.to_thread.run_sync(commit, limiter=anyio.CapacityLimiter(1))
            finally:
                if not given_back.is_set():
                    given_back.set()
                    gate.release()

    return dependency


def dispatch_recorded_events(dispatcher: OutboxDispatcher) -> Callable[[], AsyncIterator[None]]:
    """Dependency: after the response is sent, dispatch the events this request recorded.

    Register it with ``scope="request"``. Failures here never affect the
    response. Anything left over is picked up by the ``drain-outbox`` job.
    """

    async def dependency() -> AsyncIterator[None]:
        recorded: list[UUID] = []
        token = _recorded.set(recorded)
        try:
            yield
        finally:
            _recorded.reset(token)
            if recorded:
                try:
                    await run_in_threadpool(dispatcher.drain, list(recorded))
                except Exception:
                    logger.exception("after_response_dispatch_failed", extra={"events": len(recorded)})

    return dependency
