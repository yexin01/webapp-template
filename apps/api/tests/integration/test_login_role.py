"""The role the API logs in as (DEC-008): its definition, the check that refuses a privileged login in preview and
production, and the command that lets it log in.

Roles belong to the whole server, which other test sessions share, so everything here runs against uniquely named
roles created from the migration's own SQL under another name; ``app_login`` itself is never altered. What the
tests show is limited on purpose: ``app_login`` can still ``SET ROLE app_worker``, whose policies let every row
through, so the login role keeps a query from becoming the table owner, not from every cross-tenant read.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse, urlunparse

import psycopg
import pytest

from app import cli
from app.kernel.application.probes import DatabaseProbe
from app.kernel.diagnostics import IntegrationInfo, IntegrationKey, IntegrationMode, Outcome
from app.kernel.errors import DependencyUnavailableError
from app.kernel.infrastructure import migrations
from app.kernel.infrastructure.config import DatabaseSettings
from app.kernel.infrastructure.db import UNSAFE_LOGIN_ROLE, Database, PgTransactions, UnsafeLoginRoleError
from app.kernel.infrastructure.store import PgKernelStore
from app.kernel.ports import KernelStore
from app.kernel.tenancy import ContextKind, TenantContext
from tests.conftest import Harness, superuser_query

pytestmark = pytest.mark.db

CREATE = migrations.repository_file("supabase", "migrations", "20261030000010_app_login_role.sql")
CHECK = migrations.repository_file("supabase", "migrations", "20261030000011_assert_login_role.sql")
PREFIX = "unsafe login role: "
ALICE = "00000000-0000-4000-8000-00000000a11c"


def sql_for(path: Path, role: str) -> str:
    """The migration's SQL for ``role`` in place of app_login."""
    text = path.read_text(encoding="utf-8")
    assert "app_login" in text
    return text.replace("app_login", role)


def run_sql(url: str, text: str) -> None:
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(text)  # type: ignore[call-overload]


def migration_user(url: str) -> str:
    [(user,)] = superuser_query(url, "SELECT current_user::text")
    return str(user)


def login_url(url: str, role: str, password: str) -> str:
    parsed = urlparse(url)
    return urlunparse(parsed._replace(netloc=f"{role}:{quote(password, safe='')}@{parsed.hostname}:{parsed.port}"))


@contextmanager
def login_role(url: str, *, enabled: bool = True) -> Iterator[tuple[str, str]]:
    """A role the login role migration created under another name, with LOGIN and a password if ``enabled``."""
    role = f"app_test_login_{uuid.uuid4().hex[:10]}"
    password = secrets.token_urlsafe(24)
    try:
        run_sql(url, sql_for(CREATE, role))
        if enabled:
            migrations.enable_api_login(url, password, role)
        yield role, password
    finally:
        superuser_query(url, f"DROP ROLE IF EXISTS {role}")


@contextmanager
def scratch_role(url: str, attributes: str = "", *, password: str | None = None) -> Iterator[str]:
    """A uniquely named role the migration role can act as, so it can hand objects to it."""
    role = f"app_test_role_{uuid.uuid4().hex[:10]}"
    login = f"LOGIN PASSWORD '{password}'" if password else "NOLOGIN"
    superuser_query(url, f"CREATE ROLE {role} {login} NOINHERIT {attributes}")
    superuser_query(url, f'GRANT {role} TO "{migration_user(url)}"')
    try:
        yield role
    finally:
        superuser_query(url, f"DROP ROLE {role}")


@contextmanager
def owned_table(url: str, owner: str) -> Iterator[str]:
    name = f"app.login_scratch_{uuid.uuid4().hex[:10]}"
    superuser_query(url, f"CREATE TABLE {name} (id integer PRIMARY KEY)")
    try:
        superuser_query(url, f"GRANT CREATE ON SCHEMA app TO {owner}")  # a non-superuser needs it to hand over a table
        superuser_query(url, f"ALTER TABLE {name} OWNER TO {owner}")
        superuser_query(url, f"REVOKE CREATE ON SCHEMA app FROM {owner}")
        yield name
    finally:
        superuser_query(url, f"DROP TABLE {name}")


def database(url: str, *, enforce: bool) -> Database:
    return Database(
        url,
        api_pool_size=1,
        worker_pool_size=2,
        connect_timeout=3,
        statement_timeout_ms=5000,
        enforce_login_role=enforce,
    )


def check_message(error: psycopg.errors.RaiseException) -> str:
    message = error.diag.message_primary or ""
    assert message.startswith(PREFIX)
    return message.removeprefix(PREFIX)


# -- The role as migrated ---------------------------------------------------------------


def test_the_login_role_as_migrated(db: str) -> None:
    [row] = superuser_query(
        db,
        "SELECT rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, rolreplication, rolinherit "
        "FROM pg_roles WHERE rolname = 'app_login'",
    )
    assert row == (False,) * 6
    memberships = superuser_query(
        db,
        "SELECT g.rolname FROM pg_auth_members m JOIN pg_roles g ON g.oid = m.roleid "
        "WHERE m.member = 'app_login'::regrole ORDER BY 1",
    )
    assert memberships == [("app_api",), ("app_worker",)]
    # No privilege of its own: NOINHERIT keeps everything it could reach through SET ROLE out of this.
    assert superuser_query(db, "SELECT has_schema_privilege('app_login', 'app', 'USAGE')") == [(False,)]
    assert superuser_query(db, "SELECT has_table_privilege('app_login', 'app.projects', 'SELECT')") == [(False,)]
    run_sql(db, CHECK.read_text(encoding="utf-8"))  # and the migration's own check agrees


def test_the_login_role_migration_can_run_again(db: str) -> None:
    with login_role(db, enabled=False) as (role, _):
        run_sql(db, sql_for(CREATE, role))
        run_sql(db, sql_for(CHECK, role))


# -- The check ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("attribute", "problem"),
    [
        ("SUPERUSER", "{role} is a superuser (ALTER ROLE {role} NOSUPERUSER)"),
        ("BYPASSRLS", "{role} has BYPASSRLS (ALTER ROLE {role} NOBYPASSRLS)"),
        ("CREATEROLE", "{role} has CREATEROLE (ALTER ROLE {role} NOCREATEROLE)"),
        ("REPLICATION", "{role} has REPLICATION (ALTER ROLE {role} NOREPLICATION)"),
        ("INHERIT", "{role} inherits its memberships (ALTER ROLE {role} NOINHERIT)"),
    ],
)
def test_a_login_role_with_an_unsafe_attribute_is_refused(db: str, attribute: str, problem: str) -> None:
    with login_role(db, enabled=False) as (role, _):
        run_sql(db, sql_for(CHECK, role))
        superuser_query(db, f"ALTER ROLE {role} {attribute}")
        with pytest.raises(psycopg.errors.RaiseException) as raised:
            run_sql(db, sql_for(CHECK, role))
        assert check_message(raised.value) == problem.format(role=role)
        assert "run the migrations again" in (raised.value.diag.message_hint or "")

        superuser_query(
            db, f"ALTER ROLE {role} NO{attribute}" if attribute != "INHERIT" else f"ALTER ROLE {role} NOINHERIT"
        )
        run_sql(db, sql_for(CHECK, role))


def test_the_ability_to_log_in_is_expected(db: str) -> None:
    with login_role(db) as (role, _):
        run_sql(db, sql_for(CHECK, role))


def test_a_login_role_with_privileges_of_its_own_in_app_is_refused(db: str) -> None:
    with login_role(db, enabled=False) as (role, _):
        for grant, revoke, on in (
            ("GRANT SELECT ON app.users TO {role}", "REVOKE ALL ON app.users FROM {role}", "app.users"),
            (
                "GRANT EXECUTE ON FUNCTION app.ctx_user_id() TO {role}",
                "REVOKE ALL ON FUNCTION app.ctx_user_id() FROM {role}",
                "app.ctx_user_id()",
            ),
            ("GRANT USAGE ON SCHEMA app TO {role}", "REVOKE ALL ON SCHEMA app FROM {role}", "schema app"),
        ):
            superuser_query(db, grant.format(role=role))
            with pytest.raises(psycopg.errors.RaiseException) as raised:
                run_sql(db, sql_for(CHECK, role))
            expected = f"{role} holds privileges of its own on {on} (REVOKE ALL ON <object> FROM {role})"
            assert check_message(raised.value) == expected

            superuser_query(db, revoke.format(role=role))
            run_sql(db, sql_for(CHECK, role))


def test_a_login_role_that_is_a_member_of_another_role_is_refused(db: str) -> None:
    with login_role(db, enabled=False) as (role, _), scratch_role(db) as other:
        superuser_query(db, f"GRANT {other} TO {role}")
        with pytest.raises(psycopg.errors.RaiseException) as raised:
            run_sql(db, sql_for(CHECK, role))
        assert check_message(raised.value) == (
            f"{role} is a member of {other}, which it may not be (REVOKE {other} FROM {role})"
        )

        superuser_query(db, f"REVOKE {other} FROM {role}")
        run_sql(db, sql_for(CHECK, role))


# -- Logging in as it -----------------------------------------------------------------------


@pytest.fixture
def seeded(db: str) -> str:
    migrations.seed(db)
    return db


def test_a_tenant_transaction_through_the_login_role_sees_only_its_own_rows(seeded: str) -> None:
    with login_role(seeded) as (role, password), psycopg.connect(login_url(seeded, role, password)) as conn:
        with conn.transaction():
            conn.execute("SELECT set_config('role', 'app_api', true), set_config('app.user_id', %s, true)", (ALICE,))
            rows = conn.execute("SELECT name FROM app.projects").fetchall()
        assert rows == [("Alice personal project",)]


def count_projects(conn: psycopg.Connection[Any]) -> Any:
    return conn.execute("SELECT count(*) FROM app.projects").fetchone()


def leave_then_count(conn: psycopg.Connection[Any], leave: str) -> None:
    conn.execute("SELECT set_config('role', 'app_api', true)")
    conn.execute(leave)  # type: ignore[call-overload]
    count_projects(conn)


def test_leaving_the_runtime_role_leaves_the_login_role_with_nothing(seeded: str) -> None:
    """The escape the migration role allows: inside an ordinary query, set_config('role', 'none', true)."""
    with login_role(seeded) as (role, password), psycopg.connect(login_url(seeded, role, password)) as conn:
        for leave in ("SELECT set_config('role', 'none', true)", "RESET ROLE"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
                leave_then_count(conn, leave)
        with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
            count_projects(conn)  # no role switch at all


def test_the_migration_role_can_leave_the_runtime_role_and_read_everything(seeded: str) -> None:
    """What logging in as the schema owner allows, which the login role exists to prevent."""
    with psycopg.connect(seeded) as conn, conn.transaction():
        conn.execute("SELECT set_config('role', 'app_api', true), set_config('app.user_id', %s, true)", (ALICE,))
        assert conn.execute("SELECT count(*) FROM app.projects").fetchone() == (1,)
        conn.execute("SELECT set_config('role', 'none', true)")
        assert conn.execute("SELECT count(*) FROM app.projects").fetchone() == (4,)


def test_the_login_role_can_still_switch_to_the_worker_role(seeded: str) -> None:
    """The limit of this design, stated: the worker role's policies let every row through, and SET ROLE is
    allowed to app_login. Code that runs arbitrary SQL can read across tenants this way; a second login role that
    is a member of app_worker only (and a second pool) would close it."""
    with (
        login_role(seeded) as (role, password),
        psycopg.connect(login_url(seeded, role, password)) as conn,
        conn.transaction(),
    ):
        conn.execute("SET LOCAL ROLE app_worker")
        assert count_projects(conn) == (4,)


# -- Refusing a privileged login ---------------------------------------------------------------


def test_preview_and_production_refuse_the_migration_role_as_login(db: str, caplog: pytest.LogCaptureFixture) -> None:
    pool = database(db, enforce=True)
    try:
        with caplog.at_level(logging.ERROR):
            for _ in range(2):  # refused every time, from the first check's result
                with pytest.raises(UnsafeLoginRoleError) as raised, pool.transaction("app_api", {}):
                    pytest.fail("a transaction ran as an unsafe login role")
                assert raised.value.public_message == UNSAFE_LOGIN_ROLE
                assert isinstance(raised.value, DependencyUnavailableError)  # a 503
    finally:
        pool.close()
    logged = [r for r in caplog.records if r.getMessage() == "unsafe_database_login_role"]
    assert len(logged) == 1  # once per process
    assert db not in caplog.text
    assert "pnpm db:api-login" in UNSAFE_LOGIN_ROLE
    assert "postgresql://" not in UNSAFE_LOGIN_ROLE + str(logged[0].__dict__.get("problems"))


def test_local_development_and_tests_may_log_in_as_the_migration_role(db: str) -> None:
    pool = database(db, enforce=False)
    try:
        with pool.transaction("app_api", {}) as conn:
            assert conn.execute("SELECT current_user::text").fetchone() == ("app_api",)
    finally:
        pool.close()


def test_a_login_role_with_bypassrls_is_refused(db: str) -> None:
    password = secrets.token_urlsafe(24)
    with scratch_role(db, "BYPASSRLS", password=password) as role:
        superuser_query(db, f"GRANT app_api, app_worker TO {role}")
        pool = database(login_url(db, role, password), enforce=True)
        try:
            with pytest.raises(UnsafeLoginRoleError), pool.transaction("app_api", {}):
                pytest.fail("a transaction ran as an unsafe login role")
        finally:
            pool.close()


def test_a_login_role_that_can_become_the_table_owner_is_refused(db: str) -> None:
    """A NOINHERIT member of the owner still gets there with SET ROLE (or by handing the owner a function)."""
    password = secrets.token_urlsafe(24)
    with scratch_role(db) as owner, owned_table(db, owner), scratch_role(db, password=password) as role:
        superuser_query(db, f"GRANT app_api, app_worker, {owner} TO {role}")
        pool = database(login_url(db, role, password), enforce=True)
        try:
            with pytest.raises(UnsafeLoginRoleError), pool.transaction("app_api", {}):
                pytest.fail("a transaction ran as an unsafe login role")
        finally:
            pool.close()


def test_the_login_role_is_allowed_and_the_probe_says_so(seeded: str) -> None:
    ctx = TenantContext(uuid.UUID(ALICE), "user_seed_alice", ContextKind.PERSONAL)
    info = IntegrationInfo(IntegrationKey.DATABASE, "PostgreSQL", IntegrationMode.LOCAL)
    with login_role(seeded) as (role, password):
        pool = database(login_url(seeded, role, password), enforce=True)
        try:
            with pool.transaction("app_api", {}) as conn:
                assert conn.execute("SELECT current_user::text, session_user::text").fetchone() == ("app_api", role)
            tx: PgTransactions[KernelStore] = PgTransactions(pool, PgKernelStore)
            verdict = DatabaseProbe(info, tx, restricted_login=True).check(ctx)
        finally:
            pool.close()
    assert verdict.outcome is Outcome.PASSED, verdict.summary
    assert "The login role cannot skip row-level security." in verdict.summary


def test_the_probe_reports_a_privileged_login_instead_of_calling_rls_applied(seeded: str) -> None:
    ctx = TenantContext(uuid.UUID(ALICE), "user_seed_alice", ContextKind.PERSONAL)
    info = IntegrationInfo(IntegrationKey.DATABASE, "PostgreSQL", IntegrationMode.LOCAL)
    pool = database(seeded, enforce=False)
    try:
        tx: PgTransactions[KernelStore] = PgTransactions(pool, PgKernelStore)
        local = DatabaseProbe(info, tx).check(ctx)
        hosted = DatabaseProbe(info, tx, restricted_login=True).check(ctx)
    finally:
        pool.close()
    assert local.outcome is Outcome.PASSED
    assert "Caution" in local.summary
    assert "cannot skip" not in local.summary
    assert (hosted.outcome, hosted.error_code) == (Outcome.FAILED, "unsafe_session")
    assert "pnpm db:api-login" in hosted.summary
    assert migration_user(seeded) not in hosted.summary  # the login role is never named


# -- Through the API ---------------------------------------------------------------------------


def test_requests_fail_with_a_503_naming_the_fix_while_the_login_role_is_privileged(
    db: str, make_harness: Callable[..., Harness]
) -> None:
    h = make_harness(database=database(db, enforce=True))
    token = h.login("user_a")
    assert h.client.get("/api/v1/health").status_code == 200  # liveness needs no database
    ready = h.client.get("/api/v1/ready")
    assert (ready.status_code, ready.json()["status"]) == (503, "not_ready")
    response = h.client.get("/api/v1/projects", headers=token)
    assert response.status_code == 503
    assert UNSAFE_LOGIN_ROLE in response.text
    assert "postgresql://" not in response.text
    assert urlparse(db).password not in response.text


def test_the_api_runs_end_to_end_as_the_login_role(db: str, make_harness: Callable[..., Harness]) -> None:
    with login_role(db) as (role, password):
        h = make_harness(database=database(login_url(db, role, password), enforce=True))
        token = h.login("user_a")
        created = h.client.post("/api/v1/projects", json={"name": "from the login role"}, headers=token)
        assert created.status_code == 201, created.text
        listing = h.client.get("/api/v1/projects", headers=token).json()
        assert [p["name"] for p in listing["items"]] == ["from the login role"]
        assert h.client.get("/api/v1/ready").status_code == 200
        h.container.close()  # its connections must end before the role is dropped


# -- The command ---------------------------------------------------------------------------------


@pytest.fixture
def command(db: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[str, str]]:
    """``api-login`` aimed at a role of its own: (database URL, role)."""
    monkeypatch.setitem(DatabaseSettings.model_config, "env_file", None)  # never the developer's .env
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@127.0.0.1:1/unused")
    monkeypatch.setenv("MIGRATION_DATABASE_URL", db)
    monkeypatch.delenv("API_DB_PASSWORD", raising=False)
    with login_role(db, enabled=False) as (role, _):
        monkeypatch.setattr(migrations, "APP_LOGIN_ROLE", role)
        yield db, role


def sent_statements(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every statement any connection executes from now on, as text."""
    seen: list[str] = []
    execute = psycopg.Connection.execute

    def spy(self: psycopg.Connection[Any], query: Any, *args: Any, **kwargs: Any) -> Any:
        seen.append(query.as_string(self) if hasattr(query, "as_string") else str(query))
        return execute(self, query, *args, **kwargs)

    monkeypatch.setattr(psycopg.Connection, "execute", spy)
    return seen


def test_api_login_sends_only_a_verifier_and_the_role_can_then_log_in(
    command: tuple[str, str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    url, role = command
    password = secrets.token_urlsafe(24)
    monkeypatch.setenv("API_DB_PASSWORD", password)
    statements = sent_statements(monkeypatch)
    assert cli.main(["api-login"]) == 0
    printed = capsys.readouterr()
    assert password not in printed.out + printed.err
    assert not any(password in statement for statement in statements)
    [alter] = [s for s in statements if s.startswith("ALTER ROLE")]
    assert f'"{role}" LOGIN PASSWORD \'SCRAM-SHA-256$' in alter
    assert f"postgresql://{role}:<password>@127.0.0.1" in printed.err  # where DATABASE_URL goes, minus the secret
    with psycopg.connect(login_url(url, role, password)) as conn:
        assert conn.execute("SELECT session_user::text").fetchone() == (role,)


def test_api_login_rotates_the_password(command: tuple[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    url, role = command
    first, second = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    for password in (first, second):
        monkeypatch.setenv("API_DB_PASSWORD", password)
        assert cli.main(["api-login"]) == 0
    with pytest.raises(psycopg.OperationalError):
        psycopg.connect(login_url(url, role, first), connect_timeout=3)
    with psycopg.connect(login_url(url, role, second)) as conn:
        assert conn.execute("SELECT 1").fetchone() == (1,)


def test_api_login_refuses_a_remote_database_without_the_flag(
    command: tuple[str, str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("MIGRATION_DATABASE_URL", "postgresql://owner:hunter2@db.example.com:5432/postgres")
    monkeypatch.setenv("API_DB_PASSWORD", secrets.token_urlsafe(24))
    statements = sent_statements(monkeypatch)
    assert cli.main(["api-login"]) == 1
    assert "not local" in capsys.readouterr().err
    assert statements == []


@pytest.mark.parametrize(
    ("password", "message"),
    [("short", "at least 16 characters"), ("", "Set API_DB_PASSWORD")],
    ids=["too short", "no password and no terminal"],
)
def test_api_login_needs_a_proper_password(
    command: tuple[str, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    password: str,
    message: str,
) -> None:
    monkeypatch.setenv("API_DB_PASSWORD", password)
    assert cli.main(["api-login"]) == 1
    assert message in capsys.readouterr().err
    assert superuser_query(command[0], "SELECT rolcanlogin FROM pg_roles WHERE rolname = %s", (command[1],)) == [
        (False,)
    ]


def test_api_login_needs_the_migrations_first(
    command: tuple[str, str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(migrations, "APP_LOGIN_ROLE", "app_test_login_missing")
    monkeypatch.setenv("API_DB_PASSWORD", secrets.token_urlsafe(24))
    assert cli.main(["api-login"]) == 1
    assert "pnpm db:migrate" in capsys.readouterr().err


def test_api_login_refuses_a_role_that_could_skip_rls(
    command: tuple[str, str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    url, role = command
    superuser_query(url, f"ALTER ROLE {role} INHERIT")
    monkeypatch.setenv("API_DB_PASSWORD", secrets.token_urlsafe(24))
    assert cli.main(["api-login"]) == 1
    assert "INHERIT" in capsys.readouterr().err
    assert superuser_query(url, "SELECT rolcanlogin FROM pg_roles WHERE rolname = %s", (role,)) == [(False,)]


def test_migrate_uses_the_migration_url_and_the_runtime_url_is_never_touched(
    command: tuple[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    """DATABASE_URL points nowhere here: the command would fail if it connected with it."""
    assert cli.main(["migrate"]) == 0
    assert capsys.readouterr().out.strip() == '{"applied": []}'
    assert cli.main(["seed", "--yes"]) == 0
    assert cli.main(["seed", "--yes"]) == 0  # idempotent


def test_migrate_falls_back_to_the_runtime_url(
    command: tuple[str, str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("MIGRATION_DATABASE_URL")
    monkeypatch.setenv("DATABASE_URL", command[0])
    assert cli.main(["migrate"]) == 0
    assert capsys.readouterr().out.strip() == '{"applied": []}'
