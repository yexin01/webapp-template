"""A database that cannot be reached is one line with its address and the fix, not a traceback (first hour).

``pnpm db:migrate`` and the other commands that open a connection are the first thing a new user runs, often
before ``pnpm db:up``. The line names the host and port and never the credentials of the URL.
"""

from __future__ import annotations

from typing import Any

import psycopg
import pytest

from app import cli
from app.bootstrap import build_container
from app.kernel.errors import DependencyUnavailableError
from app.kernel.infrastructure.config import DatabaseSettings
from app.modules.billing.adapters.fakes import InMemoryBillingProvider
from tests.conftest import make_settings

SECRET = "not-a-real-password-just-a-marker"
URL = f"postgresql://someuser:{SECRET}@127.0.0.1:1/none"  # nothing listens on port 1: the connection is refused
SCHEMA_OWNER_LINE = (
    "Cannot connect to the database at 127.0.0.1:1. Start the local one with `pnpm db:up`; "
    "otherwise check MIGRATION_DATABASE_URL or DATABASE_URL in .env."
)
RUNTIME_LINE = (
    "Cannot connect to the database at 127.0.0.1:1. Start the local one with `pnpm db:up`; "
    "otherwise check DATABASE_URL in .env."
)


@pytest.fixture(autouse=True)
def unreachable_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(DatabaseSettings.model_config, "env_file", None)  # never the developer's .env
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.delenv("MIGRATION_DATABASE_URL", raising=False)
    monkeypatch.setenv("API_DB_PASSWORD", "x" * 20)

    def container() -> Any:  # reconcile and job build the whole application; its database is the unreachable one too
        return build_container(make_settings(database_url=URL), billing_provider=InMemoryBillingProvider())

    monkeypatch.setattr("app.bootstrap.bootstrap", container)


def one_line(capsys: pytest.CaptureFixture[str]) -> str:
    err = capsys.readouterr().err
    assert SECRET not in err
    assert "someuser" not in err
    lines = [line for line in err.splitlines() if line.strip()]
    assert len(lines) == 1, err
    return lines[0]


@pytest.mark.parametrize("argv", [["migrate"], ["seed", "--yes"], ["reset", "--yes"], ["api-login"]])
def test_the_commands_that_connect_as_the_schema_owner_say_where_and_what_to_do(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(argv) == 1
    assert one_line(capsys) == SCHEMA_OWNER_LINE


@pytest.mark.parametrize("argv", [["reconcile"], ["job", "prune-expired"]])
def test_the_commands_that_connect_as_the_api_say_where_and_what_to_do(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(argv) == 1
    assert one_line(capsys) == RUNTIME_LINE


def test_a_url_that_cannot_be_read_still_gets_the_line_without_an_address(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def refused(_: Any) -> int:
        raise psycopg.OperationalError("connection failed")

    monkeypatch.setattr(cli, "cmd_migrate", refused)
    monkeypatch.setenv("DATABASE_URL", "not a url")
    assert cli.main(["migrate"]) == 1
    assert one_line(capsys).startswith("Cannot connect to the database. Start the local one with `pnpm db:up`")


def test_an_error_the_server_answered_keeps_its_traceback(monkeypatch: pytest.MonkeyPatch) -> None:
    """A lock timeout or a deadlock is not a missing connection, and the line would say it is."""

    def timed_out(_: Any) -> int:
        raise psycopg.errors.LockNotAvailable("canceling statement due to lock timeout")

    monkeypatch.setattr(cli, "cmd_migrate", timed_out)
    with pytest.raises(psycopg.errors.LockNotAvailable):
        cli.main(["migrate"])


def test_another_unavailable_service_keeps_its_traceback(monkeypatch: pytest.MonkeyPatch) -> None:
    def stripe_down(_: Any) -> int:
        raise DependencyUnavailableError("Stripe is temporarily unavailable.")

    monkeypatch.setattr(cli, "cmd_reconcile", stripe_down)
    with pytest.raises(DependencyUnavailableError):
        cli.main(["reconcile"])
