"""Ports owned by the kernel: transactions, the kernel's own store, the clock,
and telemetry.

Modules depend on these protocols, never on the adapters behind them.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, TypeVar
from uuid import UUID

from app.kernel.events import DomainEvent, StoredEvent
from app.kernel.tenancy import Role, TenantContext

R_co = TypeVar("R_co", covariant=True)


@dataclass(frozen=True, slots=True)
class AuditEntry:
    actor_user_id: UUID | None
    org_id: UUID | None
    action: str
    target_type: str | None
    target_id: str | None
    outcome: str
    request_id: str | None


@dataclass(frozen=True, slots=True)
class ConnectionDescription:
    """What the database reports about the current transaction."""

    role: str
    server_version: str
    row_security: bool
    tenant_context_set: bool


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------


class Tx(Protocol[R_co]):
    """One open transaction: a module's repository, plus kernel facilities
    bound to the same transaction."""

    @property
    def repo(self) -> R_co: ...

    def record(self, event: DomainEvent) -> None:
        """Write ``event`` to the outbox in this transaction."""
        ...

    def audit(self, entry: AuditEntry) -> None: ...

    def lock_my_role(self, org_id: UUID) -> Role | None:
        """Re-read and lock the caller's membership role for this transaction (TEN-07, NT-06)."""
        ...


class Transactions(Protocol[R_co]):
    """Opens transactions with the right database role and tenant context (DEC-008).

    ``as_identity`` and ``as_tenant`` run as ``app_api`` under row-level
    security. ``as_system`` runs as ``app_worker``, for webhooks, jobs, and
    maintenance. A block that raises rolls back.
    """

    def as_identity(self, external_user_id: str) -> AbstractContextManager[Tx[R_co]]: ...
    def as_tenant(self, ctx: TenantContext) -> AbstractContextManager[Tx[R_co]]: ...
    def as_system(self) -> AbstractContextManager[Tx[R_co]]: ...

    def release(self) -> None:
        """Commit and end the request-scoped transaction, if one is open.

        Call this before any slow external call (an identity provider, Stripe,
        an LLM), so a pooled connection is never held idle in a transaction.
        """
        ...


class KernelStore(Protocol):
    """The kernel's own persistence: health, rate-limit counters, and the outbox."""

    def ping(self) -> None: ...
    def describe_connection(self) -> ConnectionDescription: ...

    def increment_counter(self, bucket_key: str, window_start: datetime) -> int: ...
    def prune_counters(self, older_than: datetime) -> int: ...

    def claim_outbox(
        self, *, limit: int, now: datetime, lease_seconds: int, ids: Sequence[UUID] | None
    ) -> list[StoredEvent]: ...
    def complete_outbox(self, event_id: UUID) -> None: ...
    def fail_outbox(self, event_id: UUID, error: str, retry_at: datetime | None) -> None: ...
    def outbox_counts(self) -> Mapping[str, int]: ...


# ---------------------------------------------------------------------------
# Time and telemetry
# ---------------------------------------------------------------------------


class Clock(Protocol):
    def now(self) -> datetime: ...


class Analytics(Protocol):
    def capture(self, user_id: UUID, event: str, properties: Mapping[str, str | int | bool]) -> None: ...

    def flush(self) -> bool:
        """Export queued events now. Returns False if an export failed meanwhile."""
        ...

    def shutdown(self) -> None: ...


class ErrorReporter(Protocol):
    def send_test_event(self) -> str | None:
        """Report a synthetic error and flush. Returns the event ID, if one was queued."""
        ...
