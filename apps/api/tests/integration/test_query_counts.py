"""ARCH-08: key endpoints make a bounded number of database round trips that
does not grow with the amount of data returned (no N+1 queries).

Every round trip a request makes on an open pooled connection is counted: each
statement, including the per-transaction role and context setup, and each
transaction command (BEGIN, COMMIT, ROLLBACK, SAVEPOINT). Opening a new
connection is not counted; the measured requests reuse open ones. Each round
trip waits on the network, which matters once the database is remote (PERF-07
measured about 20 ms per round trip turning a 10 ms list into 700 ms). Setup is
therefore a single statement per transaction, and the per-request bound is tight.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any
from unittest import mock

import psycopg
import pytest

from app.kernel.infrastructure.db import API_ROLE, WORKER_ROLE, Database
from app.kernel.tenancy import Role
from app.modules.billing.domain import SubscriptionStatus
from app.modules.identity.domain import ExternalMembership
from tests.conftest import Harness
from tests.integration.test_billing import event_payload, make_pro, send_webhook, snapshot

pytestmark = pytest.mark.db

# Measured: one request-scoped transaction of BEGIN, setup, context resolution,
# the data queries, and COMMIT; the organization routes need the most (8).
MAX_ROUND_TRIPS_PER_REQUEST = 8


@dataclass
class RoundTrips:
    statements: int = 0
    commands: list[Any] = field(default_factory=list)  # BEGIN, COMMIT, ROLLBACK, SAVEPOINT, as sent

    @property
    def total(self) -> int:
        return self.statements + len(self.commands)


@contextmanager
def counting_round_trips() -> Iterator[RoundTrips]:
    trips = RoundTrips()
    execute, exec_command = psycopg.Cursor.execute, psycopg.Connection._exec_command

    def counted_execute(self: psycopg.Cursor[Any], *args: Any, **kwargs: Any) -> Any:
        trips.statements += 1
        return execute(self, *args, **kwargs)

    # psycopg sends transaction control through this generator, never through a cursor.
    def counted_command(self: psycopg.Connection[Any], command: Any, *args: Any, **kwargs: Any) -> Any:
        trips.commands.append(command)
        return exec_command(self, command, *args, **kwargs)

    with (
        mock.patch.object(psycopg.Cursor, "execute", counted_execute),
        mock.patch.object(psycopg.Connection, "_exec_command", counted_command),
    ):
        yield trips


def round_trips_for(h: Harness, request: Callable[[], Any]) -> int:
    with counting_round_trips() as trips:
        response = request()
    assert response.status_code == 200, response.text
    return trips.total


def create_projects(h: Harness, path: str, headers: dict[str, str], count: int) -> None:
    for i in range(count):
        assert h.client.post(path, json={"name": f"p{i}"}, headers=headers).status_code == 201


@pytest.mark.parametrize("path", ["/api/v1/projects", "/api/v1/orgs/org_q/projects"])
def test_project_list_query_count_is_independent_of_rows(harness: Harness, path: str) -> None:
    harness.directory.set("user_a", [ExternalMembership("org_q", Role.ADMIN)])
    headers = harness.login("user_a")
    create_projects(harness, path, headers, 1)
    small = round_trips_for(harness, lambda: harness.client.get(f"{path}?limit=100", headers=headers))
    create_projects(harness, path, headers, 59)
    large = round_trips_for(harness, lambda: harness.client.get(f"{path}?limit=100", headers=headers))
    assert large == small
    assert 0 < small <= MAX_ROUND_TRIPS_PER_REQUEST
    body = harness.client.get(f"{path}?limit=100", headers=headers).json()
    assert len(body["items"]) == 60


def test_billing_status_query_count_is_independent_of_subscription_history(harness: Harness) -> None:
    customer = make_pro(harness, "user_a")
    headers = harness.login("user_a")
    small = round_trips_for(harness, lambda: harness.client.get("/api/v1/billing/status", headers=headers))

    history = [
        snapshot(customer, SubscriptionStatus.CANCELED, stripe_subscription_id=f"sub_old_{i}") for i in range(40)
    ]
    harness.billing.set_subscriptions(customer, [snapshot(customer, SubscriptionStatus.ACTIVE), *history])
    assert send_webhook(harness, event_payload(customer)).json()["status"] == "processed"
    large = round_trips_for(harness, lambda: harness.client.get("/api/v1/billing/status", headers=headers))
    assert large == small
    assert 0 < small <= MAX_ROUND_TRIPS_PER_REQUEST


def test_organization_entitlement_query_count_is_independent_of_membership_count(harness: Harness) -> None:
    few = [ExternalMembership("org_q", Role.MEMBER)]
    many = few + [ExternalMembership(f"org_{i}", Role.MEMBER) for i in range(50)]
    harness.directory.set("user_a", few)
    headers = harness.login("user_a")
    url = "/api/v1/orgs/org_q/entitlement"
    harness.client.get(url, headers=headers)  # first request mirrors memberships
    small = round_trips_for(harness, lambda: harness.client.get(url, headers=headers))

    harness.directory.set("user_a", many)
    harness.container.identity.resolver.refresh_memberships(
        harness.container.identity.resolver.personal(harness.verifier.verify(headers["Authorization"][7:])).user_id,
        "user_a",
    )
    large = round_trips_for(harness, lambda: harness.client.get(url, headers=headers))
    assert large == small
    assert 0 < small <= MAX_ROUND_TRIPS_PER_REQUEST


def test_transaction_setup_is_one_statement_and_takes_effect(db: str) -> None:
    database = Database(db, api_pool_size=1, worker_pool_size=1, connect_timeout=3, statement_timeout_ms=7000)
    user = "00000000-0000-4000-8000-00000000a11c"
    try:
        with counting_round_trips() as trips, database.transaction(API_ROLE, {"app.user_id": user}) as conn:
            setup = (trips.statements, list(trips.commands))
            row = conn.execute(
                "SELECT current_user, current_setting('statement_timeout'), current_setting('app.user_id')"
            ).fetchone()
        assert setup == (1, [b"BEGIN"])  # one statement sets role, timeout, and context
        assert (trips.statements, trips.commands) == (2, [b"BEGIN", b"COMMIT"])  # BEGIN and COMMIT are round trips too
        assert row == ("app_api", "7s", user)
        # Role and context are transaction-local: the next transaction on the same
        # pooled connection starts clean.
        with database.transaction(WORKER_ROLE, {}) as conn:
            row = conn.execute("SELECT current_user, current_setting('app.user_id', true)").fetchone()
        assert row is not None
        assert row[0] == "app_worker"
        assert row[1] in (None, "")
        with pytest.raises(ValueError, match="unknown database role"), database.transaction("postgres", {}):
            pass
    finally:
        database.close()
