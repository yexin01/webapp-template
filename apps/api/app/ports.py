"""Interfaces between the application layer and infrastructure adapters.

Application services depend only on these protocols and on domain types.
Adapters in ``app.infrastructure`` implement them; ``app.composition`` wires
them together.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from app.domain.entitlements import SubscriptionSnapshot
from app.domain.tenancy import ExternalMembership, Role, TenantContext, VerifiedIdentity

# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MembershipRecord:
    org_id: UUID
    external_org_id: str
    role: Role
    verified_at: datetime


@dataclass(frozen=True, slots=True)
class ProjectRecord:
    id: UUID
    name: str
    org_id: UUID | None
    created_by: UUID
    created_at: datetime


@dataclass(frozen=True, slots=True)
class PageCursor:
    created_at: datetime
    id: UUID


@dataclass(frozen=True, slots=True)
class AuditEntry:
    actor_user_id: UUID | None
    org_id: UUID | None
    action: str
    target_type: str | None
    target_id: str | None
    outcome: str
    request_id: str | None


class EventStatus(StrEnum):
    RECEIVED = "received"
    PROCESSED = "processed"
    IGNORED = "ignored"
    NEEDS_RECONCILIATION = "needs_reconciliation"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class BillingWebhookEvent:
    event_id: str
    event_type: str
    created_at: datetime
    customer_id: str | None


@dataclass(frozen=True, slots=True)
class IdentityWebhookEvent:
    event_id: str
    event_type: str
    data: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class PriceDescription:
    """Non-secret facts about a billing price, used by configuration checks."""

    price_id: str
    active: bool
    recurring_interval: str | None
    currency: str | None
    livemode: bool


@dataclass(frozen=True, slots=True)
class ConnectionDescription:
    """What the database reports about the current transaction."""

    role: str
    server_version: str
    row_security: bool
    tenant_context_set: bool


@dataclass(frozen=True, slots=True)
class LLMCheckResult:
    ok: bool
    provider: str
    model: str
    latency_ms: int
    error_code: str | None = None


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


class Repository(Protocol):
    """Data access available to user requests (database role ``app_api``).

    Row-level security restricts every query to the transaction's tenant
    context, in addition to the explicit filters each method applies.
    """

    def find_user_id(self, external_user_id: str) -> UUID | None: ...
    def insert_user(self, external_user_id: str) -> None: ...
    def get_my_membership(self, external_org_id: str) -> MembershipRecord | None: ...
    def lock_my_membership_role(self, org_id: UUID) -> Role | None: ...

    def list_projects(self, ctx: TenantContext, limit: int, after: PageCursor | None) -> list[ProjectRecord]: ...
    def create_project(self, ctx: TenantContext, name: str) -> ProjectRecord: ...
    def delete_project(self, ctx: TenantContext, project_id: UUID) -> bool: ...

    def get_billing_customer_id(self, user_id: UUID) -> str | None: ...
    def insert_billing_customer(self, user_id: UUID, stripe_customer_id: str) -> str: ...
    def list_subscriptions(self, user_id: UUID) -> list[SubscriptionSnapshot]: ...

    def insert_audit(self, entry: AuditEntry) -> None: ...
    def ping(self) -> None: ...
    def describe_connection(self) -> ConnectionDescription: ...


class SystemRepository(Protocol):
    """Data access for system processes (database role ``app_worker``)."""

    def ensure_user(self, external_user_id: str) -> UUID: ...
    def find_user_id(self, external_user_id: str) -> UUID | None: ...
    def user_exists(self, user_id: UUID) -> bool: ...
    def replace_user_memberships(
        self, user_id: UUID, memberships: Sequence[ExternalMembership], verified_at: datetime
    ) -> None: ...
    def mark_org_deleted(self, external_org_id: str) -> None: ...
    def remove_user_memberships(self, user_id: UUID) -> None: ...

    def lock_customer(self, stripe_customer_id: str) -> None: ...
    def find_user_by_customer(self, stripe_customer_id: str) -> UUID | None: ...
    def get_customer_for_user(self, user_id: UUID) -> str | None: ...
    def link_customer(self, user_id: UUID, stripe_customer_id: str) -> None: ...
    def upsert_subscription(self, user_id: UUID, sub: SubscriptionSnapshot) -> None: ...
    def list_subscriptions_by_customer(self, stripe_customer_id: str) -> list[SubscriptionSnapshot]: ...
    def list_billing_customers(self, limit: int, after_user_id: UUID | None) -> list[tuple[UUID, str]]: ...

    def claim_billing_event(self, event: BillingWebhookEvent) -> EventStatus: ...
    def finish_billing_event(self, event_id: str, status: EventStatus) -> None: ...
    def claim_identity_event(self, event: IdentityWebhookEvent) -> EventStatus: ...
    def finish_identity_event(self, event_id: str, status: EventStatus) -> None: ...

    def increment_counter(self, bucket_key: str, window_start: datetime) -> int: ...
    def prune_counters(self, older_than: datetime) -> int: ...
    def insert_audit(self, entry: AuditEntry) -> None: ...
    def ping(self) -> None: ...


class UnitOfWork(Protocol):
    """Opens one database transaction with the right role and context."""

    def as_identity(self, external_user_id: str) -> AbstractContextManager[Repository]: ...
    def as_tenant(self, ctx: TenantContext) -> AbstractContextManager[Repository]: ...
    def as_system(self) -> AbstractContextManager[SystemRepository]: ...


# ---------------------------------------------------------------------------
# External services
# ---------------------------------------------------------------------------


class Clock(Protocol):
    def now(self) -> datetime: ...


class IdentityVerifier(Protocol):
    def verify(self, token: str) -> VerifiedIdentity: ...


class MembershipDirectory(Protocol):
    def list_user_memberships(self, external_user_id: str) -> list[ExternalMembership]: ...


class IdentityWebhookVerifier(Protocol):
    def verify(self, payload: bytes, headers: Mapping[str, str]) -> IdentityWebhookEvent: ...


class BillingProvider(Protocol):
    def create_customer(self, user_id: UUID) -> str: ...
    def create_checkout_session(
        self,
        *,
        customer_id: str,
        price_id: str,
        user_id: UUID,
        success_url: str,
        cancel_url: str,
        idempotency_key: str,
    ) -> str: ...
    def create_portal_session(self, *, customer_id: str, return_url: str) -> str: ...
    def list_customer_subscriptions(self, customer_id: str) -> list[SubscriptionSnapshot]: ...
    def get_customer_app_user_id(self, customer_id: str) -> UUID | None: ...
    def describe_price(self, price_id: str) -> PriceDescription: ...


class BillingWebhookVerifier(Protocol):
    def verify(self, payload: bytes, signature_header: str | None) -> BillingWebhookEvent: ...


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


class LLMProvider(Protocol):
    @property
    def name(self) -> str: ...
    def check(self) -> LLMCheckResult: ...
