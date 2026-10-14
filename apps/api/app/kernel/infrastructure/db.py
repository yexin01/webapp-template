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

**Faults** (ARCH-07, §16.4; found by the DEC-001 proof):

- Every round trip has a client-side deadline (``BoundedConnection``), because
  ``statement_timeout`` is enforced by the server and cannot end a call when
  the server, or a pooler in front of it, stops responding.
- A pooled connection that died while idle (after a pooler restart or a
  failover) fails on its first round trip, before any work has run. The
  transaction is then started again on another connection.
"""

from __future__ import annotations

import contextvars
import threading
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import ExitStack, contextmanager
from typing import TypeVar
from uuid import UUID

import psycopg
from psycopg import sql
from psycopg.abc import PQGen
from psycopg.rows import TupleRow
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
RV = TypeVar("RV")

_UNAVAILABLE = "The database is temporarily unavailable."
_PSYCOPG_WAIT_INTERVAL = 0.1  # psycopg's default; how often a wait wakes to notice interrupts


class BoundedConnection(psycopg.Connection[TupleRow]):
    """A connection whose every round trip has a client-side deadline (ARCH-07).

    psycopg sends every round trip (execute, fetch, commit, rollback) through
    ``wait``. Past the deadline the request is still in flight, so the
    connection is closed: the caller gets ``OperationalError`` (a 503), the
    server aborts the transaction, and the pool replaces the connection.
    """

    round_trip_timeout: float = 15.0  # set per pool by ``Database``

    def wait(self, gen: PQGen[RV], interval: float = _PSYCOPG_WAIT_INTERVAL, timeout: float | None = None) -> RV:
        deadline = self.round_trip_timeout if timeout is None else timeout
        started = time.monotonic()
        try:
            return super().wait(gen, interval, deadline)
        except psycopg.OperationalError as exc:
            if time.monotonic() - started < deadline:
                raise
            self.close()
            raise psycopg.OperationalError(f"No response from the database within {deadline:g} s.") from exc


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
        # Longer than any statement the server allows, plus a connect timeout of slack.
        self.round_trip_timeout = statement_timeout_ms / 1000 + connect_timeout
        self._pools: dict[str, ConnectionPool[BoundedConnection]] = {}
        self._lock = threading.Lock()
        self._scope: contextvars.ContextVar[RequestScope | None] = contextvars.ContextVar(
            f"request_scope_{id(self)}", default=None
        )

    def _pool(self, role: str) -> ConnectionPool[BoundedConnection]:
        pool = self._pools.get(role)
        if pool is None:
            with self._lock:
                pool = self._pools.get(role)
                if pool is None:
                    pool = ConnectionPool(
                        self._conninfo,
                        connection_class=BoundedConnection,
                        configure=self._configure,
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

    def _configure(self, conn: BoundedConnection) -> None:
        conn.round_trip_timeout = self.round_trip_timeout

    def begin(self, stack: ExitStack, role: str, query: sql.Composed, params: list[str]) -> psycopg.Connection:
        """Check out a connection into ``stack``, open a transaction, and apply the setup statement.

        A connection that died while idle fails on its first round trip, before
        any work has run, so the next pooled connection is tried. Each dead one
        is discarded, so this ends after at most one attempt per pooled
        connection plus one on a new connection. A connection that timed out is
        closed, not broken, and is never retried.
        """
        pool = self._pool(role)
        for _ in range(pool.max_size + 1):
            attempt = ExitStack()
            conn: BoundedConnection | None = None
            try:
                conn = attempt.enter_context(pool.connection())
                attempt.enter_context(conn.transaction())
                conn.execute(query, params)
            except BaseException as exc:
                attempt.__exit__(type(exc), exc, exc.__traceback__)
                if isinstance(exc, psycopg.OperationalError) and conn is not None and conn.broken:
                    continue
                raise
            stack.enter_context(attempt)
            return conn
        raise psycopg.OperationalError("Every pooled database connection was lost.")

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
            with ExitStack() as stack:
                yield self.begin(stack, role, query, params)
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
            if self._conn is None:
                conn = self._open(query, params)
            else:
                conn = self._conn
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

    def _open(self, query: sql.Composed, params: list[str]) -> psycopg.Connection:
        stack = ExitStack()
        try:
            conn = self._db.begin(stack, API_ROLE, query, params)
        except BaseException:
            stack.close()
            raise
        self._stack, self._conn, self._applied = stack, conn, params
        return conn

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
