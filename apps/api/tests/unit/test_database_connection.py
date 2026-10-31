"""The owner's connection URL, where the runtime login's URL goes, and which environments need the restricted
login role (DEC-008)."""

from __future__ import annotations

from typing import Any

import pytest

from app import bootstrap, cli
from app.kernel.infrastructure import migrations
from app.kernel.infrastructure.config import (
    AppEnv,
    ConfigError,
    DatabaseSettings,
    load_migration_database_url,
    requires_restricted_login_role,
)
from tests.conftest import make_settings

REMOTE = "postgresql://postgres.abcdefgh:p%40ss@aws-0-eu-west-1.pooler.supabase.com:6543/postgres"


@pytest.fixture(autouse=True)
def _no_connection_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(DatabaseSettings.model_config, "env_file", None)  # never the developer's .env


# -- The owner's connection ---------------------------------------------------------


def test_the_owner_connection_is_the_migration_url_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://app_login:x@127.0.0.1:1/app")
    monkeypatch.setenv("MIGRATION_DATABASE_URL", "postgresql://owner:y@127.0.0.1:2/app")
    assert load_migration_database_url() == "postgresql://owner:y@127.0.0.1:2/app"


@pytest.mark.parametrize("value", [None, "", "   "])
def test_the_owner_connection_falls_back_to_database_url(monkeypatch: pytest.MonkeyPatch, value: str | None) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://owner:y@127.0.0.1:2/app")
    if value is None:
        monkeypatch.delenv("MIGRATION_DATABASE_URL", raising=False)
    else:
        monkeypatch.setenv("MIGRATION_DATABASE_URL", value)
    assert load_migration_database_url() == "postgresql://owner:y@127.0.0.1:2/app"


def test_an_invalid_migration_url_is_refused_without_echoing_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://owner:y@127.0.0.1:2/app")
    monkeypatch.setenv("MIGRATION_DATABASE_URL", "postgresql://owner:pass/word-secret@127.0.0.1:2/app")
    with pytest.raises(ConfigError, match="MIGRATION_DATABASE_URL is not a valid") as raised:
        load_migration_database_url()
    assert "word-secret" not in str(raised.value)


def test_nothing_set_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("MIGRATION_DATABASE_URL", raising=False)
    with pytest.raises(ConfigError, match="DATABASE_URL is required"):
        load_migration_database_url()


def test_the_running_api_never_reads_the_migration_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIGRATION_DATABASE_URL", "postgresql://owner:y@127.0.0.1:2/app")
    settings = make_settings(database_url="postgresql://app_login:x@127.0.0.1:1/app")
    assert not hasattr(settings, "migration_database_url")


# -- Where the runtime URL goes -----------------------------------------------------


POOLER_RUNTIME_URL = "postgresql://app_login.abcdefgh:<password>@aws-0-eu-west-1.pooler.supabase.com:6543/postgres"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("postgresql://postgres:postgres@127.0.0.1:5432/app", "postgresql://app_login:<password>@127.0.0.1:5432/app"),
        # Supabase's pooler routes by "<role>.<project ref>", and the running API needs its transaction mode
        # (port 6543), whichever mode or port the owner connected with: the session mode (5432) pins a server
        # connection to every client and runs out of them under concurrency.
        (
            "postgresql://postgres.abcdefgh:p%40ss@aws-0-eu-west-1.pooler.supabase.com:5432/postgres",
            POOLER_RUNTIME_URL,
        ),
        (REMOTE, POOLER_RUNTIME_URL),
        (
            "postgresql://postgres.abcdefgh:p%40ss@aws-0-eu-west-1.pooler.supabase.com/postgres",
            POOLER_RUNTIME_URL,
        ),
        # Other servers keep the port they were given, or none.
        (
            "postgresql://postgres:x@db.abcdefgh.supabase.co:5432/postgres",
            "postgresql://app_login:<password>@db.abcdefgh.supabase.co:5432/postgres",
        ),
        (
            "postgresql://my.owner:x@db.example.com:6432/app",
            "postgresql://app_login:<password>@db.example.com:6432/app",
        ),
        # A dot in a plain server's role name says nothing about a project.
        ("postgresql://my.owner:x@db.example.com/app", "postgresql://app_login:<password>@db.example.com/app"),
        ("postgresql:///app?host=/var/run/postgresql", None),
        ("postgresql://u:p@a.example.com,b.example.com/app", None),
    ],
)
def test_the_runtime_url_template(url: str, expected: str | None) -> None:
    assert migrations.runtime_url_template(url) == expected


def test_the_template_never_carries_the_owners_password() -> None:
    template = migrations.runtime_url_template(REMOTE)
    assert template is not None
    assert "p%40ss" not in template
    assert "p@ss" not in template


@pytest.mark.parametrize(
    ("owner_url", "runtime_url", "names_the_pooler"),
    [
        (REMOTE.replace(":6543/", ":5432/"), POOLER_RUNTIME_URL, True),
        (
            "postgresql://owner:x@db.example.com:5432/app",
            "postgresql://app_login:<password>@db.example.com:5432/app",
            False,
        ),
    ],
    ids=["Supabase session pooler", "other server"],
)
def test_api_login_says_which_url_the_api_needs(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    owner_url: str,
    runtime_url: str,
    names_the_pooler: bool,
) -> None:
    """The owner runs it with the session pooler string; the line it prints must send the API to port 6543."""
    monkeypatch.setenv("MIGRATION_DATABASE_URL", owner_url)
    monkeypatch.setenv("API_DB_PASSWORD", "a-new-password-of-length")
    monkeypatch.setattr(migrations, "enable_api_login", lambda *args, **kwargs: None)  # there is no server here
    assert cli.main(["api-login", "--allow-remote"]) == 0
    hint = capsys.readouterr().err
    assert f"set DATABASE_URL to {runtime_url} " in hint
    assert ("Transaction pooler" in hint) is names_the_pooler
    assert "a-new-password" not in hint
    assert "p%40ss" not in hint


# -- Which environments need the restricted login ----------------------------------


@pytest.mark.parametrize(
    ("app_env", "restricted"),
    [(AppEnv.LOCAL, False), (AppEnv.TEST, False), (AppEnv.PREVIEW, True), (AppEnv.PRODUCTION, True)],
)
def test_preview_and_production_need_the_restricted_login(app_env: AppEnv, restricted: bool) -> None:
    assert requires_restricted_login_role(app_env) is restricted


@pytest.mark.parametrize(
    ("app_env", "enforced"),
    [(AppEnv.TEST, False), (AppEnv.LOCAL, False), (AppEnv.PREVIEW, True), (AppEnv.PRODUCTION, True)],
)
def test_the_composition_root_enforces_the_login_role_by_environment(app_env: AppEnv, enforced: bool) -> None:
    # Copied, not validated: preview and production need provider keys that have nothing to do with this.
    database: Any = bootstrap._database(make_settings().model_copy(update={"app_env": app_env}))
    try:
        assert database._enforce_login_role is enforced
    finally:
        database.close()
