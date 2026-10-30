"""PostgreSQL adapter for billing customers, subscriptions, and the Stripe
webhook ledger. All SQL uses bound parameters only."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from app.kernel.errors import ConflictError
from app.kernel.webhooks import EventStatus
from app.modules.billing.domain import PlanKey, SubscriptionSnapshot, SubscriptionStatus
from app.modules.billing.ports import BillingWebhookEvent, OpenCheckout

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


class PgBillingRepository:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def _one(self, query: str, params: Sequence[object] = ()) -> dict[str, Any] | None:
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query, params)
            return cur.fetchone()

    def _all(self, query: str, params: Sequence[object] = ()) -> list[dict[str, Any]]:
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query, params)
            return cur.fetchall()

    def _exec(self, query: str, params: Sequence[object] = ()) -> int:
        with self._conn.cursor() as cur:
            cur.execute(query, params)
            return cur.rowcount

    # -- Customers ----------------------------------------------------------------

    def get_billing_customer_id(self, user_id: UUID) -> str | None:
        row = self._one("SELECT stripe_customer_id FROM app.billing_customers WHERE user_id = %s", (user_id,))
        return row["stripe_customer_id"] if row else None

    def insert_billing_customer(self, user_id: UUID, stripe_customer_id: str, linked_at: datetime) -> str:
        # Recorded as if checked when linked, so a new customer joins the back of the
        # reconciliation rotation instead of going ahead of every customer checked before.
        self._exec(
            """
            INSERT INTO app.billing_customers (user_id, stripe_customer_id, reconciled_at) VALUES (%s, %s, %s)
            ON CONFLICT (user_id) DO NOTHING
            """,
            (user_id, stripe_customer_id, linked_at),
        )
        stored = self.get_billing_customer_id(user_id)
        if stored is None:  # pragma: no cover
            raise RuntimeError("billing customer was not stored")
        return stored

    def get_open_checkout(self, user_id: UUID) -> OpenCheckout | None:
        row = self._one(
            "SELECT stripe_session_id, idempotency_key, expires_at FROM app.billing_checkout_sessions "
            "WHERE user_id = %s",
            (user_id,),
        )
        return OpenCheckout(row["stripe_session_id"], row["idempotency_key"], row["expires_at"]) if row else None

    def save_open_checkout(self, user_id: UUID, checkout: OpenCheckout) -> str | None:
        values = (user_id, checkout.stripe_session_id, checkout.idempotency_key, checkout.expires_at)
        inserted = self._exec(
            """
            INSERT INTO app.billing_checkout_sessions (user_id, stripe_session_id, idempotency_key, expires_at)
            VALUES (%s, %s, %s, %s) ON CONFLICT (user_id) DO NOTHING
            """,
            values,
        )
        if inserted:
            return None
        # A row exists, saved by an earlier request or by one that committed just now. Locked while it is
        # replaced, so two requests saving at once each learn what the other one replaced.
        previous = self._one(
            "SELECT stripe_session_id FROM app.billing_checkout_sessions WHERE user_id = %s FOR UPDATE", (user_id,)
        )
        self._exec(
            """
            UPDATE app.billing_checkout_sessions
            SET stripe_session_id = %s, idempotency_key = %s, expires_at = %s, created_at = now()
            WHERE user_id = %s
            """,
            (checkout.stripe_session_id, checkout.idempotency_key, checkout.expires_at, user_id),
        )
        replaced = previous["stripe_session_id"] if previous else None
        return None if replaced == checkout.stripe_session_id else replaced

    def delete_open_checkout(self, user_id: UUID, stripe_session_id: str) -> None:
        # Only the session that was closed: a newer one saved meanwhile stays.
        self._exec(
            "DELETE FROM app.billing_checkout_sessions WHERE user_id = %s AND stripe_session_id = %s",
            (user_id, stripe_session_id),
        )

    def user_exists(self, user_id: UUID) -> bool:
        return self._one("SELECT 1 AS ok FROM app.users WHERE id = %s", (user_id,)) is not None

    def link_customer(self, user_id: UUID, stripe_customer_id: str, linked_at: datetime) -> None:
        stored = self.insert_billing_customer(user_id, stripe_customer_id, linked_at)
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

    def list_billing_customers(self, limit: int, after_customer_id: str | None) -> list[str]:
        rows = self._all(
            """
            SELECT stripe_customer_id FROM app.billing_customers
            WHERE (%s::text IS NULL OR stripe_customer_id > %s::text)
            ORDER BY stripe_customer_id LIMIT %s
            """,
            (after_customer_id, after_customer_id, limit),
        )
        return [r["stripe_customer_id"] for r in rows]

    def list_least_recently_reconciled(self, limit: int) -> list[str]:
        rows = self._all(
            """
            SELECT stripe_customer_id FROM app.billing_customers
            ORDER BY reconciled_at NULLS FIRST, user_id LIMIT %s
            """,
            (limit,),
        )
        return [r["stripe_customer_id"] for r in rows]

    def mark_reconciled(self, stripe_customer_id: str, at: datetime) -> None:
        self._exec(
            "UPDATE app.billing_customers SET reconciled_at = %s WHERE stripe_customer_id = %s",
            (at, stripe_customer_id),
        )

    def list_flagged_customers(self, limit: int, after_customer_id: str | None) -> list[str]:
        rows = self._all(
            """
            SELECT DISTINCT stripe_customer_id FROM app.billing_events
            WHERE status = 'needs_reconciliation' AND stripe_customer_id IS NOT NULL
              AND (%s::text IS NULL OR stripe_customer_id > %s::text)
            ORDER BY stripe_customer_id LIMIT %s
            """,
            (after_customer_id, after_customer_id, limit),
        )
        return [r["stripe_customer_id"] for r in rows]

    def list_recently_flagged_customers(self, limit: int) -> list[str]:
        rows = self._all(
            """
            SELECT stripe_customer_id FROM app.billing_events
            WHERE status = 'needs_reconciliation' AND stripe_customer_id IS NOT NULL
            GROUP BY stripe_customer_id ORDER BY max(received_at) DESC LIMIT %s
            """,
            (limit,),
        )
        return [r["stripe_customer_id"] for r in rows]

    def resolve_flagged_events(self, stripe_customer_id: str) -> int:
        return self._exec(
            """
            UPDATE app.billing_events SET status = 'processed', processed_at = now()
            WHERE stripe_customer_id = %s AND status = 'needs_reconciliation'
            """,
            (stripe_customer_id,),
        )

    # -- Subscriptions --------------------------------------------------------------

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

    # -- Webhook ledger ----------------------------------------------------------------

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
