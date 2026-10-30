"""What the diagnostics page reports about the kernel's integrations (DEC-006 evidence labels)."""

from __future__ import annotations

import pytest

from app.kernel.diagnostics import IntegrationMode
from app.kernel.infrastructure.config import CoreSettings
from app.kernel.infrastructure.integrations import describe_database

LOCAL = (IntegrationMode.LOCAL, "Local server")
HOSTED = (IntegrationMode.SANDBOX, "Hosted server")


@pytest.fixture(autouse=True)
def _no_libpq_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("PGHOST", "PGHOSTADDR", "PGSERVICE"):
        monkeypatch.delenv(name, raising=False)


def describe(url: str, app_env: str = "preview") -> tuple[IntegrationMode, str]:
    info = describe_database(CoreSettings(_env_file=None, app_env=app_env, database_url=url))
    return info.mode, info.details[0]


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("postgresql://u:p@localhost:5432/app", LOCAL),
        ("postgresql://u:p@127.0.0.1:5432/app", LOCAL),
        ("postgresql://u:p@host.docker.internal:5432/app", LOCAL),
        ("postgresql:///app?host=/var/run/postgresql", LOCAL),
        ("postgresql://u:p@db.example.supabase.co:6543/postgres", HOSTED),
        # Where libpq actually connects, which the URL's host alone does not show:
        ("host=db.example.supabase.co port=6543 dbname=postgres user=u", HOSTED),
        ("postgresql://u:p@localhost:6543/postgres?host=pooler.example.com", HOSTED),
        ("postgresql://u:p@localhost:5432/app?hostaddr=203.0.113.7", HOSTED),
        ("postgresql:///app?host=/cloudsql/project:region:instance", HOSTED),
    ],
)
def test_database_mode_follows_where_libpq_connects(url: str, expected: tuple[IntegrationMode, str]) -> None:
    assert describe(url) == expected


def test_a_service_file_or_host_variable_is_not_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PGSERVICE", "hosted")
    assert describe("postgresql://u:p@localhost/app") == HOSTED
    monkeypatch.delenv("PGSERVICE")
    monkeypatch.setenv("PGHOST", "db.example.supabase.co")
    assert describe("dbname=app user=u") == HOSTED


def test_a_hosted_database_in_production_is_live() -> None:
    assert describe("host=db.example.supabase.co dbname=postgres", app_env="production") == (
        IntegrationMode.LIVE,
        "Hosted server",
    )
