"""Composition contracts: what the kernel hands to modules, and what each
module hands back.

Used only by each module's ``module.py`` and by ``app.bootstrap``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, TypeVar

import psycopg
from fastapi import APIRouter

from app.kernel.application.outbox import OutboxDispatcher
from app.kernel.application.rate_limit import RateLimiter
from app.kernel.diagnostics import IntegrationProbe
from app.kernel.events import EventHandlers
from app.kernel.http.auth import AuthDeps
from app.kernel.infrastructure.db import Database, PgTransactions
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
    error_reporter: ErrorReporter
    rate_limiter: RateLimiter
    store: PgTransactions[KernelStore]
    handlers: EventHandlers
    jobs: JobRegistry
    outbox: OutboxDispatcher
    on_record: Callable[[Any], None]
    # FastAPI dependency for routers that opt in to a request-scoped transaction.
    request_transaction: Any

    def transactions(self, factory: Callable[[psycopg.Connection], R]) -> PgTransactions[R]:
        """Transactions whose blocks build the given module repository."""
        return PgTransactions(self.database, factory, self.on_record, self.clock)


@dataclass
class Contribution:
    """What a module adds to the application."""

    routers: list[APIRouter] = field(default_factory=list)
    probes: list[IntegrationProbe] = field(default_factory=list)
    closers: list[Callable[[], None]] = field(default_factory=list)


def pick[T](overrides: Mapping[str, Any], key: str, factory: Callable[[], T]) -> T:
    """An explicit override wins, including an explicit ``None`` (tests only; ENV-08)."""
    return overrides[key] if key in overrides else factory()


__all__ = ["AuthDeps", "Contribution", "Kernel", "pick"]
