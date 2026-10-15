"""DB-02: the plan of a tenant-scoped list page, as the request role runs it.

Keyset pagination costs one page only if PostgreSQL walks the page index in
order and stops at the limit. Row-level security policies take part in every
plan, so a plan test seeds equal-size tenants whose rows are interleaved on
disk, then reads the executed plan of the adapter's own list statement as
``app_api``, with the context set by ``PgTransactions.as_tenant``.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any, Protocol, cast

import psycopg
from psycopg import sql

from app.kernel.infrastructure.db import PgTransactions
from app.kernel.pagination import DEFAULT_PAGE_SIZE, PageCursor
from app.kernel.tenancy import ContextKind, Role, TenantContext

TENANTS = 8  # organizations, and as many users with personal rows
ROWS = 1_000  # per tenant: ANALYZE then reads every row (default statistics target), so the plans are deterministic
USERS = [uuid.UUID(int=0xD0000 + n) for n in range(TENANTS)]
ORGS = [uuid.UUID(int=0xE0000 + n) for n in range(TENANTS)]
# User n is a member of organization n. These are the contexts of user 4.
ORG_CONTEXT = TenantContext(USERS[3], "user_plan_4", ContextKind.ORGANIZATION, ORGS[3], "org_plan_4", Role.MEMBER)
PERSONAL_CONTEXT = TenantContext(USERS[3], "user_plan_4", ContextKind.PERSONAL)


class Row(Protocol):
    @property
    def id(self) -> uuid.UUID: ...

    @property
    def created_at(self) -> datetime: ...


class PageReader(Protocol):
    """A tenant-scoped repository's ``list``, as in the projects module."""

    def list(self, ctx: TenantContext, limit: int, after: PageCursor | None) -> Sequence[Row]: ...


Repository = Callable[[psycopg.Connection], PageReader]


def seed_tenants(url: str, table: str) -> None:
    """USERS and ORGS, user n a member of organization n, and ROWS rows of ``app.<table>`` for each tenant.

    Each organization, and each user's personal space, gets ROWS rows. The rows
    are interleaved, so every heap page holds many tenants. Written as a
    superuser, then analyzed. Deterministic (DB-09).
    """
    target = sql.Identifier("app", table)
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO app.users (id, clerk_user_id) "
            "SELECT id, 'user_plan_' || n FROM unnest(%s::uuid[]) WITH ORDINALITY AS t(id, n)",
            (USERS,),
        )
        conn.execute(
            "INSERT INTO app.organizations (id, clerk_org_id) "
            "SELECT id, 'org_plan_' || n FROM unnest(%s::uuid[]) WITH ORDINALITY AS t(id, n)",
            (ORGS,),
        )
        conn.execute(
            "INSERT INTO app.organization_memberships (org_id, user_id, role) "
            "SELECT o, u, 'member' FROM unnest(%s::uuid[], %s::uuid[]) AS t(o, u)",
            (ORGS, USERS),
        )
        conn.execute(
            sql.SQL(
                """
                INSERT INTO {} (id, org_id, owner_user_id, created_by, name, created_at)
                SELECT md5(g || '/' || t.n || '/' || k.personal)::uuid,
                       CASE WHEN k.personal THEN NULL ELSE t.o END, CASE WHEN k.personal THEN t.u END, t.u,
                       'row ' || g, timestamptz '2026-01-01' + make_interval(secs => g)
                FROM generate_series(1, %s) AS g,
                     unnest(%s::uuid[], %s::uuid[]) WITH ORDINALITY AS t(o, u, n),
                     (VALUES (false), (true)) AS k(personal)
                ORDER BY g, t.n, k.personal
                """
            ).format(target),
            (ROWS, ORGS, USERS),
        )
        conn.execute("ANALYZE app.organization_memberships")
        conn.execute(sql.SQL("ANALYZE {}").format(target))


def halfway_cursor(
    transactions: PgTransactions[psycopg.Connection], repository: Repository, ctx: TenantContext
) -> PageCursor:
    """The cursor a client holds after reading half of the tenant's rows."""
    with transactions.as_tenant(ctx) as t:
        last = repository(t.repo).list(ctx, ROWS // 2, None)[-1]
    return PageCursor(last.created_at, last.id)


def page_plan(
    transactions: PgTransactions[psycopg.Connection],
    repository: Repository,
    ctx: TenantContext,
    after: PageCursor | None,
) -> list[dict[str, Any]]:
    """The main path of the executed plan of one default-size page, root first, leaving out subplans.

    The statement is the one the repository sends, recorded through a stand-in
    connection; the subplans left out are the policy's membership check.
    """
    recorder = _StatementRecorder()
    repository(cast(psycopg.Connection, recorder)).list(ctx, DEFAULT_PAGE_SIZE, after)
    ((query, params),) = recorder.statements
    with transactions.as_tenant(ctx) as t:
        explained = t.repo.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + query, params).fetchone()
    assert explained is not None
    return _main_path(explained[0][0]["Plan"])


def _main_path(plan: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = [plan]
    for child in plan.get("Plans", []):
        if child.get("Parent Relationship") not in ("SubPlan", "InitPlan"):
            nodes += _main_path(child)
    return nodes


class _StatementRecorder:
    """Stands in for a repository's connection and records the statements it sends."""

    def __init__(self) -> None:
        self.statements: list[tuple[str, list[Any]]] = []

    def cursor(self, **_: Any) -> _StatementRecorder:
        return self

    def __enter__(self) -> _StatementRecorder:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, query: str, params: Sequence[Any]) -> None:
        self.statements.append((query, list(params)))

    def fetchall(self) -> list[Any]:
        return []
