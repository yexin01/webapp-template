"""Runtime roles that would escape row-level security stop the migrations (DB-06).

The core schema creates ``app_api`` and ``app_worker`` only when they are
missing, so a role that already existed on the server keeps the attributes and
memberships it had. Roles belong to the whole server, which other test sessions share, so the
check's own SQL runs here against uniquely named scratch roles in their place;
``app_api`` and ``app_worker`` themselves are never altered.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import psycopg
import pytest

from app.kernel.infrastructure import migrations
from tests.conftest import grant_to_administrator, require_superuser, superuser_query

pytestmark = pytest.mark.db

CHECK = migrations.repository_file("supabase", "migrations", "20261015000006_assert_runtime_roles.sql")
# A database that already applied this migration refuses a new one numbered before it.
LATEST_BEFORE_CHECK = 20261015000005
PREFIX = "unsafe runtime roles: "

UNSAFE_ATTRIBUTES = {
    "SUPERUSER": "is a superuser (ALTER ROLE {role} NOSUPERUSER)",
    "BYPASSRLS": "has BYPASSRLS (ALTER ROLE {role} NOBYPASSRLS)",
    "LOGIN": "has LOGIN (ALTER ROLE {role} NOLOGIN)",
}
MEMBER_OF_OWNER = (
    "{role} can act as the owner of relations in schema app through its membership in {through} "
    "(REVOKE {through} FROM {role})"
)
MEMBER_OF_POLICY_ROLE = (
    "{role} can use the row-level security policies for other roles through its membership in {through} "
    "(REVOKE {through} FROM {role})"
)

# kind: (the keyword its statements use, its definition)
OBJECTS = {
    "TABLE": ("TABLE", "(id integer PRIMARY KEY)"),
    "PARTITIONED TABLE": ("TABLE", "(id integer) PARTITION BY RANGE (id)"),
    "IDENTITY TABLE": ("TABLE", "(id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY)"),
    "SERIAL TABLE": ("TABLE", "(id serial PRIMARY KEY)"),
    "VIEW": ("VIEW", "AS SELECT 1 AS id"),
    "MATERIALIZED VIEW": ("MATERIALIZED VIEW", "AS SELECT 1 AS id"),
    "SEQUENCE": ("SEQUENCE", ""),
    "COMPOSITE TYPE": ("TYPE", "AS (id integer)"),
}


def check_sql(api: str = "app_api", worker: str = "app_worker") -> str:
    """The migration's SQL, checking the roles named here in place of app_api and app_worker."""
    text = CHECK.read_text(encoding="utf-8")
    assert text.count("'app_api'") == text.count("'app_worker'") == 1, "each role name must be replaced exactly once"
    return text.replace("'app_api'", f"'{api}'").replace("'app_worker'", f"'{worker}'")


def run_check(url: str, api: str = "app_api", worker: str = "app_worker") -> None:
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(check_sql(api, worker))


def migration_user(url: str) -> str:
    [(user,)] = superuser_query(url, "SELECT current_user::text")
    return str(user)


@contextmanager
def scratch_role(url: str, attributes: str = "", *, inherit: bool = False) -> Iterator[str]:
    """A uniquely named role created as the core schema creates the runtime roles, plus ``attributes``.

    Dropping it also revokes every membership it has or grants.
    """
    if "SUPERUSER" in attributes:
        require_superuser(url)
    role = f"app_test_role_{uuid.uuid4().hex[:10]}"
    superuser_query(url, f"CREATE ROLE {role} {'INHERIT' if inherit else 'NOINHERIT'} {attributes}")
    try:
        grant_to_administrator(url, role)  # so the relations below can be handed to it
        yield role
    finally:
        superuser_query(url, f"DROP ROLE {role}")


@contextmanager
def owned_relation(url: str, owner: str, kind: str, schema: str = "app") -> Iterator[str]:
    """A throwaway object of one of the ``OBJECTS`` kinds, owned by ``owner``; the real tables stay untouched."""
    keyword, definition = OBJECTS[kind]
    name = f"{schema}.roles_scratch_{uuid.uuid4().hex[:10]}"
    superuser_query(url, f"CREATE {keyword} {name} {definition}")
    try:
        # Without superuser rights, the new owner needs CREATE on the schema, as in the core schema's own hand-over.
        superuser_query(url, f"GRANT CREATE ON SCHEMA {schema} TO {owner}")
        try:
            superuser_query(url, f"ALTER {keyword} {name} OWNER TO {owner}")
        finally:
            superuser_query(url, f"REVOKE CREATE ON SCHEMA {schema} FROM {owner}")
        yield name
    finally:
        superuser_query(url, f"DROP {keyword} {name}")


@contextmanager
def policy_table(url: str, role: str, schema: str = "app") -> Iterator[str]:
    """A throwaway table owned by the migration role, with a policy that lets ``role`` through every row, as the
    worker's policies do."""
    name = f"{schema}.roles_scratch_{uuid.uuid4().hex[:10]}"
    superuser_query(url, f"CREATE TABLE {name} (id integer PRIMARY KEY)")
    try:
        superuser_query(url, f"ALTER TABLE {name} ENABLE ROW LEVEL SECURITY, FORCE ROW LEVEL SECURITY")
        superuser_query(url, f"CREATE POLICY everything ON {name} FOR ALL TO {role} USING (true) WITH CHECK (true)")
        yield name
    finally:
        superuser_query(url, f"DROP TABLE {name}")


@contextmanager
def empty_database(url: str) -> Iterator[str]:
    """A new database on the same server, dropped afterwards."""
    name = f"app_test_{uuid.uuid4().hex[:10]}"
    superuser_query(url, f'CREATE DATABASE "{name}"')
    try:
        yield urlunparse(urlparse(url)._replace(path=f"/{name}"))
    finally:
        superuser_query(url, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def stand_in_migrations(directory: Path, api: str, worker: str, login: str) -> list[migrations.Migration]:
    """Every migration, written to ``directory`` with the roles named here in place of app_api, app_worker, and
    app_login. The login role is created by the migrations: the caller drops it."""
    for migration in migrations.discover():
        text = migration.path.read_text(encoding="utf-8")
        text = re.sub(r"\bapp_login\b", login, re.sub(r"\bapp_worker\b", worker, re.sub(r"\bapp_api\b", api, text)))
        (directory / migration.path.name).write_text(text, encoding="utf-8")
    return migrations.discover(directory)


def problems(error: psycopg.errors.RaiseException) -> list[str]:
    message = error.diag.message_primary or ""
    assert message.startswith(PREFIX)
    return sorted(message.removeprefix(PREFIX).split("; "))


def test_the_runtime_roles_as_migrated_pass(db: str) -> None:
    [check] = [m for m in migrations.discover() if m.path == CHECK]
    assert int(check.version) > LATEST_BEFORE_CHECK
    applied = superuser_query(
        db, "SELECT version FROM public.app_schema_migrations WHERE version = %s", (check.version,)
    )
    assert applied == [(check.version,)]
    # app_worker owns this function by design; a function is not a relation.
    owner = superuser_query(
        db, "SELECT proowner::regrole::text FROM pg_proc WHERE oid = 'app.lock_my_membership(uuid)'::regprocedure"
    )
    assert owner == [("app_worker",)]
    run_check(db)


def test_roles_created_as_the_core_schema_creates_them_pass(db: str) -> None:
    with scratch_role(db) as api, scratch_role(db) as worker:
        run_check(db, api, worker)


@pytest.mark.parametrize("slot", ["api", "worker"])
@pytest.mark.parametrize("attribute", sorted(UNSAFE_ATTRIBUTES))
def test_a_runtime_role_with_an_unsafe_attribute_is_refused(db: str, attribute: str, slot: str) -> None:
    with scratch_role(db) as safe, scratch_role(db, attribute) as unsafe:
        api, worker = (unsafe, safe) if slot == "api" else (safe, unsafe)
        with pytest.raises(psycopg.errors.RaiseException) as raised:
            run_check(db, api, worker)
    assert raised.value.diag.message_primary == f"{PREFIX}{unsafe} {UNSAFE_ATTRIBUTES[attribute].format(role=unsafe)}"


@pytest.mark.parametrize("kind", [kind for kind in OBJECTS if kind != "COMPOSITE TYPE"])
def test_a_runtime_role_that_owns_a_relation_in_app_is_refused(db: str, kind: str) -> None:
    """Listed by relation, and the printed fix works for each kind.

    A table's indexes and the sequences behind its identity or serial column always have the table's owner and
    move with it, so they are left out.
    """
    with scratch_role(db) as api, scratch_role(db) as worker, owned_relation(db, worker, kind) as relation:
        with pytest.raises(psycopg.errors.RaiseException) as raised:
            run_check(db, api, worker)
        fix = f"ALTER TABLE <relation> OWNER TO {migration_user(db)}"
        assert raised.value.diag.message_primary == f"{PREFIX}{worker} owns {relation} ({fix})"

        superuser_query(db, fix.replace("<relation>", relation))
        run_check(db, api, worker)


@pytest.mark.parametrize(
    ("kind", "schema"), [("TABLE", "public"), ("COMPOSITE TYPE", "app")], ids=["table outside app", "type in app"]
)
def test_what_cannot_switch_off_row_level_security_in_app_does_not_count(db: str, kind: str, schema: str) -> None:
    """Owned directly or by a role the runtime role is a member of. A composite type has no rows to protect."""
    with (
        scratch_role(db) as owner,
        scratch_role(db) as api,
        scratch_role(db) as worker,
        owned_relation(db, owner, kind, schema),
        owned_relation(db, worker, kind, schema),
    ):
        superuser_query(db, f"GRANT {owner} TO {api}")
        run_check(db, api, worker)


@pytest.mark.parametrize(
    ("inherit", "grant_options"),
    [(True, ""), (False, "WITH INHERIT TRUE"), (False, "")],
    ids=["INHERIT role", "WITH INHERIT TRUE grant", "SET-only grant to a NOINHERIT role"],
)
def test_a_runtime_role_that_is_a_member_of_an_owner_is_refused(db: str, inherit: bool, grant_options: str) -> None:
    """A member that inherits the owner's privileges acts as the owner directly. One that does not, which is what
    a plain grant gives a role created like the runtime roles, can still hand the owner a SECURITY DEFINER
    function that then runs as the owner, so either one can switch row-level security off."""
    with (
        scratch_role(db) as owner,
        scratch_role(db, inherit=inherit) as api,
        scratch_role(db) as worker,
        owned_relation(db, owner, "TABLE"),
    ):
        superuser_query(db, f"GRANT {owner} TO {api} {grant_options}")
        with pytest.raises(psycopg.errors.RaiseException) as raised:
            run_check(db, api, worker)
        assert raised.value.diag.message_primary == PREFIX + MEMBER_OF_OWNER.format(role=api, through=owner)

        superuser_query(db, f"REVOKE {owner} FROM {api}")
        run_check(db, api, worker)


def test_membership_through_another_role_names_the_membership_to_revoke(db: str) -> None:
    """Revoking the owner from the runtime role would do nothing: the runtime role is a member of the role between."""
    with (
        scratch_role(db) as owner,
        scratch_role(db, inherit=True) as middle,
        scratch_role(db, inherit=True) as api,
        scratch_role(db) as worker,
        owned_relation(db, owner, "TABLE"),
    ):
        superuser_query(db, f"GRANT {owner} TO {middle}")
        superuser_query(db, f"GRANT {middle} TO {api}")
        with pytest.raises(psycopg.errors.RaiseException) as raised:
            run_check(db, api, worker)
        assert raised.value.diag.message_primary == PREFIX + MEMBER_OF_OWNER.format(role=api, through=middle)

        superuser_query(db, f"REVOKE {middle} FROM {api}")
        run_check(db, api, worker)


@pytest.mark.parametrize(
    ("inherit", "grant_options"),
    [(True, ""), (False, "WITH INHERIT TRUE"), (False, "")],
    ids=["INHERIT role", "WITH INHERIT TRUE grant", "SET-only grant to a NOINHERIT role"],
)
def test_a_runtime_role_that_is_a_member_of_a_policy_role_is_refused(
    db: str, inherit: bool, grant_options: str
) -> None:
    """A policy for a role applies to the members that inherit its privileges, so with inheritance every query of
    the API role passes the worker's policies, which let every row through; without it, a SET ROLE does the same."""
    with scratch_role(db, inherit=inherit) as api, scratch_role(db) as worker, policy_table(db, worker):
        superuser_query(db, f"GRANT {worker} TO {api} {grant_options}")
        with pytest.raises(psycopg.errors.RaiseException) as raised:
            run_check(db, api, worker)
        assert raised.value.diag.message_primary == PREFIX + MEMBER_OF_POLICY_ROLE.format(role=api, through=worker)

        superuser_query(db, f"REVOKE {worker} FROM {api}")
        run_check(db, api, worker)


def test_policies_reached_through_another_role_name_the_membership_to_revoke(db: str) -> None:
    with (
        scratch_role(db, inherit=True) as middle,
        scratch_role(db, inherit=True) as api,
        scratch_role(db) as worker,
        policy_table(db, worker),
    ):
        superuser_query(db, f"GRANT {worker} TO {middle}")
        superuser_query(db, f"GRANT {middle} TO {api}")
        with pytest.raises(psycopg.errors.RaiseException) as raised:
            run_check(db, api, worker)
        assert raised.value.diag.message_primary == PREFIX + MEMBER_OF_POLICY_ROLE.format(role=api, through=middle)

        superuser_query(db, f"REVOKE {middle} FROM {api}")
        run_check(db, api, worker)


def test_the_worker_role_may_not_use_the_policies_for_the_api_role_either(db: str) -> None:
    """The core schema gives neither runtime role any membership."""
    with scratch_role(db) as api, scratch_role(db, inherit=True) as worker, policy_table(db, api):
        superuser_query(db, f"GRANT {api} TO {worker}")
        with pytest.raises(psycopg.errors.RaiseException) as raised:
            run_check(db, api, worker)
        assert raised.value.diag.message_primary == PREFIX + MEMBER_OF_POLICY_ROLE.format(role=worker, through=api)

        superuser_query(db, f"REVOKE {api} FROM {worker}")
        run_check(db, api, worker)


def test_policies_outside_app_do_not_count(db: str) -> None:
    with (
        scratch_role(db) as other,
        scratch_role(db, inherit=True) as api,
        scratch_role(db) as worker,
        policy_table(db, other, "public"),
    ):
        superuser_query(db, f"GRANT {other} TO {api}")
        run_check(db, api, worker)


def test_a_runtime_role_that_is_a_member_of_a_superuser_is_refused(db: str) -> None:
    """A superuser counts as a member of every role, so the membership reaches the owner of the tables in app and
    the roles their policies are for."""
    with scratch_role(db, "SUPERUSER") as boss, scratch_role(db) as api, scratch_role(db) as worker:
        superuser_query(db, f"GRANT {boss} TO {api}")
        with pytest.raises(psycopg.errors.RaiseException) as raised:
            run_check(db, api, worker)
        assert raised.value.diag.message_primary == PREFIX + "; ".join(
            [MEMBER_OF_OWNER.format(role=api, through=boss), MEMBER_OF_POLICY_ROLE.format(role=api, through=boss)]
        )

        superuser_query(db, f"REVOKE {boss} FROM {api}")
        run_check(db, api, worker)


def test_every_problem_is_reported_at_once(db: str) -> None:
    with (
        scratch_role(db) as owner,
        scratch_role(db) as peer,
        scratch_role(db, "SUPERUSER") as api,
        scratch_role(db, "LOGIN BYPASSRLS") as worker,
        owned_relation(db, owner, "TABLE"),
        owned_relation(db, worker, "TABLE") as table,
        owned_relation(db, worker, "SEQUENCE") as sequence,
        policy_table(db, peer),
    ):
        superuser_query(db, f"GRANT {owner}, {peer} TO {api}")
        superuser_query(db, f"GRANT {owner}, {peer} TO {worker} WITH INHERIT TRUE")
        with pytest.raises(psycopg.errors.RaiseException) as raised:
            run_check(db, api, worker)
    owned = ", ".join(sorted((table, sequence)))
    assert problems(raised.value) == sorted(
        [
            f"{api} is a superuser (ALTER ROLE {api} NOSUPERUSER)",
            MEMBER_OF_OWNER.format(role=api, through=owner),
            MEMBER_OF_POLICY_ROLE.format(role=api, through=peer),
            f"{worker} has BYPASSRLS (ALTER ROLE {worker} NOBYPASSRLS)",
            f"{worker} has LOGIN (ALTER ROLE {worker} NOLOGIN)",
            f"{worker} owns {owned} (ALTER TABLE <relation> OWNER TO {migration_user(db)})",
            MEMBER_OF_OWNER.format(role=worker, through=owner),
            MEMBER_OF_POLICY_ROLE.format(role=worker, through=peer),
        ]
    )
    diag = raised.value.diag
    assert diag.message_detail
    assert "run the migrations again" in (diag.message_hint or "")


@pytest.mark.parametrize(
    ("unsafe", "fix", "problem"),
    [
        (
            "ALTER ROLE {worker} BYPASSRLS",
            "ALTER ROLE {worker} NOBYPASSRLS",
            "{worker} " + UNSAFE_ATTRIBUTES["BYPASSRLS"].format(role="{worker}"),
        ),
        (
            "GRANT {worker} TO {api}",
            "REVOKE {worker} FROM {api}",
            MEMBER_OF_POLICY_ROLE.format(role="{api}", through="{worker}"),
        ),
    ],
    ids=["worker with BYPASSRLS", "API role inheriting the worker's policies"],
)
def test_migrations_stop_at_an_unsafe_role_until_it_is_fixed(
    db: str, tmp_path: Path, unsafe: str, fix: str, problem: str
) -> None:
    """The scratch roles stand in for the runtime roles in every migration, so the real policies are checked. The
    migrations before the check still apply; the check is not recorded, so it runs again after the fix."""
    login = f"app_test_login_{uuid.uuid4().hex[:10]}"
    with scratch_role(db, inherit=True) as api, scratch_role(db) as worker, empty_database(db) as url:
        try:
            superuser_query(db, unsafe.format(api=api, worker=worker))
            every = stand_in_migrations(tmp_path, api, worker, login)
            [check] = [m for m in every if m.path.name == CHECK.name]
            with pytest.raises(psycopg.errors.RaiseException) as raised:
                migrations.migrate(url, tmp_path)
            assert raised.value.diag.message_primary == PREFIX + problem.format(api=api, worker=worker)
            recorded = superuser_query(url, "SELECT version FROM public.app_schema_migrations ORDER BY version")
            assert recorded == [(m.version,) for m in every if int(m.version) < int(check.version)]

            superuser_query(db, fix.format(api=api, worker=worker))
            applied = migrations.migrate(url, tmp_path)
            assert applied == [m.path.name for m in every if int(m.version) >= int(check.version)]
        finally:
            superuser_query(db, f"DROP ROLE IF EXISTS {login}")
