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
from app.kernel.ports import ConnectionDescription

_MAX_ERROR_LENGTH = 500


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
        row = self._one(
            "SELECT current_user AS role, current_setting('server_version') AS server_version, "
            "current_setting('row_security') = 'on' AS row_security, "
            "coalesce(current_setting('app.user_id', true), '') <> '' AS tenant_context_set"
        )
        if row is None:  # pragma: no cover - a scalar SELECT always returns a row
            raise RuntimeError("connection description unavailable")
        return ConnectionDescription(
            role=row["role"],
            server_version=row["server_version"].split(" ")[0],
            row_security=row["row_security"],
            tenant_context_set=row["tenant_context_set"],
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

    def prune_counters(self, older_than: datetime) -> int:
        with self._conn.cursor() as cur:
            cur.execute("DELETE FROM app.rate_limit_counters WHERE window_start < %s", (older_than,))
            return cur.rowcount

    # -- Outbox -----------------------------------------------------------------

    def claim_outbox(
        self, *, limit: int, now: datetime, lease_seconds: int, ids: Sequence[UUID] | None
    ) -> list[StoredEvent]:
        """Lease up to ``limit`` due events. Rows leased by a crashed worker become due again."""
        rows = self._all(
            """
            UPDATE app.outbox o
            SET status = 'processing', attempts = o.attempts + 1, locked_until = %(lease_end)s
            WHERE o.id IN (
              SELECT id FROM app.outbox
              WHERE ((status = 'pending' AND available_at <= %(now)s)
                     OR (status = 'processing' AND locked_until < %(now)s))
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

    def prune_outbox(self, processed_before: datetime) -> int:
        """Delete processed events past retention. Dead events stay for inspection."""
        with self._conn.cursor() as cur:
            cur.execute("DELETE FROM app.outbox WHERE status = 'processed' AND processed_at < %s", (processed_before,))
            return cur.rowcount

    def outbox_counts(self) -> Mapping[str, int]:
        rows = self._all("SELECT status, count(*) AS n FROM app.outbox GROUP BY status")
        return {r["status"]: int(r["n"]) for r in rows}
