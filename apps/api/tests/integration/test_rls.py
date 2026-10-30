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
ACME_ROADMAP = "00000000-0000-4000-8000-000000000201"  # created by alice, in acme
ALICE_PERSONAL = "00000000-0000-4000-8000-000000000101"
EDIT_GRANT = "GRANT UPDATE (name, org_id, owner_user_id) ON app.projects TO app_api"


@pytest.fixture
def seeded(db: str) -> str:
    migrations.seed(db)
    return db


@pytest.fixture
def billed(seeded: str) -> str:
    """The seed plus a customer and an active subscription for each of Alice and Bob (the seed has none)."""
    with psycopg.connect(seeded) as conn, conn.transaction():
        for user, name in ((ALICE, "alice"), (BOB, "bob")):
            conn.execute(
                "INSERT INTO app.billing_customers (user_id, stripe_customer_id) VALUES (%s, %s)", (user, f"cus_{name}")
            )
            conn.execute(
                "INSERT INTO app.subscriptions (stripe_subscription_id, user_id, stripe_customer_id, status, plan_key) "
                "VALUES (%s, %s, %s, 'active', 'pro_monthly')",
                (f"sub_{name}", user, f"cus_{name}"),
            )
    return seeded


@contextmanager
def as_role(url: str, role: str, **ctx: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(url) as conn, conn.transaction():
        conn.execute(f"SET LOCAL ROLE {role}")
        for key, value in ctx.items():
            conn.execute("SELECT set_config(%s, %s, true)", (f"app.{key}", value))
        yield conn


@contextmanager
def as_role_with_edit_grant(url: str, role: str, **ctx: str) -> Iterator[psycopg.Connection]:
    """``as_role`` with UPDATE granted on the columns an adopter would let members edit, and everything rolled back.

    The grant covers the tenant keys too, so a refusal comes from the policy, not from a missing column privilege.
    """
    with psycopg.connect(url) as conn, conn.transaction(force_rollback=True):
        conn.execute(EDIT_GRANT)
        conn.execute(f"SET LOCAL ROLE {role}")
        for key, value in ctx.items():
            conn.execute("SELECT set_config(%s, %s, true)", (f"app.{key}", value))
        yield conn


def names(conn: psycopg.Connection, sql: str = "SELECT name FROM app.projects ORDER BY name") -> list[Any]:
    return [r[0] for r in conn.execute(sql).fetchall()]


def test_api_role_without_context_sees_nothing(billed: str) -> None:
    tables = ("users", "organizations", "organization_memberships", "projects", "billing_customers", "subscriptions")
    with psycopg.connect(billed) as conn:  # every table has rows, so the zero counts below come from RLS
        empty = [table for table in tables if conn.execute(f"SELECT count(*) FROM app.{table}").fetchone() == (0,)]
    assert empty == []
    with as_role(billed, "app_api") as conn:
        for table in tables:
            assert conn.execute(f"SELECT count(*) FROM app.{table}").fetchone() == (0,), table
        assert conn.execute("DELETE FROM app.projects").rowcount == 0


@pytest.mark.parametrize(
    ("sql", "alices", "bobs"),
    [
        ("SELECT stripe_subscription_id FROM app.subscriptions", [("sub_alice",)], [("sub_bob",)]),
        ("SELECT stripe_customer_id FROM app.billing_customers", [("cus_alice",)], [("cus_bob",)]),
        ("SELECT id::text FROM app.organizations", [(ACME,)], [(ACME,), (GLOBEX,)]),
        (
            "SELECT org_id::text, user_id::text, role FROM app.organization_memberships",
            [(ACME, ALICE, "admin")],
            [(ACME, BOB, "member"), (GLOBEX, BOB, "admin")],
        ),
    ],
    ids=["subscriptions", "billing_customers", "organizations", "organization_memberships"],
)
def test_reads_return_only_the_callers_rows(
    billed: str, sql: str, alices: list[tuple[str, ...]], bobs: list[tuple[str, ...]]
) -> None:
    """DB-07: each user's context reads its own rows (permitted) and never the other user's (wrong context)."""
    for user, expected in ((ALICE, alices), (BOB, bobs)):
        with as_role(billed, "app_api", user_id=user) as conn:
            assert sorted(conn.execute(sql).fetchall()) == sorted(expected), user


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


def test_cannot_insert_rows_created_by_someone_else(seeded: str) -> None:
    """DB-07: in both branches of the projects policy, the creator is the caller."""
    with pytest.raises(psycopg.errors.InsufficientPrivilege), as_role(seeded, "app_api", user_id=ALICE) as conn:
        conn.execute("INSERT INTO app.projects (owner_user_id, created_by, name) VALUES (%s, %s, 'x')", (ALICE, BOB))
    with (
        pytest.raises(psycopg.errors.InsufficientPrivilege),
        as_role(seeded, "app_api", user_id=ALICE, org_id=ACME) as conn,
    ):
        conn.execute("INSERT INTO app.projects (org_id, created_by, name) VALUES (%s, %s, 'x')", (ACME, BOB))


def test_a_member_may_update_a_row_another_member_created(seeded: str) -> None:
    """The creator is checked on INSERT only: a FOR ALL policy would have refused Bob here."""
    with as_role_with_edit_grant(seeded, "app_api", user_id=BOB, org_id=ACME) as conn:
        renamed = conn.execute(
            "UPDATE app.projects SET name = 'Acme roadmap v2' WHERE org_id = app.ctx_org_id() AND id = %s",
            (ACME_ROADMAP,),
        )
        assert renamed.rowcount == 1
        assert conn.execute("SELECT name, created_by::text FROM app.projects").fetchall() == [
            ("Acme roadmap v2", ALICE)
        ]


@pytest.mark.parametrize(
    ("context", "project", "change"),
    [
        ({"user_id": BOB, "org_id": ACME}, ACME_ROADMAP, f"org_id = '{GLOBEX}'"),  # Bob is a member of both
        ({"user_id": BOB, "org_id": ACME}, ACME_ROADMAP, f"org_id = NULL, owner_user_id = '{BOB}'"),
        ({"user_id": ALICE}, ALICE_PERSONAL, f"owner_user_id = NULL, org_id = '{ACME}'"),
        ({"user_id": ALICE}, ALICE_PERSONAL, f"owner_user_id = '{BOB}'"),
    ],
    ids=[
        "organization to another organization",
        "organization to personal",
        "personal to organization",
        "personal to another user",
    ],
)
def test_an_update_cannot_move_a_row_out_of_the_callers_tenant(
    seeded: str, context: dict[str, str], project: str, change: str
) -> None:
    with (
        pytest.raises(psycopg.errors.InsufficientPrivilege, match="row-level security"),
        as_role_with_edit_grant(seeded, "app_api", **context) as conn,
    ):
        conn.execute(f"UPDATE app.projects SET {change} WHERE id = %s", (project,))


@pytest.mark.parametrize(
    ("context", "project"),
    [
        ({"user_id": ALICE, "org_id": GLOBEX}, ACME_ROADMAP),  # not a member of that organization
        ({"user_id": ALICE}, ACME_ROADMAP),  # an organization row in the personal context
        ({"user_id": BOB, "org_id": ACME}, ALICE_PERSONAL),  # a personal row in an organization context
        ({"user_id": BOB}, ALICE_PERSONAL),  # someone else's personal row
        ({}, ACME_ROADMAP),  # no context
    ],
    ids=["not a member", "wrong context kind", "personal row in organization", "other user", "no context"],
)
def test_an_update_matches_nothing_outside_the_callers_tenant(
    seeded: str, context: dict[str, str], project: str
) -> None:
    with as_role_with_edit_grant(seeded, "app_api", **context) as conn:
        assert conn.execute("UPDATE app.projects SET name = 'x' WHERE id = %s", (project,)).rowcount == 0


def test_tenant_keys_are_never_updatable_by_the_request_role(seeded: str) -> None:
    """RLS cannot keep these columns unchanged; only the absence of a column grant does. A table-wide UPDATE grant
    on a tenant table fails here."""
    with psycopg.connect(seeded) as conn:
        for column in ("id", "org_id", "owner_user_id", "created_by", "created_at"):
            row = conn.execute("SELECT has_column_privilege('app_api', 'app.projects', %s, 'UPDATE')", (column,))
            assert row.fetchone() == (False,), column


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


@pytest.fixture
def with_open_checkouts(seeded: str) -> str:
    """The seed plus an open Checkout Session for each of Alice and Bob (DEC-013)."""
    with psycopg.connect(seeded) as conn, conn.transaction():
        for user, name in ((ALICE, "alice"), (BOB, "bob")):
            conn.execute(
                "INSERT INTO app.billing_checkout_sessions (user_id, stripe_session_id, idempotency_key) "
                "VALUES (%s, %s, 'key')",
                (user, f"cs_test_{name}"),
            )
    return seeded


def test_each_user_reaches_only_their_own_open_checkout_session(with_open_checkouts: str) -> None:
    for user, name in ((ALICE, "alice"), (BOB, "bob")):
        with as_role(with_open_checkouts, "app_api", user_id=user) as conn:
            assert conn.execute("SELECT stripe_session_id FROM app.billing_checkout_sessions").fetchall() == [
                (f"cs_test_{name}",)
            ]
    with as_role(with_open_checkouts, "app_api") as conn:  # no user in the context
        assert conn.execute("SELECT count(*) FROM app.billing_checkout_sessions").fetchone() == (0,)


def test_a_user_cannot_change_or_delete_another_users_open_checkout_session(with_open_checkouts: str) -> None:
    with as_role(with_open_checkouts, "app_api", user_id=ALICE) as conn:
        assert conn.execute("UPDATE app.billing_checkout_sessions SET stripe_session_id = 'cs_x'").rowcount == 1
        assert conn.execute("DELETE FROM app.billing_checkout_sessions WHERE user_id = %s", (BOB,)).rowcount == 0
    with as_role(with_open_checkouts, "app_api", user_id=BOB) as conn:
        assert conn.execute("SELECT stripe_session_id FROM app.billing_checkout_sessions").fetchall() == [
            ("cs_test_bob",)
        ]


@pytest.mark.parametrize("user", [BOB, None])
def test_a_user_cannot_save_an_open_checkout_session_for_someone_else(seeded: str, user: str | None) -> None:
    context = {"user_id": user} if user else {}
    with (
        pytest.raises(psycopg.errors.InsufficientPrivilege),
        as_role(seeded, "app_api", **context) as conn,
    ):
        conn.execute(
            "INSERT INTO app.billing_checkout_sessions (user_id, stripe_session_id, idempotency_key) "
            "VALUES (%s, 'cs_test_x', 'key')",
            (ALICE,),
        )


def test_the_worker_role_has_no_access_to_open_checkout_sessions(with_open_checkouts: str) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege), as_role(with_open_checkouts, "app_worker") as conn:
        conn.execute("SELECT * FROM app.billing_checkout_sessions")


def test_open_checkout_sessions_are_removed_with_their_user(with_open_checkouts: str) -> None:
    with psycopg.connect(with_open_checkouts) as conn, conn.transaction():
        conn.execute("DELETE FROM app.projects WHERE owner_user_id = %s OR created_by = %s", (BOB, BOB))
        conn.execute("DELETE FROM app.organization_memberships WHERE user_id = %s", (BOB,))
        conn.execute("DELETE FROM app.users WHERE id = %s", (BOB,))
        assert conn.execute("SELECT user_id::text FROM app.billing_checkout_sessions").fetchall() == [(ALICE,)]


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


@pytest.mark.parametrize(
    ("context", "sql", "params"),
    [
        ({"clerk_user_id": "user_new"}, "INSERT INTO app.users (clerk_user_id) VALUES ('user_new')", ()),
        (
            {"user_id": ALICE},
            "INSERT INTO app.projects (owner_user_id, created_by, name) VALUES (%s, %s, 'x')",
            (ALICE, ALICE),
        ),
        (
            {"user_id": ALICE, "org_id": ACME},
            "INSERT INTO app.projects (org_id, created_by, name) VALUES (%s, %s, 'x')",
            (ACME, ALICE),
        ),
        (
            {"user_id": ALICE},
            "INSERT INTO app.billing_customers (user_id, stripe_customer_id) VALUES (%s, 'cus_own')",
            (ALICE,),
        ),
        (
            {"user_id": ALICE},
            "INSERT INTO app.audit_log (actor_user_id, action, target_type, outcome) VALUES (%s, 'x', 'p', 'allowed')",
            (ALICE,),
        ),
        (
            {"user_id": ALICE},
            "INSERT INTO app.outbox (id, event_type) VALUES (gen_random_uuid(), 'projects.created')",
            (),
        ),
    ],
    ids=["users", "projects_personal", "projects_organization", "billing_customers", "audit_log", "outbox"],
)
def test_insert_policies_permit_the_callers_own_rows(
    seeded: str, context: dict[str, str], sql: str, params: tuple[Any, ...]
) -> None:
    """DB-07: the permitted case, so the denials around it are not just a policy that refuses everything."""
    with as_role(seeded, "app_api", **context) as conn:
        assert conn.execute(sql, params).rowcount == 1


def test_insert_policies_reject_missing_context(seeded: str) -> None:
    for sql, params in (
        (
            "INSERT INTO app.audit_log (actor_user_id, action, target_type, outcome) VALUES (%s, 'x', 'p', 'allowed')",
            (ALICE,),
        ),
        ("INSERT INTO app.outbox (id, event_type) VALUES (gen_random_uuid(), 'projects.created')", ()),
        ("INSERT INTO app.billing_customers (user_id, stripe_customer_id) VALUES (%s, 'cus_noctx')", (ALICE,)),
        ("INSERT INTO app.users (clerk_user_id) VALUES ('user_noctx')", ()),
        ("INSERT INTO app.projects (owner_user_id, created_by, name) VALUES (%s, %s, 'x')", (ALICE, ALICE)),
        ("INSERT INTO app.projects (org_id, created_by, name) VALUES (%s, %s, 'x')", (ACME, ALICE)),
    ):
        with pytest.raises(psycopg.errors.InsufficientPrivilege), as_role(seeded, "app_api") as conn:
            conn.execute(sql, params)
