"""The owner's connection URL, where the runtime login's URL goes, and which environments need the restricted
login role (DEC-008)."""

from __future__ import annotations

from typing import Any

import pytest

from app import bootstrap
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


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("postgresql://postgres:postgres@127.0.0.1:5432/app", "postgresql://app_login:<password>@127.0.0.1:5432/app"),
        # Supabase's pooler routes by "<role>.<project ref>".
        (
            "postgresql://postgres.abcdefgh:p%40ss@aws-0-eu-west-1.pooler.supabase.com:5432/postgres",
            "postgresql://app_login.abcdefgh:<password>@aws-0-eu-west-1.pooler.supabase.com:5432/postgres",
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
