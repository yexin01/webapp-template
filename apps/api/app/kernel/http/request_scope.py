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
from collections.abc import AsyncIterator, Callable
from uuid import UUID

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
    """

    async def dependency() -> AsyncIterator[None]:
        token = database.begin_request_scope()
        try:
            yield
        finally:
            commit = database.end_request_scope(token)
            await run_in_threadpool(commit)

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
