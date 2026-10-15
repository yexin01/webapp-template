"""Database maintenance commands: production-targeting guards (ENV-05) and a
migration runner that is safe to point at a pooled, busy database (MIG-01)."""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

import psycopg
import pytest

from app import cli
from app.kernel.infrastructure import migrations
from app.kernel.infrastructure.config import DatabaseSettings
from tests.conftest import superuser_query

pytestmark = pytest.mark.db

UNREACHABLE_LOCAL_URL = "postgresql://u:p@127.0.0.1:1/none"  # local, so only APP_ENV or --yes can stop a reset


@pytest.fixture
def maintenance_env(monkeypatch: pytest.MonkeyPatch, db: str) -> str:
    monkeypatch.setitem(DatabaseSettings.model_config, "env_file", None)  # never the developer's .env
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("DATABASE_URL", db)
    return db


def seeded_users(url: str) -> int:
    return int(superuser_query(url, "SELECT count(*) FROM app.users")[0][0])


def test_seed_needs_explicit_confirmation(maintenance_env: str) -> None:
    assert cli.main(["seed"]) == 2
    assert seeded_users(maintenance_env) == 0
    assert cli.main(["seed", "--yes"]) == 0
    assert seeded_users(maintenance_env) > 0


def test_reset_needs_explicit_confirmation(maintenance_env: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", UNREACHABLE_LOCAL_URL)
    assert cli.main(["reset", "--seed"]) == 2  # refused before connecting


def test_seed_and_reset_need_app_env_set_explicitly(maintenance_env: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """An unset APP_ENV is not taken to mean local: a shell with a tunnelled DATABASE_URL must not pass."""
    monkeypatch.delenv("APP_ENV")
    assert cli.main(["seed", "--yes"]) == 1
    assert seeded_users(maintenance_env) == 0
    monkeypatch.setenv("DATABASE_URL", UNREACHABLE_LOCAL_URL)
    assert cli.main(["reset", "--yes"]) == 1


@pytest.mark.parametrize(
    "url",
    [
        "postgresql:///app?host=/var/run/postgresql",
        "postgresql:///app?host=/run/postgresql",
        "postgresql:///app?host=/tmp",
        "postgresql:///app?host=/private/tmp/",
    ],
)
def test_unix_sockets_in_well_known_local_directories_are_local(url: str) -> None:
    assert migrations.is_local_database(url)


@pytest.mark.parametrize(
    "url",
    [
        # The Cloud SQL Auth Proxy serves a remote database on a local socket.
        "postgresql:///app?host=/cloudsql/project:region:production",
        "postgresql:///app?host=/tmp/cloudsql/project:region:production",
        "postgresql:///app?host=/var/run/postgresql/../../cloudsql/project:region:production",
        "postgresql://u:p@localhost/app?host=/srv/sockets",
    ],
)
def test_other_unix_sockets_are_not_local(url: str) -> None:
    assert not migrations.is_local_database(url)


def test_a_socket_from_the_environment_counts_too(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PGHOST", "/cloudsql/project:region:production")
    assert not migrations.is_local_database("postgresql:///app")


# -- Migration runner --------------------------------------------------------------


@pytest.fixture
def scratch_url(db: str) -> Iterator[str]:
    """An empty database of its own: these tests apply their own migrations."""
    name = f"app_scratch_{uuid.uuid4().hex[:10]}"
    superuser_query(db, f'CREATE DATABASE "{name}"')
    yield urlunparse(urlparse(db)._replace(path=f"/{name}"))
    superuser_query(
        db,
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()",
        (name,),
    )
    superuser_query(db, f'DROP DATABASE IF EXISTS "{name}"')


def test_the_runner_works_through_a_transaction_mode_pooler(db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """No server-side prepared statements (a transaction-mode pooler does not support them), and a connect timeout."""
    seen: list[dict[str, Any]] = []
    connect = psycopg.connect

    def spy(*args: Any, **kwargs: Any) -> psycopg.Connection[Any]:
        conn = connect(*args, **kwargs)
        close = conn.close

        def inspect_then_close() -> None:
            if not conn.closed:
                prepared = conn.execute("SELECT count(*) FROM pg_prepared_statements").fetchone()
                seen.append({"prepared": prepared[0] if prepared else None, **conn.info.get_parameters()})
            close()

        conn.close = inspect_then_close  # type: ignore[method-assign]
        return conn

    monkeypatch.setattr(psycopg, "connect", spy)
    migrations.migrate(db)  # already applied: every migration repeats the same ledger queries
    assert len(migrations.discover()) >= 5  # enough repeats to reach psycopg's default prepare threshold
    assert [(s["prepared"], s["connect_timeout"]) for s in seen] == [(0, "10")]


def _write(directory: Path, name: str, sql: str) -> None:
    (directory / name).write_text(sql, encoding="utf-8")


def test_a_migration_waiting_for_a_table_lock_gives_up(
    scratch_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A migration blocked by running traffic fails after the lock timeout, instead of queueing
    every later query on the table behind its exclusive lock."""
    monkeypatch.setattr(migrations, "_LOCK_TIMEOUT", "200ms")
    _write(tmp_path, "1_probe.sql", "CREATE TABLE public.probe (id int);")
    migrations.migrate(scratch_url, tmp_path)
    _write(tmp_path, "2_alter.sql", "ALTER TABLE public.probe ADD COLUMN x int;")
    outcome: dict[str, BaseException] = {}

    def run() -> None:
        try:
            migrations.migrate(scratch_url, tmp_path)
        except BaseException as exc:  # noqa: BLE001 - reported to the test
            outcome["error"] = exc

    with psycopg.connect(scratch_url) as traffic:
        traffic.execute("SELECT * FROM public.probe")  # an open transaction holds a lock on the table
        runner = threading.Thread(target=run, daemon=True)
        runner.start()
        runner.join(3)
        finished = not runner.is_alive()
    runner.join(10)
    assert finished, "the migration waited for the lock past the lock timeout"
    assert isinstance(outcome.get("error"), psycopg.errors.LockNotAvailable), outcome
    applied = superuser_query(scratch_url, "SELECT version FROM public.app_schema_migrations ORDER BY version")
    assert applied == [("1",)]


def test_a_concurrent_run_still_waits_for_the_migration_lock(
    scratch_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The lock timeout covers migration statements only; a second run waits for the first as long as it takes."""
    monkeypatch.setattr(migrations, "_LOCK_TIMEOUT", "200ms")
    _write(tmp_path, "1_probe.sql", "CREATE TABLE public.probe (id int);")
    applied: list[list[str]] = []
    errors: list[BaseException] = []

    def run() -> None:
        try:
            applied.append(migrations.migrate(scratch_url, tmp_path))
        except BaseException as exc:  # noqa: BLE001 - reported to the test
            errors.append(exc)

    with psycopg.connect(scratch_url) as first_run:
        first_run.execute(migrations._LOCK)  # another run holds the migration lock
        runner = threading.Thread(target=run, daemon=True)
        runner.start()
        time.sleep(0.6)  # three times the lock timeout
        assert runner.is_alive()
    runner.join(10)
    assert errors == []
    assert applied == [["1_probe.sql"]]


def test_a_run_started_during_another_waits_at_every_migration(
    scratch_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second run waits for the lock at the ledger and again at each migration, which the runs
    take one at a time: no wait may be cut short by the lock timeout, and each migration is applied
    once, in order, by whichever run takes its lock first."""
    monkeypatch.setattr(migrations, "_LOCK_TIMEOUT", "200ms")
    _write(tmp_path, "1_slow.sql", "SELECT pg_sleep(1);")
    _write(tmp_path, "2_slow.sql", "SELECT pg_sleep(1);")
    outcome: dict[str, Any] = {}

    def run(name: str) -> None:
        try:
            outcome[name] = migrations.migrate(scratch_url, tmp_path)
        except BaseException as exc:  # noqa: BLE001 - reported to the test
            outcome[name] = exc

    first = threading.Thread(target=run, args=("first",), daemon=True)
    first.start()
    give_up = time.monotonic() + 10
    in_first_migration = (
        "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND state = 'active' AND query = %s"
    )
    while not superuser_query(scratch_url, in_first_migration, ("SELECT pg_sleep(1);",))[0][0]:
        assert time.monotonic() < give_up, "the first run never started its first migration"
        time.sleep(0.01)
    second = threading.Thread(target=run, args=("second",), daemon=True)
    second.start()
    first.join(15)
    second.join(15)
    assert set(outcome) == {"first", "second"}, "a run did not finish"
    assert not any(isinstance(result, BaseException) for result in outcome.values()), outcome
    assert outcome["first"][:1] == ["1_slow.sql"]  # it was inside that migration when the second run started
    assert outcome["first"] + outcome["second"] == ["1_slow.sql", "2_slow.sql"]
