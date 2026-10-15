"""Request-level transaction and event dispatch (ADR-0003): the
request-scoped transaction dependency, and the middleware that dispatches a
request's events after its response.

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
from anyio.lowlevel import RunVar
from starlette.types import ASGIApp, Receive, Scope, Send

from app.kernel.application.outbox import OutboxDispatcher
from app.kernel.errors import DependencyUnavailableError
from app.kernel.infrastructure.db import DATABASE_UNAVAILABLE, Database

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
    commit therefore surfaces as an error, never as a false success. The
    transaction commits when the handler returns or raises a ``DomainError``
    (a 4xx or 503 the service chose, whose writes such as a denial's audit
    entry must persist), and rolls back on any other exception, so a 500
    never comes with committed writes.

    A request-scoped request holds a pooled connection across several
    threadpool hops (dependencies, endpoint, commit). Three measures keep
    connection holders from starving behind threads that are waiting for a
    connection:

    - **Admission:** at most ``api_pool_size`` scoped requests run at once.
      Others wait asynchronously, holding neither a thread nor a connection,
      for at most the connect timeout; then they get a 503, so a backlog
      during an outage cannot grow without bound. ``release()`` ends the
      scope and gives the place back at once, so a request waiting on an
      external service never blocks other requests. The gate belongs to the
      event loop (an ASGI server runs one per process): an AnyIO semaphore
      cannot wake a waiter on another loop.
    - **Commit or rollback:** runs on its own one-off thread limiter, the
      pattern FastAPI uses for dependency exits, so it never queues behind the
      shared threadpool. It is shielded from cancellation, so a client that
      disconnects cannot leave the transaction open.
    """
    admission: RunVar[anyio.Semaphore] = RunVar("request_admission")

    def current_gate() -> anyio.Semaphore:
        try:
            return admission.get()
        except LookupError:
            gate = anyio.Semaphore(database.api_pool_size)
            admission.set(gate)
            return gate

    async def dependency() -> AsyncIterator[None]:
        gate = current_gate()
        admitted = False
        with anyio.move_on_after(database.connect_timeout):
            await gate.acquire()
            admitted = True
        if not admitted:
            logger.warning("database_admission_timeout", extra={"limit": database.api_pool_size})
            raise DependencyUnavailableError(DATABASE_UNAVAILABLE)
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
        error: BaseException | None = None
        try:
            yield
        except BaseException as exc:
            # FastAPI throws the handler's exception in here; it decides commit or rollback.
            error = exc
            raise
        finally:
            try:
                finish = database.end_request_scope(token, error=error)
                if finish is not None:
                    with anyio.CancelScope(shield=True):
                        await anyio.to_thread.run_sync(finish, limiter=anyio.CapacityLimiter(1))
            finally:
                if not given_back.is_set():
                    given_back.set()
                    gate.release()

    return dependency


class AfterResponseDispatch:
    """Pure ASGI middleware: dispatch the events a request recorded once its response is sent.

    It runs when the application returns, so the response has been sent,
    whether it reports a success or an error (a domain error commits the
    request's writes and events). An unhandled error propagates through it
    before any response exists, so it dispatches nothing then, and the 500
    never waits: the request transaction rolls back, and an event that a
    route committed outside it is left to ``drain-outbox``. Failures here
    never affect the response, and anything left over is picked up by the
    ``drain-outbox`` job.

    Dispatch never takes the request threads: per event loop, at most
    ``DATABASE_WORKER_POOL_MAX_SIZE - 1`` dispatches run at once, on threads
    of their own, so one worker connection stays free for readiness and rate
    limits. A dispatch that finds no free slot within the connect timeout is
    left to ``drain-outbox`` (``after_response_dispatch_deferred``).
    """

    def __init__(self, app: ASGIApp, *, dispatcher: OutboxDispatcher, database: Database) -> None:
        self.app = app
        self._dispatcher = dispatcher
        self._wait_seconds = database.connect_timeout
        self._size = max(1, database.worker_pool_size - 1)
        self._slots: RunVar[anyio.Semaphore] = RunVar(f"dispatch_slots_{id(self)}")
        self._threads: RunVar[anyio.CapacityLimiter] = RunVar(f"dispatch_threads_{id(self)}")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        recorded: list[UUID] = []
        token = _recorded.set(recorded)
        try:
            await self.app(scope, receive, send)
        finally:
            _recorded.reset(token)
        if recorded:
            await self._dispatch(list(recorded))

    async def _dispatch(self, recorded: list[UUID]) -> None:
        slots = self._slots_for_this_loop()
        acquired = False
        with anyio.move_on_after(self._wait_seconds):
            await slots.acquire()
            acquired = True
        if not acquired:
            logger.warning("after_response_dispatch_deferred", extra={"events": len(recorded)})
            return
        try:
            await anyio.to_thread.run_sync(self._dispatcher.drain, recorded, limiter=self._threads_for_this_loop())
        except Exception:
            logger.exception("after_response_dispatch_failed", extra={"events": len(recorded)})
        finally:
            slots.release()

    def _slots_for_this_loop(self) -> anyio.Semaphore:
        try:
            return self._slots.get()
        except LookupError:
            created = anyio.Semaphore(self._size)
            self._slots.set(created)
            return created

    def _threads_for_this_loop(self) -> anyio.CapacityLimiter:
        try:
            return self._threads.get()
        except LookupError:
            created = anyio.CapacityLimiter(self._size)
            self._threads.set(created)
            return created
