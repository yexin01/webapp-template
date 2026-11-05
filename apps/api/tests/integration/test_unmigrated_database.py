"""A database nothing was migrated into is not ready, and the database check says so (API-06, MIG-04).

`SELECT 1` answers on such a database, so readiness said "ready" while every route failed on a missing table
(found when `pnpm check:setup` passed against a database without schema `app`). Nothing migrates on deploy, so
forgetting the migration is the likeliest first mistake of a deployment.

The runtime roles exist for the whole server once any database on it has been migrated, which the `db` fixture
has done: the database here is empty, not new, so `SET ROLE` works in it and only the tables are missing.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from typing import Any
from urllib.parse import urlparse, urlunparse

import psycopg
import pytest
from psycopg import sql

from app.kernel.infrastructure.db import Database
from app.kernel.infrastructure.store import PgKernelStore
from app.kernel.ports import ConnectionDescription
from tests.conftest import superuser_query

pytestmark = pytest.mark.db

# The tables PgKernelStore itself reads and writes: health, rate limits, the outbox, and job runs.
KERNEL_TABLES = ("job_runs", "outbox", "rate_limit_counters")


@pytest.fixture
def empty_url(db: str) -> Iterator[str]:
    """A database of its own with no schema `app`."""
    name = f"app_unmigrated_{uuid.uuid4().hex[:10]}"
    superuser_query(db, f'CREATE DATABASE "{name}"')
    yield urlunparse(urlparse(db)._replace(path=f"/{name}"))
    superuser_query(
        db,
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()",
        (name,),
    )
    superuser_query(db, f'DROP DATABASE IF EXISTS "{name}"')


def describe_as(url: str, role: str) -> ConnectionDescription:
    with psycopg.connect(url) as conn, conn.transaction():
        conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))
        return PgKernelStore(conn).describe_connection()


def test_the_connection_description_lists_the_kernel_tables_a_database_lacks(db: str, empty_url: str) -> None:
    assert describe_as(db, "app_api").missing_tables == ()
    assert describe_as(empty_url, "app_api").missing_tables == KERNEL_TABLES


def test_a_database_with_one_kernel_table_missing_lists_just_that_one(db: str, empty_url: str) -> None:
    """Every migration applied but the newest kernel one: what a deployment that skipped the last migration has."""
    superuser_query(empty_url, "CREATE SCHEMA app")
    superuser_query(empty_url, "CREATE TABLE app.outbox (id int)")
    superuser_query(empty_url, "CREATE TABLE app.rate_limit_counters (id int)")
    assert describe_as(empty_url, "app_api").missing_tables == ("job_runs",)


def test_a_view_or_a_table_of_another_schema_does_not_stand_in_for_a_kernel_table(db: str, empty_url: str) -> None:
    superuser_query(empty_url, "CREATE TABLE public.outbox (id int)")
    superuser_query(empty_url, "CREATE SCHEMA app")
    superuser_query(empty_url, "CREATE VIEW app.job_runs AS SELECT 1 AS id")
    assert describe_as(empty_url, "app_api").missing_tables == KERNEL_TABLES


def database_for(url: str) -> Database:
    return Database(url, api_pool_size=1, worker_pool_size=1, connect_timeout=2, statement_timeout_ms=2000)


def test_readiness_fails_on_a_database_nothing_was_migrated_into(
    make_harness: Callable[..., Any], empty_url: str
) -> None:
    unmigrated = make_harness(database=database_for(empty_url))
    assert unmigrated.client.get("/api/v1/health").status_code == 200  # liveness never looks at the database
    ready = unmigrated.client.get("/api/v1/ready")
    assert ready.status_code == 503
    assert ready.json() == {"status": "not_ready", "checks": {"database": "unavailable"}}
    assert empty_url not in ready.text  # never reveals configuration


def test_readiness_passes_on_the_migrated_database_beside_it(make_harness: Callable[..., Any]) -> None:
    """The control for the test above: the same check on the migrated test database."""
    ready = make_harness().client.get("/api/v1/ready")
    assert (ready.status_code, ready.json()) == (200, {"status": "ready", "checks": {"database": "ok"}})
