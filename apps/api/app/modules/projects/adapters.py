"""PostgreSQL adapter for projects.

Row-level security filters every statement by the transaction's role and
context; the explicit tenant filters below are a second, independent guard.
All SQL uses bound parameters only.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from app.kernel.pagination import PageCursor
from app.kernel.tenancy import ContextKind, TenantContext
from app.modules.projects.domain import Project

_COLUMNS = "id, name, org_id, created_by, created_at"


def _project(row: dict[str, Any]) -> Project:
    return Project(row["id"], row["name"], row["org_id"], row["created_by"], row["created_at"])


def _tenant_filter(ctx: TenantContext) -> tuple[str, tuple[object, ...]]:
    if ctx.kind is ContextKind.ORGANIZATION:
        return "org_id = %s", (ctx.org_id,)
    return "owner_user_id = %s AND org_id IS NULL", (ctx.user_id,)


class PgProjectRepository:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def list(self, ctx: TenantContext, limit: int, after: PageCursor | None) -> list[Project]:
        tenant_sql, params = _tenant_filter(ctx)
        cursor_sql = ""
        values: list[object] = list(params)
        if after is not None:
            cursor_sql = "AND (created_at, id) < (%s, %s)"
            values += [after.created_at, after.id]
        values.append(limit)
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"""
                SELECT {_COLUMNS} FROM app.projects
                WHERE {tenant_sql} {cursor_sql}
                ORDER BY created_at DESC, id DESC
                LIMIT %s
                """,  # noqa: S608 - fragments are constants; values are bound parameters
                values,
            )
            return [_project(r) for r in cur.fetchall()]

    def create(self, ctx: TenantContext, name: str) -> Project:
        is_org = ctx.kind is ContextKind.ORGANIZATION
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"""
                INSERT INTO app.projects (org_id, owner_user_id, created_by, name)
                VALUES (%s, %s, %s, %s)
                RETURNING {_COLUMNS}
                """,  # noqa: S608 - column list is a constant
                (ctx.org_id if is_org else None, None if is_org else ctx.user_id, ctx.user_id, name),
            )
            row = cur.fetchone()
        if row is None:  # pragma: no cover
            raise RuntimeError("insert returned no row")
        return _project(row)

    def delete(self, ctx: TenantContext, project_id: UUID) -> bool:
        tenant_sql, params = _tenant_filter(ctx)
        with self._conn.cursor() as cur:
            cur.execute(
                f"DELETE FROM app.projects WHERE id = %s AND {tenant_sql}",  # noqa: S608
                (project_id, *params),
            )
            return cur.rowcount == 1
