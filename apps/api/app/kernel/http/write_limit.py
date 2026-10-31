"""A per-user limit on writes (SEC-09, DEC-011).

``limit_writes`` makes a router dependency. It counts the POST, PUT, PATCH and DELETE requests of
the signed-in user, in every context and whatever their outcome, in a fixed one-minute window in
PostgreSQL, and answers 429 with ``Retry-After`` past ``WRITES_PER_USER_PER_MINUTE``.

It depends on ``authenticate``, so a request without a valid token is refused first and counts
for nothing, and it needs no tenant context, so a refused write costs one counter update. List it
after ``request_transaction``: the counter update takes a worker connection, and a burst of writes
should queue at the admission gate, which holds no thread, not on threads that wait for one (the
database refuses those past its waiter budget with 503). It runs before the transaction opens a
connection, so it never holds an API connection while it waits for a worker one.

A route with a limit of its own (checkout, the portal, the LLM check, diagnostics) and the
webhooks, which have no user, do not use it. A test lists them, so a new write route is limited
unless it says why not.
"""

# No ``from __future__ import annotations`` here: FastAPI must evaluate the dependency
# annotations defined inside the factory at definition time.

from collections.abc import Callable
from typing import Annotated, Any

from fastapi import Depends, Request
from starlette.concurrency import run_in_threadpool

from app.kernel.application.rate_limit import RateLimiter
from app.kernel.tenancy import VerifiedIdentity

WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
WINDOW_SECONDS = 60


def limit_writes(authenticate: Callable[..., VerifiedIdentity], limiter: RateLimiter, per_minute: int) -> Any:
    async def count_write(request: Request, identity: Annotated[VerifiedIdentity, Depends(authenticate)]) -> None:
        # A read costs no thread hop: only a write waits for the counter.
        if request.method in WRITE_METHODS:
            await run_in_threadpool(limiter.hit, f"writes:user:{identity.external_user_id}", per_minute, WINDOW_SECONDS)

    return Depends(count_write)


def limits_writes(call: Callable[..., Any]) -> bool:
    """Whether ``call`` is a dependency made by ``limit_writes``. The route manifest finds a counted route by it."""
    return getattr(call, "__module__", "") == __name__ and getattr(call, "__qualname__", "").startswith(
        f"{limit_writes.__qualname__}."
    )
