"""PostgreSQL connections, role-scoped transactions, and request-scoped
transactions (DEC-008).

Every transaction first switches to a non-privileged role (``app_api`` or
``app_worker``) and sets the transaction-local tenant context that row-level
security reads. Role, statement timeout, and context are set in a single
statement, because each statement is a network round trip to the database.

**Two pools.** ``app_api`` work and ``app_worker`` work use separate
connection pools. A request-scoped transaction holds one ``app_api``
connection for most of a request. If that request needs a worker transaction
(a rate-limit counter, a membership refresh), the worker connection comes
from the other pool. So requests holding API connections can never starve the
pool they are waiting on.

**Request-scoped transactions.** Routes that only touch the database can opt
in (see ``app.kernel.http.request_scope``). Inside the scope, every
``app_api`` block joins one transaction, and the context is re-applied only
when it changes. That folds identity lookup, membership check, and the data
query into a single transaction. Semantics:

- a block that raises rolls the request transaction back;
- the transaction commits when the request ends, before the response is sent;
- ``release()`` commits early, before slow external calls;
- ``app_worker`` blocks never join; they always commit on their own.
"""

from __future__ import annotations

import contextvars
import threading
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import ExitStack, contextmanager
from typing import TypeVar
from uuid import UUID

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool, PoolTimeout

from app.kernel.errors import DependencyUnavailableError
from app.kernel.events import DomainEvent
from app.kernel.ports import AuditEntry, Clock
from app.kernel.tenancy import Role, TenantContext

API_ROLE = "app_api"
WORKER_ROLE = "app_worker"
# Every transaction sets all of these, so no context can leak between blocks.
CONTEXT_KEYS = ("app.clerk_user_id", "app.user_id", "app.org_id")
_SET_CONFIG = sql.SQL("set_config(%s, %s, true)")

R = TypeVar("R")

_UNAVAILABLE = "The database is temporarily unavailable."


class Database:
    """Lazily opened, concurrency-safe connection pools (one per process)."""

    def __init__(
        self,
        conninfo: str,
        *,
        api_pool_size: int,
        worker_pool_size: int,
        connect_timeout: float,
        statement_timeout_ms: int,
    ) -> None:
        self._conninfo = conninfo
        self._sizes = {API_ROLE: api_pool_size, WORKER_ROLE: worker_pool_size}
        self._connect_timeout = connect_timeout
        self._statement_timeout_ms = statement_timeout_ms
        self._pools: dict[str, ConnectionPool] = {}
        self._lock = threading.Lock()
        self._scope: contextvars.ContextVar[RequestScope | None] = contextvars.ContextVar(
            f"request_scope_{id(self)}", default=None
        )

    def _pool(self, role: str) -> ConnectionPool:
        pool = self._pools.get(role)
        if pool is None:
            with self._lock:
                pool = self._pools.get(role)
                if pool is None:
                    pool = ConnectionPool(
                        self._conninfo,
                        min_size=0,
                        max_size=self._sizes[role],
                        timeout=self._connect_timeout,
                        # Transaction-mode poolers (e.g. Supabase's) do not
                        # support server-side prepared statements.
                        kwargs={"prepare_threshold": None, "connect_timeout": int(self._connect_timeout)},
                        open=True,
                        name=role,
                    )
                    self._pools[role] = pool
        return pool

    def setup_statement(self, role: str, context: Mapping[str, str]) -> tuple[sql.Composed, list[str]]:
        """The single statement that applies role, timeout, and full tenant context.

        ``set_config('role', ...)`` changes the same setting as ``SET LOCAL
        ROLE``, with the same membership check. Every value is a bound parameter.
        """
        if role not in (API_ROLE, WORKER_ROLE):
            raise ValueError("unknown database role")
        unknown = set(context) - set(CONTEXT_KEYS)
        if unknown:
            raise ValueError(f"unknown context settings: {sorted(unknown)}")
        pairs = [("role", role), ("statement_timeout", str(self._statement_timeout_ms))]
        pairs += [(key, context.get(key, "")) for key in CONTEXT_KEYS]
        query = sql.SQL("SELECT ") + sql.SQL(", ").join([_SET_CONFIG] * len(pairs))
        return query, [item for pair in pairs for item in pair]

    @contextmanager
    def transaction(self, role: str, context: Mapping[str, str]) -> Iterator[psycopg.Connection]:
        query, params = self.setup_statement(role, context)
        scope = self._scope.get()
        if scope is not None and role == API_ROLE:
            with scope.block(query, params) as conn:
                yield conn
            return
        try:
            with self._pool(role).connection() as conn, conn.transaction():
                conn.execute(query, params)
                yield conn
        except (PoolTimeout, psycopg.OperationalError) as exc:
            raise DependencyUnavailableError(_UNAVAILABLE) from exc

    # -- Request scope ----------------------------------------------------------

    def begin_request_scope(self) -> contextvars.Token[RequestScope | None]:
        return self._scope.set(RequestScope(self))

    @property
    def api_pool_size(self) -> int:
        return self._sizes[API_ROLE]

    def end_request_scope(self, token: contextvars.Token[RequestScope | None]) -> Callable[[], None] | None:
        """End the scope in the caller's context; return the blocking commit to run, if any.

        The context variable must be reset in the context that set it (the
        event loop), while the commit belongs in a worker thread. Returns
        ``None`` when no transaction was opened, so no thread hop is needed.
        """
        scope = self._scope.get()
        self._scope.reset(token)
        return scope.commit if scope is not None and scope.is_open else None

    def release_request_transaction(self) -> None:
        scope = self._scope.get()
        if scope is not None:
            scope.commit()

    def in_request_transaction(self) -> bool:
        scope = self._scope.get()
        return scope is not None and scope.is_open

    def close(self) -> None:
        with self._lock:
            pools, self._pools = self._pools, {}
        for pool in pools.values():
            pool.close()


class RequestScope:
    """One lazily opened ``app_api`` transaction shared by a request's blocks."""

    def __init__(self, db: Database) -> None:
        self._db = db
        self._stack: ExitStack | None = None
        self._conn: psycopg.Connection | None = None
        self._applied: list[str] | None = None
        self._depth = 0

    @property
    def is_open(self) -> bool:
        return self._conn is not None

    @contextmanager
    def block(self, query: sql.Composed, params: list[str]) -> Iterator[psycopg.Connection]:
        if self._depth:
            # A nested block would switch the shared transaction's context
            # under the outer block, and its rollback would release the
            # connection the outer block still uses.
            raise RuntimeError("Nested database blocks are not supported inside a request transaction.")
        self._depth += 1
        try:
            yield from self._block(query, params)
        finally:
            self._depth -= 1

    def _block(self, query: sql.Composed, params: list[str]) -> Iterator[psycopg.Connection]:
        try:
            conn = self._open()
            if params != self._applied:
                conn.execute(query, params)
                self._applied = params
        except (PoolTimeout, psycopg.OperationalError) as exc:
            self._rollback(exc)
            raise DependencyUnavailableError(_UNAVAILABLE) from exc
        try:
            yield conn
        except BaseException as exc:
            self._rollback(exc)
            if isinstance(exc, psycopg.OperationalError):
                raise DependencyUnavailableError(_UNAVAILABLE) from exc
            raise

    def _open(self) -> psycopg.Connection:
        if self._conn is None:
            stack = ExitStack()
            try:
                conn = stack.enter_context(self._db._pool(API_ROLE).connection())
                stack.enter_context(conn.transaction())
            except BaseException:
                stack.close()
                raise
            self._stack, self._conn, self._applied = stack, conn, None
        return self._conn

    def commit(self) -> None:
        stack, self._stack, self._conn, self._applied = self._stack, None, None, None
        if stack is not None:
            try:
                stack.close()  # exits the transaction (COMMIT), then returns the connection
            except psycopg.OperationalError as exc:
                raise DependencyUnavailableError(_UNAVAILABLE) from exc

    def _rollback(self, exc: BaseException) -> None:
        stack, self._stack, self._conn, self._applied = self._stack, None, None, None
        if stack is not None:
            stack.__exit__(type(exc), exc, exc.__traceback__)


# ---------------------------------------------------------------------------
# Generic transactions for modules
# ---------------------------------------------------------------------------


class PgTx[R]:
    """``Tx`` implementation: a module repository plus kernel facilities on one connection."""

    def __init__(
        self,
        conn: psycopg.Connection,
        repo: R,
        on_record: Callable[[UUID], None] | None,
        clock: Clock | None,
    ) -> None:
        self._conn = conn
        self._repo = repo
        self._on_record = on_record
        self._clock = clock

    @property
    def repo(self) -> R:
        return self._repo

    def record(self, event: DomainEvent) -> None:
        # The ID is generated here: RETURNING would need a SELECT policy, and
        # request roles may only append to the outbox.
        event_id = uuid.uuid4()
        # The application clock (not the database's) schedules outbox work, so
        # occurrence, availability, leases, and backoff all use one timeline.
        now = self._clock.now() if self._clock is not None else None
        self._conn.execute(
            """
            INSERT INTO app.outbox (id, event_type, payload, occurred_at, available_at)
            VALUES (%s, %s, %s, coalesce(%s, now()), coalesce(%s, now()))
            """,
            (event_id, event.event_type, Jsonb(event.payload()), now, now),
        )
        if self._on_record is not None:
            self._on_record(event_id)

    def audit(self, entry: AuditEntry) -> None:
        self._conn.execute(
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

    def lock_my_role(self, org_id: UUID) -> Role | None:
        row = self._conn.execute("SELECT app.lock_my_membership(%s)", (org_id,)).fetchone()
        return Role(row[0]) if row and row[0] else None


class PgTransactions[R]:
    """``Transactions[R]`` for one module: builds its repository on each transaction."""

    def __init__(
        self,
        db: Database,
        factory: Callable[[psycopg.Connection], R],
        on_record: Callable[[UUID], None] | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._db = db
        self._factory = factory
        self._on_record = on_record
        self._clock = clock

    @contextmanager
    def as_identity(self, external_user_id: str) -> Iterator[PgTx[R]]:
        with self._db.transaction(API_ROLE, {"app.clerk_user_id": external_user_id}) as conn:
            yield PgTx(conn, self._factory(conn), self._on_record, self._clock)

    @contextmanager
    def as_tenant(self, ctx: TenantContext) -> Iterator[PgTx[R]]:
        context = {
            "app.clerk_user_id": ctx.external_user_id,
            "app.user_id": str(ctx.user_id),
            "app.org_id": str(ctx.org_id) if ctx.org_id else "",
        }
        with self._db.transaction(API_ROLE, context) as conn:
            yield PgTx(conn, self._factory(conn), self._on_record, self._clock)

    @contextmanager
    def as_system(self) -> Iterator[PgTx[R]]:
        with self._db.transaction(WORKER_ROLE, {}) as conn:
            yield PgTx(conn, self._factory(conn), self._on_record, self._clock)

    def release(self) -> None:
        self._db.release_request_transaction()
