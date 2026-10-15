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
- the transaction commits when the request ends, before the response is sent,
  if the handler returned or raised a ``DomainError``; any other error rolls
  it back;
- a block whose transaction can no longer commit (a caught statement error,
  a lost connection) fails instead of ending as a success;
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
import functools
import logging
import math
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
from psycopg.pq import TransactionStatus
from psycopg.rows import TupleRow
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool, PoolTimeout

from app.kernel.errors import DependencyUnavailableError, DomainError
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

# Threads the HTTP server runs blocking work on (AnyIO's default, set explicitly
# at startup). Request connections are bounded against it; see ``Database``.
REQUEST_THREADS = 40

logger = logging.getLogger(__name__)


class BoundedConnection(psycopg.Connection[TupleRow]):
    """A connection whose every round trip has a client-side deadline (ARCH-07).

    psycopg sends every round trip (execute, fetch, commit, rollback) through
    ``wait``. Past the deadline the request is still in flight, so the
    connection is closed, the caller gets ``OperationalError`` (a 503), and
    the pool replaces the connection. The server then discards any open
    transaction, except that a ``COMMIT`` it had already applied stays
    committed: after a deadline on ``COMMIT``, the outcome is unknown.
    """

    round_trip_timeout: float = 15.0  # set per pool by ``Database``

    def wait(self, gen: PQGen[RV], interval: float = _PSYCOPG_WAIT_INTERVAL, timeout: float | None = None) -> RV:
        if timeout is not None:
            # psycopg's own timed waits (``notifies``) expect its timeout error and a usable connection.
            return super().wait(gen, interval, timeout)
        deadline = self.round_trip_timeout
        started = time.monotonic()
        try:
            return super().wait(gen, interval, deadline)
        except psycopg.OperationalError as exc:
            if time.monotonic() - started < deadline:
                raise
            self.close()
            raise psycopg.OperationalError(f"No response from the database within {deadline:g} s.") from exc


class Database:
    """Lazily opened, concurrency-safe connection pools (one per process).

    **Threads waiting for request connections.** A scoped request holds an
    ``app_api`` connection across several threadpool hops, and needs a thread
    for each. Threads that block waiting for an ``app_api`` connection outside
    a request scope must therefore never fill the threadpool, or the holders
    they wait for cannot finish. At most ``request_threads - api_pool_size``
    threads may wait at once; past that, a call fails at once with a 503,
    holding no thread. With at most ``api_pool_size`` scoped requests admitted,
    waiting threads plus scoped requests never exceed ``request_threads``.
    """

    def __init__(
        self,
        conninfo: str,
        *,
        api_pool_size: int,
        worker_pool_size: int,
        connect_timeout: float,
        statement_timeout_ms: int,
        request_threads: int = REQUEST_THREADS,
    ) -> None:
        self._conninfo = conninfo
        self._sizes = {API_ROLE: api_pool_size, WORKER_ROLE: worker_pool_size}
        self._connect_timeout = connect_timeout
        self._statement_timeout_ms = statement_timeout_ms
        # Longer than any statement the server allows, plus a connect timeout of slack.
        self.round_trip_timeout = statement_timeout_ms / 1000 + connect_timeout
        self.api_waiters_limit = max(1, request_threads - api_pool_size)
        self._api_waiters = threading.BoundedSemaphore(self.api_waiters_limit)
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
                        kwargs={
                            "prepare_threshold": None,
                            # libpq takes whole seconds; never round a short timeout down to 0 (no limit).
                            "connect_timeout": max(1, math.ceil(self._connect_timeout)),
                            # Keepalives notice a peer that vanished without closing the
                            # connection (a failover), so an idle pooled connection fails
                            # fast and is replaced instead of waiting out the deadline.
                            "keepalives": 1,
                            "keepalives_idle": 30,
                            "keepalives_interval": 10,
                            "keepalives_count": 3,
                        },
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
        if scope is not None and role == API_ROLE and not scope.released:
            with scope.block(query, params) as conn:
                yield conn
            return
        try:
            with ExitStack() as stack:
                conn = self._begin_outside_scope(stack, role, query, params)
                yield conn
                _require_committable(conn)
        except (PoolTimeout, psycopg.OperationalError) as exc:
            raise DependencyUnavailableError(_UNAVAILABLE) from exc

    def _begin_outside_scope(
        self, stack: ExitStack, role: str, query: sql.Composed, params: list[str]
    ) -> psycopg.Connection:
        """``begin``, bounding the threads that wait for a request connection (see the class docstring)."""
        if role != API_ROLE:
            return self.begin(stack, role, query, params)
        if not self._api_waiters.acquire(blocking=False):
            logger.warning("database_waiters_exhausted", extra={"limit": self.api_waiters_limit})
            raise DependencyUnavailableError(_UNAVAILABLE)
        try:
            return self.begin(stack, role, query, params)
        finally:
            self._api_waiters.release()

    # -- Request scope ----------------------------------------------------------

    def begin_request_scope(
        self, on_release: Callable[[], None] | None = None
    ) -> contextvars.Token[RequestScope | None]:
        """Start a request scope. ``on_release`` runs once ``release()`` has ended it early."""
        return self._scope.set(RequestScope(self, on_release))

    @property
    def api_pool_size(self) -> int:
        return self._sizes[API_ROLE]

    def end_request_scope(
        self, token: contextvars.Token[RequestScope | None], *, error: BaseException | None
    ) -> Callable[[], None] | None:
        """End the scope in the caller's context; return the blocking commit or rollback to run, if any.

        The context variable must be reset in the context that set it (the
        event loop), while the commit belongs in a worker thread. Returns
        ``None`` when no transaction is open, so no thread hop is needed.

        ``error`` is what the request raised, or ``None`` if it completed:

        - Completed, or a ``DomainError``: commit. A domain error is an
          expected outcome whose writes must persist, such as the audit entry
          of a denied action (NT-07).
        - Any other error (a bug, a cancelled request): roll back, so an
          unexpected failure never leaves committed writes behind it.

        If a block rolled the request transaction back and the handler still
        completed, the work of earlier blocks is gone, so the request fails
        instead of reporting a success that did not happen.
        """
        scope = self._scope.get()
        self._scope.reset(token)
        if scope is None:
            return None
        if error is None and scope.rolled_back:
            raise RuntimeError(
                "A database block raised and rolled back the request transaction, but the request completed. "
                "Let the error propagate, or use explicit transactions for this route."
            )
        if not scope.is_open:
            return None
        if error is None or isinstance(error, DomainError):
            return scope.commit
        return functools.partial(scope.rollback, error)

    def release_request_transaction(self) -> None:
        scope = self._scope.get()
        if scope is not None and not scope.released:
            scope.release()

    def in_request_transaction(self) -> bool:
        scope = self._scope.get()
        return scope is not None and scope.is_open

    def close(self) -> None:
        with self._lock:
            pools, self._pools = self._pools, {}
        for pool in pools.values():
            pool.close()


class RequestScope:
    """One lazily opened ``app_api`` transaction shared by a request's blocks.

    After ``release()``, the scope is over: the remaining blocks of the request
    run as ordinary short transactions.
    """

    def __init__(self, db: Database, on_release: Callable[[], None] | None = None) -> None:
        self._db = db
        self._on_release = on_release
        self._stack: ExitStack | None = None
        self._conn: psycopg.Connection | None = None
        self._applied: list[str] | None = None
        self._depth = 0
        self.rolled_back = False
        self.released = False

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
        if self.rolled_back:
            # The earlier blocks' work is gone; continuing would commit only part of the request.
            raise RuntimeError("The request transaction was rolled back by an earlier error.")
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
        except BaseException as exc:
            # Whatever failed, the shared transaction must not stay open in an unknown state.
            self._rollback(exc)
            if isinstance(exc, (PoolTimeout, psycopg.OperationalError)):
                raise DependencyUnavailableError(_UNAVAILABLE) from exc
            raise
        try:
            yield conn
            _require_committable(conn)
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
        if self._conn is None:
            return
        try:
            _require_committable(self._conn)
        except BaseException as exc:
            self._rollback(exc)
            if isinstance(exc, psycopg.OperationalError):
                raise DependencyUnavailableError(_UNAVAILABLE) from exc
            raise
        stack, self._stack, self._conn, self._applied = self._stack, None, None, None
        if stack is not None:
            try:
                stack.close()  # exits the transaction (COMMIT), then returns the connection
            except psycopg.OperationalError as exc:
                raise DependencyUnavailableError(_UNAVAILABLE) from exc

    def rollback(self, error: BaseException) -> None:
        """Roll back and end the scope, because the request failed with ``error``."""
        self._rollback(error)

    def release(self) -> None:
        """Commit now and end the scope, before a slow external call."""
        if self._depth:
            # The block still uses the connection that a commit would return to the pool.
            raise RuntimeError("release() cannot be called inside a database block.")
        if self.rolled_back:
            raise RuntimeError("The request transaction was rolled back by an earlier error.")
        try:
            self.commit()
        finally:
            self.released = True
            if self._on_release is not None:
                self._on_release()

    def _rollback(self, exc: BaseException) -> None:
        stack, self._stack, self._conn, self._applied = self._stack, None, None, None
        if stack is not None:
            self.rolled_back = True
            try:
                stack.__exit__(type(exc), exc, exc.__traceback__)
            except psycopg.OperationalError as rollback_error:
                # The ROLLBACK itself failed, so the connection is closed and the
                # server discards the transaction. Report the outage, as an
                # unscoped transaction does.
                raise DependencyUnavailableError(_UNAVAILABLE) from rollback_error


def _require_committable(conn: psycopg.Connection) -> None:
    """Refuse to end a block as a success when its transaction can no longer commit.

    - A statement failed and its error was caught: PostgreSQL would turn
      ``COMMIT`` into ``ROLLBACK`` silently.
    - The connection was lost (the server ended the session, a pooler
      restarted, a deadline closed it) and the error was caught: psycopg
      would leave the block without a ``COMMIT`` and without an error.
      This raises ``OperationalError``, so callers report a 503.
    """
    status = conn.info.transaction_status
    if status == TransactionStatus.INTRANS:
        return
    if status == TransactionStatus.INERROR:
        raise RuntimeError("A statement failed inside this transaction, so it cannot commit.")
    if conn.closed or status == TransactionStatus.UNKNOWN:
        raise psycopg.OperationalError("The database connection was lost inside this transaction; nothing committed.")
    raise RuntimeError("The transaction ended inside the block, so its work cannot be committed here.")


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
