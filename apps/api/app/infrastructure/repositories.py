"""PostgreSQL repository. All SQL lives here and uses bound parameters only.

Row-level security filters every statement by the transaction's role and
context; the explicit tenant filters below are a second, independent guard.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from app.domain.entitlements import PlanKey, SubscriptionSnapshot, SubscriptionStatus
from app.domain.errors import ConflictError
from app.domain.tenancy import ContextKind, ExternalMembership, Role, TenantContext
from app.ports import (
    AuditEntry,
    BillingWebhookEvent,
    EventStatus,
    IdentityWebhookEvent,
    MembershipRecord,
    PageCursor,
    ProjectRecord,
)

_SUBSCRIPTION_COLUMNS = (
    "stripe_subscription_id, stripe_customer_id, status, plan_key, price_id, "
    "current_period_end, cancel_at_period_end, cancel_at, ended_at"
)


def _subscription(row: dict[str, Any]) -> SubscriptionSnapshot:
    return SubscriptionSnapshot(
        stripe_subscription_id=row["stripe_subscription_id"],
        stripe_customer_id=row["stripe_customer_id"],
        status=SubscriptionStatus(row["status"]),
        plan_key=PlanKey(row["plan_key"]),
        price_id=row["price_id"],
        current_period_end=row["current_period_end"],
        cancel_at_period_end=row["cancel_at_period_end"],
        cancel_at=row["cancel_at"],
        ended_at=row["ended_at"],
    )


def _project(row: dict[str, Any]) -> ProjectRecord:
    return ProjectRecord(
        id=row["id"],
        name=row["name"],
        org_id=row["org_id"],
        created_by=row["created_by"],
        created_at=row["created_at"],
    )


class PgRepository:
    """Implements both ``Repository`` and ``SystemRepository``.

    Which methods actually succeed is decided by the database role the
    transaction runs as, not by this class.
    """

    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def _one(self, sql: str, params: Sequence[object] = ()) -> dict[str, Any] | None:
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            return cur.fetchone()

    def _all(self, sql: str, params: Sequence[object] = ()) -> list[dict[str, Any]]:
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    def _exec(self, sql: str, params: Sequence[object] = ()) -> int:
        with self._conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.rowcount

    # -- Health ---------------------------------------------------------------

    def ping(self) -> None:
        self._one("SELECT 1 AS ok")

    # -- Users and memberships ----------------------------------------------

    def find_user_id(self, external_user_id: str) -> UUID | None:
        row = self._one("SELECT id FROM app.users WHERE clerk_user_id = %s", (external_user_id,))
        return row["id"] if row else None

    def insert_user(self, external_user_id: str) -> None:
        self._exec(
            "INSERT INTO app.users (clerk_user_id) VALUES (%s) ON CONFLICT (clerk_user_id) DO NOTHING",
            (external_user_id,),
        )

    def ensure_user(self, external_user_id: str) -> UUID:
        self.insert_user(external_user_id)
        user_id = self.find_user_id(external_user_id)
        if user_id is None:  # pragma: no cover - unique constraint guarantees a row
            raise RuntimeError("user could not be provisioned")
        return user_id

    def user_exists(self, user_id: UUID) -> bool:
        return self._one("SELECT 1 AS ok FROM app.users WHERE id = %s", (user_id,)) is not None

    def get_my_membership(self, external_org_id: str) -> MembershipRecord | None:
        row = self._one(
            """
            SELECT m.org_id, o.clerk_org_id, m.role, m.verified_at
            FROM app.organization_memberships m
            JOIN app.organizations o ON o.id = m.org_id
            WHERE o.clerk_org_id = %s
              AND m.user_id = app.ctx_user_id()
              AND o.deleted_at IS NULL
            """,
            (external_org_id,),
        )
        if row is None:
            return None
        return MembershipRecord(row["org_id"], row["clerk_org_id"], Role(row["role"]), row["verified_at"])

    def lock_my_membership_role(self, org_id: UUID) -> Role | None:
        row = self._one("SELECT app.lock_my_membership(%s) AS role", (org_id,))
        return Role(row["role"]) if row and row["role"] else None

    def replace_user_memberships(
        self, user_id: UUID, memberships: Sequence[ExternalMembership], verified_at: datetime
    ) -> None:
        external_ids = [m.external_org_id for m in memberships]
        for m in memberships:
            self._exec(
                """
                INSERT INTO app.organizations (clerk_org_id) VALUES (%s)
                ON CONFLICT (clerk_org_id) DO NOTHING
                """,
                (m.external_org_id,),
            )
            self._exec(
                """
                INSERT INTO app.organization_memberships (org_id, user_id, role, verified_at)
                SELECT o.id, %s, %s, %s FROM app.organizations o
                WHERE o.clerk_org_id = %s AND o.deleted_at IS NULL
                ON CONFLICT (org_id, user_id)
                DO UPDATE SET role = EXCLUDED.role, verified_at = EXCLUDED.verified_at
                """,
                (user_id, m.role.value, verified_at, m.external_org_id),
            )
        self._exec(
            """
            DELETE FROM app.organization_memberships m
            USING app.organizations o
            WHERE m.org_id = o.id AND m.user_id = %s AND NOT (o.clerk_org_id = ANY(%s))
            """,
            (user_id, external_ids),
        )

    def mark_org_deleted(self, external_org_id: str) -> None:
        self._exec(
            """
            INSERT INTO app.organizations (clerk_org_id, deleted_at) VALUES (%s, now())
            ON CONFLICT (clerk_org_id) DO UPDATE SET deleted_at = COALESCE(app.organizations.deleted_at, now())
            """,
            (external_org_id,),
        )
        self._exec(
            """
            DELETE FROM app.organization_memberships m USING app.organizations o
            WHERE m.org_id = o.id AND o.clerk_org_id = %s
            """,
            (external_org_id,),
        )

    def remove_user_memberships(self, user_id: UUID) -> None:
        self._exec("DELETE FROM app.organization_memberships WHERE user_id = %s", (user_id,))

    # -- Projects -------------------------------------------------------------

    def list_projects(self, ctx: TenantContext, limit: int, after: PageCursor | None) -> list[ProjectRecord]:
        tenant_sql, tenant_params = _tenant_filter(ctx)
        cursor_sql = ""
        params: list[object] = list(tenant_params)
        if after is not None:
            cursor_sql = "AND (created_at, id) < (%s, %s)"
            params += [after.created_at, after.id]
        params.append(limit)
        rows = self._all(
            f"""
            SELECT id, name, org_id, created_by, created_at FROM app.projects
            WHERE {tenant_sql} {cursor_sql}
            ORDER BY created_at DESC, id DESC
            LIMIT %s
            """,  # noqa: S608 - fragments are constants; values are bound parameters
            params,
        )
        return [_project(r) for r in rows]

    def create_project(self, ctx: TenantContext, name: str) -> ProjectRecord:
        is_org = ctx.kind is ContextKind.ORGANIZATION
        row = self._one(
            """
            INSERT INTO app.projects (org_id, owner_user_id, created_by, name)
            VALUES (%s, %s, %s, %s)
            RETURNING id, name, org_id, created_by, created_at
            """,
            (ctx.org_id if is_org else None, None if is_org else ctx.user_id, ctx.user_id, name),
        )
        if row is None:  # pragma: no cover
            raise RuntimeError("insert returned no row")
        return _project(row)

    def delete_project(self, ctx: TenantContext, project_id: UUID) -> bool:
        tenant_sql, tenant_params = _tenant_filter(ctx)
        return (
            self._exec(
                f"DELETE FROM app.projects WHERE id = %s AND {tenant_sql}",  # noqa: S608
                (project_id, *tenant_params),
            )
            == 1
        )

    # -- Billing --------------------------------------------------------------

    def get_billing_customer_id(self, user_id: UUID) -> str | None:
        row = self._one("SELECT stripe_customer_id FROM app.billing_customers WHERE user_id = %s", (user_id,))
        return row["stripe_customer_id"] if row else None

    get_customer_for_user = get_billing_customer_id

    def insert_billing_customer(self, user_id: UUID, stripe_customer_id: str) -> str:
        self._exec(
            """
            INSERT INTO app.billing_customers (user_id, stripe_customer_id) VALUES (%s, %s)
            ON CONFLICT (user_id) DO NOTHING
            """,
            (user_id, stripe_customer_id),
        )
        stored = self.get_billing_customer_id(user_id)
        if stored is None:  # pragma: no cover
            raise RuntimeError("billing customer was not stored")
        return stored

    def link_customer(self, user_id: UUID, stripe_customer_id: str) -> None:
        stored = self.insert_billing_customer(user_id, stripe_customer_id)
        if stored != stripe_customer_id:
            raise ConflictError("User is already linked to another billing customer.")

    def find_user_by_customer(self, stripe_customer_id: str) -> UUID | None:
        row = self._one(
            "SELECT user_id FROM app.billing_customers WHERE stripe_customer_id = %s", (stripe_customer_id,)
        )
        return row["user_id"] if row else None

    def lock_customer(self, stripe_customer_id: str) -> None:
        # Serializes all synchronization for one customer within this transaction.
        self._one("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0)) AS locked", (stripe_customer_id,))

    def list_subscriptions(self, user_id: UUID) -> list[SubscriptionSnapshot]:
        rows = self._all(
            f"SELECT {_SUBSCRIPTION_COLUMNS} FROM app.subscriptions WHERE user_id = %s",  # noqa: S608
            (user_id,),
        )
        return [_subscription(r) for r in rows]

    def list_subscriptions_by_customer(self, stripe_customer_id: str) -> list[SubscriptionSnapshot]:
        rows = self._all(
            f"SELECT {_SUBSCRIPTION_COLUMNS} FROM app.subscriptions WHERE stripe_customer_id = %s",  # noqa: S608
            (stripe_customer_id,),
        )
        return [_subscription(r) for r in rows]

    def upsert_subscription(self, user_id: UUID, sub: SubscriptionSnapshot) -> None:
        self._exec(
            """
            INSERT INTO app.subscriptions (
              stripe_subscription_id, user_id, stripe_customer_id, status, plan_key, price_id,
              current_period_end, cancel_at_period_end, cancel_at, ended_at, synced_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
            ON CONFLICT (stripe_subscription_id) DO UPDATE SET
              status = EXCLUDED.status,
              plan_key = EXCLUDED.plan_key,
              price_id = EXCLUDED.price_id,
              current_period_end = EXCLUDED.current_period_end,
              cancel_at_period_end = EXCLUDED.cancel_at_period_end,
              cancel_at = EXCLUDED.cancel_at,
              ended_at = EXCLUDED.ended_at,
              synced_at = now()
            WHERE app.subscriptions.user_id = EXCLUDED.user_id
            """,
            (
                sub.stripe_subscription_id,
                user_id,
                sub.stripe_customer_id,
                sub.status.value,
                sub.plan_key.value,
                sub.price_id,
                sub.current_period_end,
                sub.cancel_at_period_end,
                sub.cancel_at,
                sub.ended_at,
            ),
        )

    def list_billing_customers(self, limit: int, after_user_id: UUID | None) -> list[tuple[UUID, str]]:
        rows = self._all(
            """
            SELECT user_id, stripe_customer_id FROM app.billing_customers
            WHERE (%s::uuid IS NULL OR user_id > %s::uuid)
            ORDER BY user_id LIMIT %s
            """,
            (after_user_id, after_user_id, limit),
        )
        return [(r["user_id"], r["stripe_customer_id"]) for r in rows]

    # -- Webhook ledgers ------------------------------------------------------

    def claim_billing_event(self, event: BillingWebhookEvent) -> EventStatus:
        self._exec(
            """
            INSERT INTO app.billing_events (stripe_event_id, event_type, stripe_customer_id, event_created_at, status)
            VALUES (%s, %s, %s, %s, 'received')
            ON CONFLICT (stripe_event_id) DO NOTHING
            """,
            (event.event_id, event.event_type, event.customer_id, event.created_at),
        )
        row = self._one(
            """
            UPDATE app.billing_events SET attempts = attempts + 1
            WHERE stripe_event_id = %s RETURNING status
            """,
            (event.event_id,),
        )
        return EventStatus(row["status"]) if row else EventStatus.RECEIVED

    def finish_billing_event(self, event_id: str, status: EventStatus) -> None:
        self._exec(
            "UPDATE app.billing_events SET status = %s, processed_at = now() WHERE stripe_event_id = %s",
            (status.value, event_id),
        )

    def claim_identity_event(self, event: IdentityWebhookEvent) -> EventStatus:
        self._exec(
            """
            INSERT INTO app.identity_events (event_id, event_type, status) VALUES (%s, %s, 'received')
            ON CONFLICT (event_id) DO NOTHING
            """,
            (event.event_id, event.event_type),
        )
        row = self._one(
            "UPDATE app.identity_events SET attempts = attempts + 1 WHERE event_id = %s RETURNING status",
            (event.event_id,),
        )
        return EventStatus(row["status"]) if row else EventStatus.RECEIVED

    def finish_identity_event(self, event_id: str, status: EventStatus) -> None:
        self._exec(
            "UPDATE app.identity_events SET status = %s, processed_at = now() WHERE event_id = %s",
            (status.value, event_id),
        )

    # -- Audit and rate limits ------------------------------------------------

    def insert_audit(self, entry: AuditEntry) -> None:
        self._exec(
            """
            INSERT INTO app.audit_log (actor_user_id, org_id, action, target_type, target_id, outcome, request_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                entry.actor_user_id,
                entry.org_id,
                entry.action,
                entry.target_type,
                entry.target_id,
                entry.outcome,
                entry.request_id,
            ),
        )

    def increment_counter(self, bucket_key: str, window_start: datetime) -> int:
        row = self._one(
            """
            INSERT INTO app.rate_limit_counters (bucket_key, window_start, hits) VALUES (%s, %s, 1)
            ON CONFLICT (bucket_key, window_start) DO UPDATE SET hits = app.rate_limit_counters.hits + 1
            RETURNING hits
            """,
            (bucket_key, window_start),
        )
        return int(row["hits"]) if row else 0

    def prune_counters(self, older_than: datetime) -> int:
        return self._exec("DELETE FROM app.rate_limit_counters WHERE window_start < %s", (older_than,))


def _tenant_filter(ctx: TenantContext) -> tuple[str, tuple[object, ...]]:
    if ctx.kind is ContextKind.ORGANIZATION:
        return "org_id = %s", (ctx.org_id,)
    return "owner_user_id = %s AND org_id IS NULL", (ctx.user_id,)
