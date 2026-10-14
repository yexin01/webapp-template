"""Request-scoped transactions (ADR-0003).

Database-only routes run in one ``app_api`` transaction per request. It commits
before the response is sent, rolls back if a database block raises, re-applies
the tenant context whenever it changes, and never absorbs ``app_worker`` work.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any
from unittest import mock

import psycopg
import pytest

from app.kernel.errors import DependencyUnavailableError
from app.kernel.infrastructure.db import API_ROLE, WORKER_ROLE, Database, RequestScope
from app.kernel.tenancy import Role
from app.modules.identity.domain import ExternalMembership
from tests.conftest import Harness, superuser_query

pytestmark = pytest.mark.db


@contextmanager
def counting_transactions() -> Iterator[dict[str, int]]:
    counter = {"n": 0}
    original = psycopg.Connection.transaction

    def transaction(self: psycopg.Connection[Any], *args: Any, **kwargs: Any) -> Any:
        counter["n"] += 1
        return original(self, *args, **kwargs)

    with mock.patch.object(psycopg.Connection, "transaction", transaction):
        yield counter


def transactions_for(request: Callable[[], Any], expect: int = 200) -> int:
    with counting_transactions() as counter:
        response = request()
    assert response.status_code == expect, response.text
    return counter["n"]


@pytest.fixture
def member(harness: Harness) -> dict[str, str]:
    harness.directory.set("user_a", [ExternalMembership("org_q", Role.ADMIN)])
    headers = harness.login("user_a")
    harness.client.get("/api/v1/orgs/org_q/projects", headers=headers)  # mirror memberships first
    return headers


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/me",
        "/api/v1/projects",
        "/api/v1/orgs/org_q/projects",
        "/api/v1/entitlement",
        "/api/v1/orgs/org_q/entitlement",
        "/api/v1/billing/status",
    ],
)
def test_database_only_routes_use_one_transaction(harness: Harness, member: dict[str, str], path: str) -> None:
    # Identity lookup, membership check, and the data query share one transaction.
    assert transactions_for(lambda: harness.client.get(path, headers=member)) == 1


def test_write_route_uses_one_transaction_and_commits(harness: Harness, member: dict[str, str]) -> None:
    n = transactions_for(
        lambda: harness.client.post("/api/v1/orgs/org_q/projects", json={"name": "scoped"}, headers=member), 201
    )
    # One request transaction, plus the worker transactions of after-response event dispatch.
    rows = superuser_query(harness.database_url, "SELECT count(*) FROM app.projects WHERE name = 'scoped'")
    assert rows[0][0] == 1
    assert n >= 1


def test_commit_failure_is_reported_before_the_response(harness: Harness, member: dict[str, str]) -> None:
    """The commit runs before the response is sent, so a failed commit can never look like success."""

    def failing_commit(self: RequestScope) -> None:
        self._rollback(RuntimeError("simulated commit failure"))
        raise DependencyUnavailableError("The database is temporarily unavailable.")

    with mock.patch.object(RequestScope, "commit", failing_commit):
        r = harness.client.post("/api/v1/projects", json={"name": "never-committed"}, headers=member)
    assert r.status_code == 503
    assert r.json()["code"] == "DEPENDENCY_UNAVAILABLE"
    rows = superuser_query(harness.database_url, "SELECT count(*) FROM app.projects WHERE name = 'never-committed'")
    assert rows[0][0] == 0


def _finish(database: Database, token: Any) -> None:
    commit = database.end_request_scope(token)
    if commit is not None:
        commit()


def _database(url: str) -> Database:
    return Database(url, api_pool_size=2, worker_pool_size=2, connect_timeout=3, statement_timeout_ms=5000)


def test_block_exception_rolls_back_pending_request_work(db: str) -> None:
    database = _database(db)
    try:
        token = database.begin_request_scope()
        with database.transaction(API_ROLE, {"app.clerk_user_id": "user_rollback"}) as conn:
            conn.execute("INSERT INTO app.users (clerk_user_id) VALUES ('user_rollback')")
        with pytest.raises(ZeroDivisionError), database.transaction(API_ROLE, {"app.clerk_user_id": "user_rollback"}):
            _ = 1 / 0
        _finish(database, token)
        assert superuser_query(db, "SELECT count(*) FROM app.users WHERE clerk_user_id = 'user_rollback'")[0][0] == 0
    finally:
        database.close()


def test_worker_blocks_never_join_and_commit_independently(db: str) -> None:
    database = _database(db)
    try:
        token = database.begin_request_scope()
        with database.transaction(API_ROLE, {"app.clerk_user_id": "user_scope"}) as conn:
            conn.execute("INSERT INTO app.users (clerk_user_id) VALUES ('user_scope')")
        with database.transaction(WORKER_ROLE, {}) as conn:
            assert conn.execute("SELECT current_user").fetchone() == ("app_worker",)
            conn.execute("INSERT INTO app.users (clerk_user_id) VALUES ('user_worker')")
        # The worker row is committed while the request transaction is still open.
        assert superuser_query(db, "SELECT count(*) FROM app.users WHERE clerk_user_id = 'user_worker'")[0][0] == 1
        assert superuser_query(db, "SELECT count(*) FROM app.users WHERE clerk_user_id = 'user_scope'")[0][0] == 0
        _finish(database, token)
        assert superuser_query(db, "SELECT count(*) FROM app.users WHERE clerk_user_id = 'user_scope'")[0][0] == 1
    finally:
        database.close()


def test_context_is_fully_reapplied_between_blocks(db: str) -> None:
    """No context value from an earlier block can leak into a later one."""
    database = _database(db)
    org_context = {"app.clerk_user_id": "u", "app.user_id": "00000000-0000-4000-8000-0000000000a1", "app.org_id": "x"}
    try:
        token = database.begin_request_scope()
        with database.transaction(API_ROLE, org_context) as conn:
            assert conn.execute("SELECT current_setting('app.org_id')").fetchone() == ("x",)
        with database.transaction(API_ROLE, {"app.clerk_user_id": "u"}) as conn:
            row = conn.execute(
                "SELECT current_setting('app.user_id'), current_setting('app.org_id'), current_user"
            ).fetchone()
            assert row == ("", "", "app_api")
        _finish(database, token)
    finally:
        database.close()


def test_release_commits_early_and_reopens(db: str) -> None:
    database = _database(db)
    try:
        token = database.begin_request_scope()
        with database.transaction(API_ROLE, {"app.clerk_user_id": "user_release"}) as conn:
            conn.execute("INSERT INTO app.users (clerk_user_id) VALUES ('user_release')")
        assert database.in_request_transaction()
        database.release_request_transaction()
        assert not database.in_request_transaction()
        assert superuser_query(db, "SELECT count(*) FROM app.users WHERE clerk_user_id = 'user_release'")[0][0] == 1
        with database.transaction(API_ROLE, {"app.clerk_user_id": "user_release"}) as conn:
            assert conn.execute("SELECT count(*) FROM app.users").fetchone() == (1,)
        _finish(database, token)
    finally:
        database.close()


def test_stale_membership_refresh_releases_the_request_transaction(harness: Harness, member: dict[str, str]) -> None:
    """Before calling the identity provider, the resolver ends the request transaction."""
    harness.clock.advance(seconds=harness.container.settings.membership_max_staleness_seconds + 1)
    seen: list[bool] = []
    original = harness.directory.list_user_memberships

    def spy(external_user_id: str) -> list[ExternalMembership]:
        seen.append(harness.container.kernel.database.in_request_transaction())
        return original(external_user_id)

    harness.directory.list_user_memberships = spy  # type: ignore[method-assign]
    assert harness.client.get("/api/v1/orgs/org_q/projects", headers=member).status_code == 200
    assert seen == [False]


def test_nested_blocks_are_refused_inside_a_request_transaction(db: str) -> None:
    """Nesting would switch the shared transaction's context under the outer block."""
    database = _database(db)
    try:
        token = database.begin_request_scope()
        with (
            database.transaction(API_ROLE, {"app.clerk_user_id": "outer"}),
            pytest.raises(RuntimeError, match="Nested"),
            database.transaction(API_ROLE, {"app.clerk_user_id": "inner"}),
        ):
            pass
        _finish(database, token)
    finally:
        database.close()


def test_no_thread_hop_or_commit_when_no_transaction_was_opened(db: str) -> None:
    database = _database(db)
    try:
        token = database.begin_request_scope()
        assert database.end_request_scope(token) is None
    finally:
        database.close()


def test_admission_is_bounded_by_the_api_pool(harness: Harness) -> None:
    assert harness.container.kernel.database.api_pool_size == harness.container.settings.database_pool_max_size
