"""PostgreSQL adapter for the kernel's own store: health, rate-limit counters,
and the outbox. All SQL uses bound parameters only."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from app.kernel.events import StoredEvent
from app.kernel.ports import ConnectionDescription, LoginRole

_MAX_ERROR_LENGTH = 500

# What the login role could do in a query that does not switch role. Reads catalogs every role can read.
_LOGIN_ROLE_SQL = """
SELECT s.rolsuper AS superuser, s.rolbypassrls AS bypass_rls, s.rolcreaterole AS create_role,
  EXISTS (
    SELECT 1 FROM pg_roles r
    WHERE (r.rolsuper OR r.rolbypassrls) AND r.oid <> s.oid AND pg_has_role(s.oid, r.oid, 'MEMBER')
  ) AS privileged_membership,
  ARRAY(
    SELECT c.relname::text FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'app' AND c.relkind IN ('r', 'p', 'v', 'm') AND pg_has_role(s.oid, c.relowner, 'MEMBER')
    ORDER BY 1
  ) AS owned_tables
FROM pg_roles s WHERE s.rolname = session_user
"""


def describe_login_role(conn: psycopg.Connection) -> LoginRole:
    """The role this connection logged in as, whatever role the transaction has switched to."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_LOGIN_ROLE_SQL)
        row = cur.fetchone()
    if row is None:  # pragma: no cover - the login role is always in pg_roles
        raise RuntimeError("login role description unavailable")
    return LoginRole(
        superuser=row["superuser"],
        bypass_rls=row["bypass_rls"],
        create_role=row["create_role"],
        privileged_membership=row["privileged_membership"],
        owned_tables=tuple(row["owned_tables"]),
    )


class PgKernelStore:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def _one(self, query: str, params: Sequence[object] | Mapping[str, object] = ()) -> dict[str, Any] | None:
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query, params)
            return cur.fetchone()

    def _all(self, query: str, params: Sequence[object] | Mapping[str, object] = ()) -> list[dict[str, Any]]:
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query, params)
            return cur.fetchall()

    # -- Health ---------------------------------------------------------------

    def ping(self) -> None:
        self._one("SELECT 1 AS ok")

    def describe_connection(self) -> ConnectionDescription:
        # The catalogs read here are readable by every role, including app_api.
        row = self._one(
            """
            SELECT current_user AS role, current_setting('server_version') AS server_version,
              current_setting('row_security') = 'on' AS row_security,
              coalesce(current_setting('app.user_id', true), '') <> '' AS tenant_context_set,
              r.rolsuper AS superuser, r.rolbypassrls AS bypass_rls,
              ARRAY(
                SELECT c.relname::text FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'app' AND c.relkind IN ('r', 'p')
                  AND NOT (c.relrowsecurity AND c.relforcerowsecurity)
                ORDER BY 1
              ) AS unprotected_tables,
              ARRAY(
                SELECT c.relname::text FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'app' AND c.relkind IN ('r', 'p')
                  AND pg_has_role(current_user, c.relowner, 'USAGE')
                ORDER BY 1
              ) AS owned_tables
            FROM pg_roles r WHERE r.rolname = current_user
            """
        )
        if row is None:  # pragma: no cover - the current role is always in pg_roles
            raise RuntimeError("connection description unavailable")
        return ConnectionDescription(
            role=row["role"],
            server_version=row["server_version"].split(" ")[0],
            row_security=row["row_security"],
            tenant_context_set=row["tenant_context_set"],
            superuser=row["superuser"],
            bypass_rls=row["bypass_rls"],
            unprotected_tables=tuple(row["unprotected_tables"]),
            owned_tables=tuple(row["owned_tables"]),
            login=describe_login_role(self._conn),
        )

    # -- Rate limits ------------------------------------------------------------

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

    def prune_counters(self, older_than: datetime, limit: int) -> int:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM app.rate_limit_counters WHERE (bucket_key, window_start) IN (
                  SELECT bucket_key, window_start FROM app.rate_limit_counters
                  WHERE window_start < %s LIMIT %s
                )
                """,
                (older_than, limit),
            )
            return cur.rowcount

    # -- Outbox -----------------------------------------------------------------

    def claim_outbox(
        self, *, limit: int, now: datetime, lease_seconds: int, max_attempts: int, ids: Sequence[UUID] | None
    ) -> list[StoredEvent]:
        """Lease up to ``limit`` pending, due events with attempts left.

        A leased event is never re-leased here, even after its lease expired:
        ``expired_outbox_leases`` first turns it back into a retry (or a dead
        event), so the attempt limit holds and a crashing event goes to the
        back of the queue.
        """
        rows = self._all(
            """
            UPDATE app.outbox o
            SET status = 'processing', attempts = o.attempts + 1, locked_until = %(lease_end)s
            WHERE o.id IN (
              SELECT id FROM app.outbox
              WHERE status = 'pending' AND available_at <= %(now)s AND attempts < %(max_attempts)s
                AND (%(ids)s::uuid[] IS NULL OR id = ANY(%(ids)s::uuid[]))
              ORDER BY available_at, id
              LIMIT %(limit)s
              FOR UPDATE SKIP LOCKED
            )
            RETURNING o.id, o.event_type, o.payload, o.occurred_at, o.attempts
            """,
            {
                "lease_end": now + timedelta(seconds=lease_seconds),
                "now": now,
                "max_attempts": max_attempts,
                "ids": list(ids) if ids is not None else None,
                "limit": limit,
            },
        )
        return [
            StoredEvent(r["id"], r["event_type"], r["payload"], r["occurred_at"], r["attempts"])
            for r in sorted(rows, key=lambda r: (r["occurred_at"], r["id"]))
        ]

    # Completion and failure are fenced to the lease: a worker whose lease
    # expired (and whose event another worker re-leased) changes nothing.

    def complete_outbox(self, event_id: UUID, attempt: int, processed_at: datetime) -> bool:
        row = self._one(
            """
            UPDATE app.outbox SET status = 'processed', processed_at = %s, locked_until = NULL, last_error = NULL
            WHERE id = %s AND status = 'processing' AND attempts = %s RETURNING id
            """,
            (processed_at, event_id, attempt),
        )
        return row is not None

    def fail_outbox(self, event_id: UUID, attempt: int, error: str, retry_at: datetime | None) -> bool:
        row = self._one(
            """
            UPDATE app.outbox
            SET status = CASE WHEN %(retry_at)s::timestamptz IS NULL THEN 'dead' ELSE 'pending' END,
                available_at = coalesce(%(retry_at)s::timestamptz, available_at),
                locked_until = NULL,
                last_error = %(error)s
            WHERE id = %(id)s AND status = 'processing' AND attempts = %(attempt)s RETURNING id
            """,
            {"retry_at": retry_at, "error": error[:_MAX_ERROR_LENGTH], "id": event_id, "attempt": attempt},
        )
        return row is not None

    def expired_outbox_leases(self, now: datetime, limit: int) -> list[tuple[UUID, int]]:
        """Leased events whose worker stopped (a crash or a platform timeout) before recording an outcome.

        The rows stay locked until the transaction ends, so concurrent drains
        skip them, and ``fail_outbox`` with the returned attempt is fenced as usual.
        """
        rows = self._all(
            """
            SELECT id, attempts FROM app.outbox
            WHERE status = 'processing' AND locked_until < %s
            ORDER BY locked_until, id
            LIMIT %s
            FOR UPDATE SKIP LOCKED
            """,
            (now, limit),
        )
        return [(r["id"], int(r["attempts"])) for r in rows]

    def dead_letter_exhausted_outbox(self, max_attempts: int, limit: int) -> int:
        """Pending events that already used ``max_attempts`` (it was lowered) can never be leased: mark them dead."""
        with self._conn.cursor() as cur:
            cur.execute(
                """
                UPDATE app.outbox SET status = 'dead',
                  last_error = 'no attempts left: OUTBOX_MAX_ATTEMPTS is lower than the attempts already made'
                WHERE id IN (
                  SELECT id FROM app.outbox
                  WHERE status = 'pending' AND attempts >= %s
                  LIMIT %s
                  FOR UPDATE SKIP LOCKED
                )
                """,
                (max_attempts, limit),
            )
            return cur.rowcount

    def prune_outbox(self, processed_before: datetime, limit: int) -> int:
        """Delete processed events past retention. Dead events stay for inspection."""
        with self._conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM app.outbox WHERE id IN (
                  SELECT id FROM app.outbox WHERE status = 'processed' AND processed_at < %s
                  ORDER BY processed_at LIMIT %s
                )
                """,
                (processed_before, limit),
            )
            return cur.rowcount

    def outbox_counts(self) -> Mapping[str, int]:
        rows = self._all("SELECT status, count(*) AS n FROM app.outbox GROUP BY status")
        return {r["status"]: int(r["n"]) for r in rows}
