"""ARCH-08: key endpoints issue a bounded number of SQL statements that does
not grow with the amount of data returned (no N+1 queries).

Every statement sent through psycopg is counted, including the per-transaction
role and context setup. Each statement is a network round trip, which matters
once the database is remote (PERF-07 measured about 20 ms per round trip
turning a 10 ms list into 700 ms). Setup is therefore a single statement per
transaction, and the per-request bound is tight.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any
from unittest import mock

import psycopg
import pytest

from app.domain.entitlements import SubscriptionStatus
from app.domain.tenancy import ExternalMembership, Role
from app.infrastructure.db import API_ROLE, WORKER_ROLE, Database
from tests.conftest import Harness
from tests.integration.test_billing import event_payload, make_pro, send_webhook, snapshot

pytestmark = pytest.mark.db

# Context resolution plus the data query: two or three transactions of setup + query.
MAX_STATEMENTS_PER_REQUEST = 8


@contextmanager
def counting_statements() -> Iterator[dict[str, int]]:
    counter = {"n": 0}
    original = psycopg.Cursor.execute

    def execute(self: psycopg.Cursor[Any], *args: Any, **kwargs: Any) -> Any:
        counter["n"] += 1
        return original(self, *args, **kwargs)

    with mock.patch.object(psycopg.Cursor, "execute", execute):
        yield counter


def statements_for(h: Harness, request: Callable[[], Any]) -> int:
    with counting_statements() as counter:
        response = request()
    assert response.status_code == 200, response.text
    return counter["n"]


def create_projects(h: Harness, path: str, headers: dict[str, str], count: int) -> None:
    for i in range(count):
        assert h.client.post(path, json={"name": f"p{i}"}, headers=headers).status_code == 201


@pytest.mark.parametrize("path", ["/api/v1/projects", "/api/v1/orgs/org_q/projects"])
def test_project_list_query_count_is_independent_of_rows(harness: Harness, path: str) -> None:
    harness.directory.set("user_a", [ExternalMembership("org_q", Role.ADMIN)])
    headers = harness.login("user_a")
    create_projects(harness, path, headers, 1)
    small = statements_for(harness, lambda: harness.client.get(f"{path}?limit=100", headers=headers))
    create_projects(harness, path, headers, 59)
    large = statements_for(harness, lambda: harness.client.get(f"{path}?limit=100", headers=headers))
    assert large == small
    assert 0 < small <= MAX_STATEMENTS_PER_REQUEST
    body = harness.client.get(f"{path}?limit=100", headers=headers).json()
    assert len(body["items"]) == 60


def test_billing_status_query_count_is_independent_of_subscription_history(harness: Harness) -> None:
    customer = make_pro(harness, "user_a")
    headers = harness.login("user_a")
    small = statements_for(harness, lambda: harness.client.get("/api/v1/billing/status", headers=headers))

    history = [
        snapshot(customer, SubscriptionStatus.CANCELED, stripe_subscription_id=f"sub_old_{i}") for i in range(40)
    ]
    harness.billing.set_subscriptions(customer, [snapshot(customer, SubscriptionStatus.ACTIVE), *history])
    assert send_webhook(harness, event_payload(customer)).json()["status"] == "processed"
    large = statements_for(harness, lambda: harness.client.get("/api/v1/billing/status", headers=headers))
    assert large == small
    assert 0 < small <= MAX_STATEMENTS_PER_REQUEST


def test_organization_entitlement_query_count_is_independent_of_membership_count(harness: Harness) -> None:
    few = [ExternalMembership("org_q", Role.MEMBER)]
    many = few + [ExternalMembership(f"org_{i}", Role.MEMBER) for i in range(50)]
    harness.directory.set("user_a", few)
    headers = harness.login("user_a")
    url = "/api/v1/orgs/org_q/entitlement"
    harness.client.get(url, headers=headers)  # first request mirrors memberships
    small = statements_for(harness, lambda: harness.client.get(url, headers=headers))

    harness.directory.set("user_a", many)
    harness.container.resolver.refresh_memberships(
        harness.container.resolver.personal(harness.verifier.verify(headers["Authorization"][7:])).user_id, "user_a"
    )
    large = statements_for(harness, lambda: harness.client.get(url, headers=headers))
    assert large == small
    assert 0 < small <= MAX_STATEMENTS_PER_REQUEST


def test_transaction_setup_is_one_statement_and_takes_effect(db: str) -> None:
    database = Database(db, max_size=1, connect_timeout=3, statement_timeout_ms=7000)
    user = "00000000-0000-4000-8000-00000000a11c"
    try:
        with counting_statements() as counter, database.transaction(API_ROLE, {"app.user_id": user}) as conn:
            setup = counter["n"]
            row = conn.execute(
                "SELECT current_user, current_setting('statement_timeout'), current_setting('app.user_id')"
            ).fetchone()
        assert setup == 1
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
