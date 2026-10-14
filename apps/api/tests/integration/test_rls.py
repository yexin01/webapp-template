"""Row-level security tested directly at the database level (DB-06, DB-07, NT-08).

These run raw SQL as the runtime roles, bypassing application filters, to
prove the database enforces isolation on its own.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
import pytest

from app.kernel.infrastructure import migrations

pytestmark = pytest.mark.db

ALICE = "00000000-0000-4000-8000-00000000a11c"
BOB = "00000000-0000-4000-8000-0000000000b0"
ACME = "00000000-0000-4000-8000-0000000a0001"  # alice admin, bob member
GLOBEX = "00000000-0000-4000-8000-0000000a0002"  # bob admin only


@pytest.fixture
def seeded(db: str) -> str:
    migrations.seed(db)
    return db


@contextmanager
def as_role(url: str, role: str, **ctx: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(url) as conn, conn.transaction():
        conn.execute(f"SET LOCAL ROLE {role}")
        for key, value in ctx.items():
            conn.execute("SELECT set_config(%s, %s, true)", (f"app.{key}", value))
        yield conn


def names(conn: psycopg.Connection, sql: str = "SELECT name FROM app.projects ORDER BY name") -> list[Any]:
    return [r[0] for r in conn.execute(sql).fetchall()]


def test_api_role_without_context_sees_nothing(seeded: str) -> None:
    with as_role(seeded, "app_api") as conn:
        for table in (
            "users",
            "organizations",
            "organization_memberships",
            "projects",
            "billing_customers",
            "subscriptions",
        ):
            assert conn.execute(f"SELECT count(*) FROM app.{table}").fetchone() == (0,), table


def test_personal_context_sees_only_own_personal_projects(seeded: str) -> None:
    with as_role(seeded, "app_api", user_id=ALICE) as conn:
        assert names(conn) == ["Alice personal project"]


def test_org_context_requires_membership(seeded: str) -> None:
    with as_role(seeded, "app_api", user_id=ALICE, org_id=ACME) as conn:
        assert names(conn) == ["Acme roadmap"]
    # Alice is not a member of Globex: setting the context is not enough.
    with as_role(seeded, "app_api", user_id=ALICE, org_id=GLOBEX) as conn:
        assert names(conn) == []


def test_cannot_insert_into_other_tenant(seeded: str) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege), as_role(seeded, "app_api", user_id=ALICE) as conn:
        conn.execute("INSERT INTO app.projects (owner_user_id, created_by, name) VALUES (%s, %s, 'x')", (BOB, ALICE))
    with (
        pytest.raises(psycopg.errors.InsufficientPrivilege),
        as_role(seeded, "app_api", user_id=ALICE, org_id=GLOBEX) as conn,
    ):
        conn.execute("INSERT INTO app.projects (org_id, created_by, name) VALUES (%s, %s, 'x')", (GLOBEX, ALICE))


def test_cannot_delete_other_tenant_rows(seeded: str) -> None:
    with as_role(seeded, "app_api", user_id=ALICE) as conn:
        deleted = conn.execute("DELETE FROM app.projects WHERE owner_user_id = %s", (BOB,)).rowcount
        assert deleted == 0
    with as_role(seeded, "app_api", user_id=BOB) as conn:
        assert names(conn) == ["Bob personal project"]


def test_api_role_cannot_modify_memberships_or_subscriptions(seeded: str) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege), as_role(seeded, "app_api", user_id=BOB) as conn:
        conn.execute("UPDATE app.organization_memberships SET role = 'admin' WHERE user_id = %s", (BOB,))
    with pytest.raises(psycopg.errors.InsufficientPrivilege), as_role(seeded, "app_api", user_id=BOB) as conn:
        conn.execute(
            "INSERT INTO app.subscriptions (stripe_subscription_id, user_id, stripe_customer_id, status, plan_key) "
            "VALUES ('sub_x', %s, 'cus_x', 'active', 'pro_monthly')",
            (BOB,),
        )


def test_api_role_cannot_read_audit_or_ledgers(seeded: str) -> None:
    for table in ("audit_log", "billing_events", "identity_events"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege), as_role(seeded, "app_api", user_id=ALICE) as conn:
            conn.execute(f"SELECT * FROM app.{table}")


def test_users_visible_only_to_themselves(seeded: str) -> None:
    with as_role(seeded, "app_api", user_id=ALICE) as conn:
        rows = conn.execute("SELECT id::text FROM app.users").fetchall()
    assert rows == [(ALICE,)]


def test_runtime_roles_cannot_bypass_rls(seeded: str) -> None:
    rows = (
        psycopg.connect(seeded)
        .execute(
            "SELECT rolname, rolbypassrls, rolsuper, rolcanlogin FROM pg_roles WHERE rolname IN ('app_api','app_worker') ORDER BY 1"
        )
        .fetchall()
    )
    assert rows == [("app_api", False, False, False), ("app_worker", False, False, False)]


def test_every_app_table_has_forced_rls(seeded: str) -> None:
    rows = (
        psycopg.connect(seeded)
        .execute(
            "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'app' AND c.relkind = 'r'"
        )
        .fetchall()
    )
    assert rows, "no tables found"
    assert all(enabled and forced for _, enabled, forced in rows), rows


def test_membership_lock_only_returns_callers_role(seeded: str) -> None:
    with as_role(seeded, "app_api", user_id=BOB) as conn:
        assert conn.execute("SELECT app.lock_my_membership(%s)", (ACME,)).fetchone() == ("member",)
        assert conn.execute("SELECT app.lock_my_membership(%s)", (GLOBEX,)).fetchone() == ("admin",)
    with as_role(seeded, "app_api", user_id=ALICE) as conn:
        assert conn.execute("SELECT app.lock_my_membership(%s)", (GLOBEX,)).fetchone() == (None,)


@pytest.mark.parametrize(
    ("sql", "params"),
    [
        # users: a request may create only its own user row.
        (
            "INSERT INTO app.users (clerk_user_id) VALUES (%s)",
            ("user_new_by_alice",),
        ),
        # billing_customers: only the caller's own customer link.
        (
            "INSERT INTO app.billing_customers (user_id, stripe_customer_id) VALUES (%s, 'cus_wrongctx')",
            (BOB,),
        ),
        # audit_log: entries are attributed to the caller only.
        (
            "INSERT INTO app.audit_log (actor_user_id, action, target_type, outcome) "
            "VALUES (%s, 'x', 'project', 'allowed')",
            (BOB,),
        ),
        # outbox: only fresh, pending events, and only while acting for a user.
        (
            "INSERT INTO app.outbox (id, event_type, status, attempts) "
            "VALUES (gen_random_uuid(), 'projects.created', 'processed', 0)",
            (),
        ),
    ],
    ids=["users", "billing_customers", "audit_log", "outbox"],
)
def test_insert_policies_reject_wrong_context(seeded: str, sql: str, params: tuple[Any, ...]) -> None:
    """DB-07: a wrong context is denied for every insert policy, not only for reads."""
    with (
        pytest.raises(psycopg.errors.InsufficientPrivilege),
        as_role(seeded, "app_api", user_id=ALICE, clerk_user_id="user_alice_other") as conn,
    ):
        conn.execute(sql, params)


def test_insert_policies_reject_missing_context(seeded: str) -> None:
    for sql, params in (
        (
            "INSERT INTO app.audit_log (actor_user_id, action, target_type, outcome) VALUES (%s, 'x', 'p', 'allowed')",
            (ALICE,),
        ),
        ("INSERT INTO app.outbox (id, event_type) VALUES (gen_random_uuid(), 'projects.created')", ()),
        ("INSERT INTO app.billing_customers (user_id, stripe_customer_id) VALUES (%s, 'cus_noctx')", (ALICE,)),
    ):
        with pytest.raises(psycopg.errors.InsufficientPrivilege), as_role(seeded, "app_api") as conn:
            conn.execute(sql, params)
