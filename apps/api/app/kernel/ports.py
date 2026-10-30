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
class LoginRole:
    """The role the connection logged in as (``session_user``), which no ``SET ROLE`` changes (DB-06, DEC-008)."""

    superuser: bool = False
    bypass_rls: bool = False
    create_role: bool = False
    # Member of a superuser or BYPASSRLS role, so it can switch into one.
    privileged_membership: bool = False
    # Tables in schema ``app`` it owns, or whose owner it is a member of.
    owned_tables: tuple[str, ...] = ()

    @property
    def problems(self) -> tuple[str, ...]:
        """Why a query that does not switch role could skip row-level security. Never names the role."""
        found = []
        if self.superuser:
            found.append("the login role is a superuser")
        if self.bypass_rls:
            found.append("the login role has BYPASSRLS")
        if self.create_role:
            found.append("the login role can create roles")
        if self.privileged_membership:
            found.append("the login role is a member of a superuser or BYPASSRLS role")
        if self.owned_tables:
            found.append("the login role owns, or can act as the owner of, " + ", ".join(self.owned_tables))
        return tuple(found)


@dataclass(frozen=True, slots=True)
class ConnectionDescription:
    """What the database reports about the current transaction, and whether row-level security binds it (DB-06)."""

    role: str
    server_version: str
    row_security: bool
    tenant_context_set: bool
    # Either attribute makes PostgreSQL skip row-level security, whatever ``row_security`` says.
    superuser: bool
    bypass_rls: bool
    # Tables in schema ``app`` whose row-level security is not both enabled and forced.
    unprotected_tables: tuple[str, ...]
    # Tables in schema ``app`` the role owns (or has its owner's privileges on).
    owned_tables: tuple[str, ...]
    login: LoginRole = LoginRole()


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
    def prune_counters(self, older_than: datetime, limit: int) -> int:
        """Delete up to ``limit`` expired windows; returns how many were deleted."""
        ...

    def claim_outbox(
        self, *, limit: int, now: datetime, lease_seconds: int, max_attempts: int, ids: Sequence[UUID] | None
    ) -> list[StoredEvent]:
        """Lease up to ``limit`` pending, due events that have attempts left."""
        ...

    def complete_outbox(self, event_id: UUID, attempt: int, processed_at: datetime) -> bool:
        """Mark processed if this lease (``attempt``) still holds the event."""
        ...

    def fail_outbox(self, event_id: UUID, attempt: int, error: str, retry_at: datetime | None) -> bool: ...
    def dead_letter_exhausted_outbox(self, max_attempts: int, limit: int) -> int:
        """Mark up to ``limit`` pending events that have no attempts left as dead; return how many."""
        ...

    def expired_outbox_leases(self, now: datetime, limit: int) -> list[tuple[UUID, int]]:
        """Up to ``limit`` leased events whose lease ran out, as (ID, attempt), locked until the transaction ends."""
        ...

    def outbox_counts(self) -> Mapping[str, int]: ...
    def prune_outbox(self, processed_before: datetime, limit: int) -> int:
        """Delete up to ``limit`` processed events; returns how many were deleted."""
        ...


# ---------------------------------------------------------------------------
# Time and telemetry
# ---------------------------------------------------------------------------


class Clock(Protocol):
    def now(self) -> datetime: ...


class Analytics(Protocol):
    def capture(
        self,
        user_id: UUID,
        event: str,
        properties: Mapping[str, str | int | bool],
        *,
        event_id: UUID | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        """Export an allowlisted event.

        Without ``event_id`` the event is queued, and the call never waits for
        the export. With one, it is sent before the call returns, waiting a
        bounded time (2 s by default for PostHog), so ``flush()`` can confirm
        it: pass one from outbox handlers only, never on the request path.
        While the provider is not answering, such an event may instead be
        reported unconfirmed at once, without being sent.

        Outbox handlers pass the outbox event's ``id`` and ``occurred_at``, so a
        redelivery produces the same provider event: PostHog deduplicates an
        event by its uuid together with its event, distinct ID, and timestamp.
        Without ``event_id`` every call is a new event, and without
        ``occurred_at`` it is stamped with the current time.
        """
        ...

    def flush(self) -> bool:
        """Export queued events now. Returns False if an export failed meanwhile, or if queued
        events were still unconfirmed when the flush timeout ran out.

        It also returns False if one of the caller's captures since its last flush could not be
        queued. After captures with ``event_id``, it instead confirms the events the caller sent
        that way since its last flush, waiting on nothing else: False unless the provider accepted
        every one. An outbox handler raises when it returns False, so the event is retried.
        """
        ...

    def shutdown(self) -> None: ...


class ErrorReporter(Protocol):
    def send_test_event(self) -> str | None:
        """Report a synthetic error and flush. Returns the event ID, if one was queued."""
        ...
