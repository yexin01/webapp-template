"""PostgreSQL adapter for users, memberships, and the identity webhook ledger.

Row-level security filters every statement by the transaction's role and
context; the explicit filters below are a second, independent guard. All SQL
uses bound parameters only.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from app.kernel.tenancy import Role
from app.kernel.webhooks import EventStatus
from app.modules.identity.domain import ExternalMembership, MembershipRecord
from app.modules.identity.ports import IdentityWebhookEvent


class PgIdentityRepository:
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

    # -- Users -----------------------------------------------------------------

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

    # -- Memberships -------------------------------------------------------------

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

    def get_my_memberships_verified_at(self) -> datetime | None:
        row = self._one("SELECT memberships_verified_at FROM app.users WHERE id = app.ctx_user_id()")
        return row["memberships_verified_at"] if row else None

    def replace_user_memberships(
        self, user_id: UUID, memberships: Sequence[ExternalMembership], verified_at: datetime
    ) -> bool:
        """Apply a directory read taken at ``verified_at``; refuse one older than the last applied.

        The user row lock serializes concurrent refreshes for one user, so a
        slow, older read can never overwrite (or resurrect) what a newer read
        applied. It is ``FOR NO KEY UPDATE``: a privileged write that holds its
        membership lock and then inserts a row referencing the user (an audit
        entry) takes ``FOR KEY SHARE`` on this row, and must not deadlock with
        a refresh waiting for that membership.

        Organizations and memberships are written in two statements, however
        many there are, in a fixed order, so two users' refreshes that create
        the same new organizations cannot deadlock each other.
        """
        row = self._one("SELECT memberships_verified_at FROM app.users WHERE id = %s FOR NO KEY UPDATE", (user_id,))
        if row is None or (row["memberships_verified_at"] is not None and row["memberships_verified_at"] > verified_at):
            return False
        # One row per organization: a paged read can list one twice, and the last listing wins.
        role_by_org = {m.external_org_id: m.role.value for m in memberships}
        external_ids, roles = list(role_by_org), list(role_by_org.values())
        self._exec(
            """
            INSERT INTO app.organizations (clerk_org_id)
            SELECT clerk_org_id FROM unnest(%s::text[]) AS listed(clerk_org_id)
            ORDER BY clerk_org_id
            ON CONFLICT (clerk_org_id) DO NOTHING
            """,
            (external_ids,),
        )
        self._exec(
            """
            INSERT INTO app.organization_memberships (org_id, user_id, role, verified_at)
            SELECT o.id, %s, listed.role, %s
            FROM unnest(%s::text[], %s::text[]) AS listed(clerk_org_id, role)
            JOIN app.organizations o ON o.clerk_org_id = listed.clerk_org_id AND o.deleted_at IS NULL
            ORDER BY listed.clerk_org_id
            ON CONFLICT (org_id, user_id)
            DO UPDATE SET role = EXCLUDED.role, verified_at = EXCLUDED.verified_at
            """,
            (user_id, verified_at, external_ids, roles),
        )
        self._exec(
            """
            DELETE FROM app.organization_memberships m
            USING app.organizations o
            WHERE m.org_id = o.id AND m.user_id = %s AND NOT (o.clerk_org_id = ANY(%s))
            """,
            (user_id, external_ids),
        )
        self._exec("UPDATE app.users SET memberships_verified_at = %s WHERE id = %s", (verified_at, user_id))
        return True

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

    def remove_user_memberships(self, user_id: UUID, verified_at: datetime) -> None:
        """Remove every membership, stamped like a read at ``verified_at`` that found none.

        Unlike ``replace_user_memberships``, this is never refused: a deleted
        user has no memberships, whatever an earlier read applied. The
        watermark only moves forward, so a slower read taken before
        ``verified_at`` is refused afterwards. The user row is locked first,
        in the same order as a refresh.
        """
        self._exec(
            "UPDATE app.users SET memberships_verified_at = GREATEST(memberships_verified_at, %s) WHERE id = %s",
            (verified_at, user_id),
        )
        self._exec("DELETE FROM app.organization_memberships WHERE user_id = %s", (user_id,))

    def list_users_with_stale_memberships(self, older_than: datetime, limit: int) -> list[tuple[UUID, str]]:
        """Users with a membership verified before ``older_than``, least recently tried or read first.

        Tried by the reconcile job, or read by anything (a request, a webhook,
        the job). Users never read come last. Walks
        ``users_memberships_reconcile_idx`` in order, skipping users without a
        stale membership, and stops at ``limit``.
        """
        rows = self._all(
            """
            SELECT u.id, u.clerk_user_id
            FROM app.users u
            WHERE EXISTS (
              SELECT 1 FROM app.organization_memberships m
              WHERE m.user_id = u.id AND m.verified_at < %s
            )
            ORDER BY GREATEST(u.memberships_checked_at, u.memberships_verified_at), u.id
            LIMIT %s
            """,
            (older_than, limit),
        )
        return [(r["id"], r["clerk_user_id"]) for r in rows]

    def mark_memberships_checked(self, user_id: UUID, at: datetime) -> None:
        self._exec("UPDATE app.users SET memberships_checked_at = %s WHERE id = %s", (at, user_id))

    # -- Webhook ledger ----------------------------------------------------------

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
