"""PostgreSQL connection management and role-scoped transactions (DEC-008).

The only way to obtain a database handle is through ``PgUnitOfWork``, and
every transaction it opens first switches to a non-privileged role
(``app_api`` or ``app_worker``) and sets the transaction-local tenant context
read by row-level security policies.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg_pool import ConnectionPool, PoolTimeout

from app.domain.errors import DependencyUnavailableError
from app.domain.tenancy import TenantContext
from app.infrastructure.repositories import PgRepository
from app.ports import Repository, SystemRepository

API_ROLE = "app_api"
WORKER_ROLE = "app_worker"


class Database:
    """Lazily opened, concurrency-safe connection pool (one per process)."""

    def __init__(
        self,
        conninfo: str,
        *,
        max_size: int,
        connect_timeout: float,
        statement_timeout_ms: int,
    ) -> None:
        self._conninfo = conninfo
        self._max_size = max_size
        self._connect_timeout = connect_timeout
        self._statement_timeout_ms = statement_timeout_ms
        self._pool: ConnectionPool | None = None
        self._lock = threading.Lock()

    def _get_pool(self) -> ConnectionPool:
        if self._pool is None:
            with self._lock:
                if self._pool is None:
                    self._pool = ConnectionPool(
                        self._conninfo,
                        min_size=0,
                        max_size=self._max_size,
                        timeout=self._connect_timeout,
                        # Transaction-mode poolers (e.g. Supabase's) do not
                        # support server-side prepared statements.
                        kwargs={"prepare_threshold": None, "connect_timeout": int(self._connect_timeout)},
                        open=True,
                        name="app",
                    )
        return self._pool

    @contextmanager
    def transaction(self, role: str, settings: dict[str, str]) -> Iterator[psycopg.Connection]:
        if role not in (API_ROLE, WORKER_ROLE):
            raise ValueError("unknown database role")
        try:
            with self._get_pool().connection() as conn, conn.transaction():
                conn.execute(f"SET LOCAL ROLE {role}")
                conn.execute(
                    "SELECT set_config('statement_timeout', %s, true)",
                    (str(self._statement_timeout_ms),),
                )
                for key, value in settings.items():
                    conn.execute("SELECT set_config(%s, %s, true)", (key, value))
                yield conn
        except (PoolTimeout, psycopg.OperationalError) as exc:
            raise DependencyUnavailableError("The database is temporarily unavailable.") from exc

    def close(self) -> None:
        if self._pool is not None:
            self._pool.close()
            self._pool = None


class PgUnitOfWork:
    def __init__(self, db: Database) -> None:
        self._db = db

    @contextmanager
    def as_identity(self, external_user_id: str) -> Iterator[Repository]:
        with self._db.transaction(API_ROLE, {"app.clerk_user_id": external_user_id}) as conn:
            yield PgRepository(conn)

    @contextmanager
    def as_tenant(self, ctx: TenantContext) -> Iterator[Repository]:
        settings = {
            "app.clerk_user_id": ctx.external_user_id,
            "app.user_id": str(ctx.user_id),
            "app.org_id": str(ctx.org_id) if ctx.org_id else "",
        }
        with self._db.transaction(API_ROLE, settings) as conn:
            yield PgRepository(conn)

    @contextmanager
    def as_system(self) -> Iterator[SystemRepository]:
        with self._db.transaction(WORKER_ROLE, {}) as conn:
            yield PgRepository(conn)
