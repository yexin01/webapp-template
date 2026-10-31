"""Composition contracts: what the kernel hands to modules, and what each
module hands back.

Used only by each module's ``module.py`` and by ``app.bootstrap``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any, TypeVar

import psycopg
from fastapi import APIRouter

from app.kernel.application.outbox import OutboxDispatcher
from app.kernel.application.rate_limit import RateLimiter
from app.kernel.diagnostics import IntegrationProbe
from app.kernel.events import EventHandlers
from app.kernel.http.auth import AuthDeps
from app.kernel.http.write_limit import limit_writes
from app.kernel.infrastructure.db import Database, PgTransactions
from app.kernel.infrastructure.observability import AnalyticsEvents
from app.kernel.jobs import JobRegistry
from app.kernel.ports import Analytics, Clock, ErrorReporter, KernelStore

R = TypeVar("R")
T = TypeVar("T")


@dataclass
class Kernel:
    """Platform services shared by all modules."""

    database: Database
    clock: Clock
    analytics: Analytics
    analytics_events: AnalyticsEvents
    error_reporter: ErrorReporter
    rate_limiter: RateLimiter
    store: PgTransactions[KernelStore]
    handlers: EventHandlers
    jobs: JobRegistry
    outbox: OutboxDispatcher
    on_record: Callable[[Any], None]
    # FastAPI dependency for routers that opt in to a request-scoped transaction.
    request_transaction: Any
    # POST, PUT, PATCH and DELETE requests per user per minute (WRITES_PER_USER_PER_MINUTE).
    writes_per_user_per_minute: int

    def limit_writes(self, auth: AuthDeps) -> Any:
        """The router dependency that counts a user's writes (SEC-09): list it after ``auth.authenticate``
        and ``request_transaction``. A route with a limit of its own does not use it."""
        return limit_writes(auth.authenticate, self.rate_limiter, self.writes_per_user_per_minute)

    def transactions(self, factory: Callable[[psycopg.Connection], R]) -> PgTransactions[R]:
        """Transactions whose blocks build the given module repository."""
        return PgTransactions(self.database, factory, self.on_record, self.clock)


@dataclass
class Contribution:
    """What a module adds to the application."""

    routers: list[APIRouter] = field(default_factory=list)
    probes: list[IntegrationProbe] = field(default_factory=list)
    # Product-analytics events the module sends, with the properties of each (OBS-03). See ``AnalyticsEvents``.
    analytics_events: Mapping[str, frozenset[str]] = field(default_factory=dict)
    closers: list[Callable[[], None]] = field(default_factory=list)


class Overrides(Mapping[str, Any]):
    """Adapter overrides for ``build_container`` (tests only; ENV-08).

    It remembers which keys the builders looked up, so ``unread()`` names an override that
    nothing consumed: a misspelled key, which would otherwise leave the test running on a
    different adapter than the one it meant to inject.
    """

    def __init__(self, values: Mapping[str, Any]) -> None:
        self._values = dict(values)
        self._asked: set[str] = set()

    def __getitem__(self, key: str) -> Any:
        self._asked.add(key)
        return self._values[key]

    def __contains__(self, key: object) -> bool:
        if isinstance(key, str):
            self._asked.add(key)
        return key in self._values

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def unread(self) -> list[str]:
        return sorted(self._values.keys() - self._asked)


def pick[T](overrides: Mapping[str, Any], key: str, factory: Callable[[], T]) -> T:
    """An explicit override wins, including an explicit ``None`` (tests only; ENV-08)."""
    return overrides[key] if key in overrides else factory()


__all__ = ["AuthDeps", "Contribution", "Kernel", "Overrides", "pick"]
