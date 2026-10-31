"""The database enforces its own integrity: CHECK, UNIQUE, FOREIGN KEY and NOT NULL constraints (DB-01).

Each case inserts one violating row as the migration role, so row-level security is out of the way, into a
transaction that is rolled back. It expects the error class of its family and the constraint (or, for NOT NULL, the
column) the migration declares: a case cannot pass because another constraint fired or because the setup was wrong.
Dropping or loosening a constraint in a later migration fails here. Every case rolls back, so the cases use the
session's database directly and skip the per-test cleanup.
"""

from __future__ import annotations

import psycopg
import pytest
from psycopg import errors

pytestmark = pytest.mark.db

USER = "00000000-0000-4000-8000-0000000000c1"
OTHER = "00000000-0000-4000-8000-0000000000c2"
ORG = "00000000-0000-4000-8000-0000000c0001"
PROJECT = "00000000-0000-4000-8000-000000000001"
EVENT = "00000000-0000-4000-8000-0000000e0001"  # an outbox event
GONE = "00000000-0000-4000-8000-0000000fffff"  # a row that does not exist
SUBSCRIPTION = "INSERT INTO app.subscriptions (stripe_subscription_id, user_id, stripe_customer_id, status, plan_key)"

# Valid rows for the violating statements to point at: two users, one organization with a membership of the first
# user and a project created by them, their billing customer and subscription, a checkout session, and the ledgers.
PARENTS = f"""
INSERT INTO app.users (id, clerk_user_id) VALUES ('{USER}', 'user_c1'), ('{OTHER}', 'user_c2');
INSERT INTO app.organizations (id, clerk_org_id) VALUES ('{ORG}', 'org_c1');
INSERT INTO app.organization_memberships (org_id, user_id, role) VALUES ('{ORG}', '{USER}', 'member');
INSERT INTO app.projects (id, org_id, created_by, name) VALUES ('{PROJECT}', '{ORG}', '{USER}', 'first');
INSERT INTO app.billing_customers (user_id, stripe_customer_id) VALUES ('{USER}', 'cus_c1');
{SUBSCRIPTION} VALUES ('sub_c1', '{USER}', 'cus_c1', 'active', 'pro_monthly');
INSERT INTO app.billing_checkout_sessions (user_id, stripe_session_id, idempotency_key) VALUES ('{USER}', 'cs_c1', 'k');
INSERT INTO app.billing_events (stripe_event_id, event_type, event_created_at, status) VALUES ('evt_c1', 'x', now(), 'received');
INSERT INTO app.identity_events (event_id, event_type, status) VALUES ('evt_c1', 'x', 'received');
INSERT INTO app.outbox (id, event_type) VALUES ('{EVENT}', 'projects.created');
"""


def run(db: str, statement: str) -> None:
    with psycopg.connect(db) as conn, conn.transaction(force_rollback=True):
        conn.execute(PARENTS)  # type: ignore[call-overload]
        conn.execute(statement)  # type: ignore[call-overload]


def violation(db: str, statement: str, error: type[psycopg.Error]) -> psycopg.errors.Diagnostic:
    with pytest.raises(error) as raised:
        run(db, statement)
    return raised.value.diag


def sub(identifier: str = "'sub_c2'", status: str = "'active'", plan: str = "'pro_monthly'") -> str:
    return f"{SUBSCRIPTION} VALUES ({identifier}, '{USER}', 'cus_c1', {status}, {plan})"


def event(table: str, status: str) -> str:
    if table == "billing_events":
        return f"INSERT INTO app.billing_events (stripe_event_id, event_type, event_created_at, status) VALUES ('evt_c2', 'x', now(), {status})"
    return f"INSERT INTO app.identity_events (event_id, event_type, status) VALUES ('evt_c2', 'x', {status})"


def project(org: str = "NULL", owner: str = "NULL", creator: str = f"'{USER}'", name: str = "'second'") -> str:
    return (
        f"INSERT INTO app.projects (org_id, owner_user_id, created_by, name) VALUES ({org}, {owner}, {creator}, {name})"
    )


def outbox(event_type: str, status: str = "'pending'") -> str:
    return f"INSERT INTO app.outbox (id, event_type, status) VALUES (gen_random_uuid(), {event_type}, {status})"


def session(identifier: str = "'cs_c2'", key: str = "'k'") -> str:
    return f"INSERT INTO app.billing_checkout_sessions (user_id, stripe_session_id, idempotency_key) VALUES ('{OTHER}', {identifier}, {key})"


CHECKS = [
    pytest.param("INSERT INTO app.users (clerk_user_id) VALUES ('')", "users_clerk_user_id_check", id="user id empty"),
    pytest.param(
        "INSERT INTO app.users (clerk_user_id) VALUES (repeat('u', 256))",
        "users_clerk_user_id_check",
        id="user id too long",
    ),
    pytest.param(
        "INSERT INTO app.organizations (clerk_org_id) VALUES ('')",
        "organizations_clerk_org_id_check",
        id="organization id empty",
    ),
    pytest.param(
        "UPDATE app.organization_memberships SET role = 'owner'",
        "organization_memberships_role_check",
        id="membership role",
    ),
    pytest.param(project(owner=f"'{USER}'", name="'   '"), "projects_name_check", id="project name blank"),
    pytest.param(
        project(owner=f"'{USER}'", name="repeat('n', 121)"), "projects_name_check", id="project name too long"
    ),
    pytest.param(project(), "projects_single_tenant", id="project with no tenant"),
    pytest.param(project(org=f"'{ORG}'", owner=f"'{USER}'"), "projects_single_tenant", id="project with two tenants"),
    pytest.param(
        f"INSERT INTO app.billing_customers (user_id, stripe_customer_id) VALUES ('{OTHER}', 'bad_1')",
        "billing_customers_stripe_customer_id_check",
        id="customer id prefix",
    ),
    pytest.param(sub("'bad_1'"), "subscriptions_stripe_subscription_id_check", id="subscription id prefix"),
    pytest.param(sub(status="'bogus'"), "subscriptions_status_check", id="subscription status"),
    pytest.param(sub(plan="'enterprise'"), "subscriptions_plan_key_check", id="subscription plan"),
    pytest.param(event("billing_events", "'bogus'"), "billing_events_status_check", id="billing event status"),
    pytest.param(event("identity_events", "'bogus'"), "identity_events_status_check", id="identity event status"),
    pytest.param(
        "INSERT INTO app.audit_log (action, outcome) VALUES ('x', 'maybe')",
        "audit_log_outcome_check",
        id="audit outcome",
    ),
    pytest.param(outbox("'nodot'"), "outbox_event_type_check", id="event type without a dot"),
    pytest.param(outbox("'Upper.case'"), "outbox_event_type_check", id="event type in capitals"),
    pytest.param(outbox("'a.b.c'"), "outbox_event_type_check", id="event type with two dots"),
    pytest.param(outbox("'.leading'"), "outbox_event_type_check", id="event type starting with a dot"),
    pytest.param(outbox("'trailing.'"), "outbox_event_type_check", id="event type ending with a dot"),
    pytest.param(outbox("'a.b'", "'bogus'"), "outbox_status_check", id="outbox status"),
    pytest.param(
        session("'bad'"), "billing_checkout_sessions_stripe_session_id_check", id="checkout session id prefix"
    ),
    pytest.param(session(key="''"), "billing_checkout_sessions_idempotency_key_check", id="idempotency key empty"),
    pytest.param(
        session(key="repeat('k', 201)"),
        "billing_checkout_sessions_idempotency_key_check",
        id="idempotency key too long",
    ),
]

UNIQUES = [
    pytest.param("INSERT INTO app.users (clerk_user_id) VALUES ('user_c1')", "users_clerk_user_id_key", id="user"),
    pytest.param(
        f"INSERT INTO app.users (id, clerk_user_id) VALUES ('{USER}', 'user_new')", "users_pkey", id="user row id"
    ),
    pytest.param(
        "INSERT INTO app.organizations (clerk_org_id) VALUES ('org_c1')",
        "organizations_clerk_org_id_key",
        id="organization",
    ),
    pytest.param(
        f"INSERT INTO app.projects (id, owner_user_id, created_by, name) VALUES ('{PROJECT}', '{USER}', '{USER}', 'again')",
        "projects_pkey",
        id="project row id",
    ),
    pytest.param(
        f"INSERT INTO app.organization_memberships (org_id, user_id, role) VALUES ('{ORG}', '{USER}', 'admin')",
        "organization_memberships_pkey",
        id="membership",
    ),
    pytest.param(
        f"INSERT INTO app.billing_customers (user_id, stripe_customer_id) VALUES ('{OTHER}', 'cus_c1')",
        "billing_customers_stripe_customer_id_key",
        id="Stripe customer",
    ),
    pytest.param(
        f"INSERT INTO app.billing_customers (user_id, stripe_customer_id) VALUES ('{USER}', 'cus_c2')",
        "billing_customers_pkey",
        id="one customer per user",
    ),
    pytest.param(sub("'sub_c1'"), "subscriptions_pkey", id="subscription"),
    pytest.param(session().replace(OTHER, USER), "billing_checkout_sessions_pkey", id="one open checkout per user"),
    pytest.param(
        f"INSERT INTO app.outbox (id, event_type) VALUES ('{EVENT}', 'a.b')", "outbox_pkey", id="outbox event"
    ),
    pytest.param(
        "INSERT INTO app.billing_events (stripe_event_id, event_type, event_created_at, status) "
        "VALUES ('evt_c1', 'x', now(), 'received')",
        "billing_events_pkey",
        id="billing event",
    ),
    pytest.param(
        "INSERT INTO app.identity_events (event_id, event_type, status) VALUES ('evt_c1', 'x', 'received')",
        "identity_events_pkey",
        id="identity event",
    ),
]

FOREIGN_KEYS = [
    pytest.param(
        f"INSERT INTO app.organization_memberships (org_id, user_id, role) VALUES ('{GONE}', '{OTHER}', 'member')",
        "organization_memberships_org_id_fkey",
        id="membership of a missing organization",
    ),
    pytest.param(
        f"INSERT INTO app.organization_memberships (org_id, user_id, role) VALUES ('{ORG}', '{GONE}', 'member')",
        "organization_memberships_user_id_fkey",
        id="membership of a missing user",
    ),
    pytest.param(project(org=f"'{GONE}'"), "projects_org_id_fkey", id="project in a missing organization"),
    pytest.param(project(owner=f"'{GONE}'"), "projects_owner_user_id_fkey", id="project of a missing owner"),
    pytest.param(
        project(owner=f"'{USER}'", creator=f"'{GONE}'"), "projects_created_by_fkey", id="project of a missing creator"
    ),
    pytest.param(
        f"INSERT INTO app.billing_customers (user_id, stripe_customer_id) VALUES ('{GONE}', 'cus_c2')",
        "billing_customers_user_id_fkey",
        id="customer of a missing user",
    ),
    pytest.param(
        f"{SUBSCRIPTION} VALUES ('sub_c2', '{GONE}', 'cus_c1', 'active', 'pro_monthly')",
        "subscriptions_user_id_fkey",
        id="subscription of a missing user",
    ),
    pytest.param(
        f"{SUBSCRIPTION} VALUES ('sub_c2', '{USER}', 'cus_missing', 'active', 'pro_monthly')",
        "subscriptions_stripe_customer_id_fkey",
        id="subscription of a missing customer",
    ),
    pytest.param(
        session().replace(OTHER, GONE), "billing_checkout_sessions_user_id_fkey", id="checkout of a missing user"
    ),
    # ON DELETE RESTRICT: what other rows still point at is never deleted from under them.
    pytest.param(
        f"DELETE FROM app.organizations WHERE id = '{ORG}'",
        "projects_org_id_fkey",
        id="delete an organization with projects",
    ),
    pytest.param(
        f"DELETE FROM app.billing_customers WHERE user_id = '{USER}'",
        "subscriptions_stripe_customer_id_fkey",
        id="delete a customer with subscriptions",
    ),
]

# A valid row for each table, from which one column at a time is set to NULL. Primary key columns are left out (they
# are covered with the UNIQUE cases); every other required column is listed, defaulted ones included.
BASE_ROWS = {
    "users": {"clerk_user_id": "'user_n'"},
    "organizations": {"clerk_org_id": "'org_n'"},
    "organization_memberships": {"org_id": f"'{ORG}'", "user_id": f"'{OTHER}'", "role": "'member'"},
    "projects": {"owner_user_id": f"'{USER}'", "created_by": f"'{USER}'", "name": "'n'"},
    "billing_customers": {"user_id": f"'{OTHER}'", "stripe_customer_id": "'cus_n'"},
    "subscriptions": {
        "stripe_subscription_id": "'sub_n'",
        "user_id": f"'{USER}'",
        "stripe_customer_id": "'cus_c1'",
        "status": "'active'",
        "plan_key": "'pro_monthly'",
    },
    "billing_events": {
        "stripe_event_id": "'evt_n'",
        "event_type": "'x'",
        "event_created_at": "now()",
        "status": "'received'",
    },
    "identity_events": {"event_id": "'evt_n'", "event_type": "'x'", "status": "'received'"},
    "audit_log": {"action": "'x'", "outcome": "'allowed'"},
    "outbox": {"id": "gen_random_uuid()", "event_type": "'a.b'"},
    "billing_checkout_sessions": {
        "user_id": f"'{OTHER}'",
        "stripe_session_id": "'cs_n'",
        "idempotency_key": "'k'",
    },
    "rate_limit_counters": {"bucket_key": "'b'", "window_start": "now()"},
    "job_runs": {"job_name": "'j'", "started_at": "now()", "lease_until": "now()"},
}
REQUIRED = {
    "users": ["clerk_user_id", "created_at"],
    "organizations": ["clerk_org_id", "created_at"],
    "organization_memberships": ["role", "verified_at"],
    "projects": ["name", "created_by", "created_at"],
    "billing_customers": ["stripe_customer_id", "created_at"],
    "subscriptions": [
        "user_id",
        "stripe_customer_id",
        "status",
        "plan_key",
        "cancel_at_period_end",
        "synced_at",
    ],
    "billing_events": ["event_type", "event_created_at", "received_at", "status", "attempts"],
    "identity_events": ["event_type", "received_at", "status", "attempts"],
    "audit_log": ["action", "outcome", "occurred_at"],
    "outbox": ["event_type", "payload", "occurred_at", "available_at", "status", "attempts"],
    "billing_checkout_sessions": ["stripe_session_id", "idempotency_key", "created_at"],
    "rate_limit_counters": ["hits"],
    "job_runs": ["started_at", "lease_until"],
}
NOT_NULLS = [
    pytest.param(table, column, id=f"{table}.{column}") for table, columns in REQUIRED.items() for column in columns
]


def insert(table: str, row: dict[str, str]) -> str:
    return f"INSERT INTO app.{table} ({', '.join(row)}) VALUES ({', '.join(row.values())})"


@pytest.mark.parametrize(("statement", "constraint"), CHECKS)
def test_a_check_constraint_refuses_the_row(database_url: str, statement: str, constraint: str) -> None:
    assert violation(database_url, statement, errors.CheckViolation).constraint_name == constraint


@pytest.mark.parametrize(("statement", "constraint"), UNIQUES)
def test_a_unique_constraint_refuses_the_duplicate(database_url: str, statement: str, constraint: str) -> None:
    assert violation(database_url, statement, errors.UniqueViolation).constraint_name == constraint


@pytest.mark.parametrize(("statement", "constraint"), FOREIGN_KEYS)
def test_a_foreign_key_refuses_the_row_or_the_delete(database_url: str, statement: str, constraint: str) -> None:
    assert violation(database_url, statement, errors.ForeignKeyViolation).constraint_name == constraint


@pytest.mark.parametrize("table", BASE_ROWS)
def test_the_rows_the_not_null_cases_start_from_are_valid(database_url: str, table: str) -> None:
    """So the refusals below come from the one column set to NULL."""
    run(database_url, insert(table, BASE_ROWS[table]))


@pytest.mark.parametrize(("table", "column"), NOT_NULLS)
def test_a_required_column_refuses_null(database_url: str, table: str, column: str) -> None:
    diag = violation(database_url, insert(table, {**BASE_ROWS[table], column: "NULL"}), errors.NotNullViolation)
    assert (diag.table_name, diag.column_name) == (table, column)
