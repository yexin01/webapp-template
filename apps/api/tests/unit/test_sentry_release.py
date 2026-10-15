"""API error events are tagged with their release (OBS-02)."""

from __future__ import annotations

from typing import Any

import pytest

from app.kernel.infrastructure.observability import sentry_options
from tests.conftest import make_settings

DSN = "https://public@o0.ingest.example/0"  # a placeholder; no client is created


def options(**overrides: Any) -> dict[str, Any]:
    return sentry_options(make_settings(error_reporting_mode="sandbox", sentry_dsn=DSN, **overrides))


@pytest.fixture(autouse=True)
def _no_release_in_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("SENTRY_RELEASE", "VERCEL_GIT_COMMIT_SHA"):
        monkeypatch.delenv(name, raising=False)


def test_events_carry_the_configured_release() -> None:
    assert options(sentry_release="2026.10.15-1")["release"] == "2026.10.15-1"


def test_on_vercel_the_release_is_the_deployed_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    """A deployed bundle has no git checkout, and sentry-sdk does not read Vercel's variables."""
    monkeypatch.setenv("VERCEL_GIT_COMMIT_SHA", "0123abcd")
    assert options()["release"] == "0123abcd"
    monkeypatch.setenv("SENTRY_RELEASE", "explicit")
    assert options()["release"] == "explicit"


def test_an_empty_sentry_release_still_falls_back_to_the_deployed_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    """``SENTRY_RELEASE=`` means unset, like every empty setting, so it does not hide Vercel's commit."""
    monkeypatch.setenv("SENTRY_RELEASE", "")
    monkeypatch.setenv("VERCEL_GIT_COMMIT_SHA", "0123abcd")
    assert options()["release"] == "0123abcd"


def test_without_a_release_sentry_sdk_applies_its_default() -> None:
    """``None`` lets sentry-sdk derive the release itself (SENTRY_RELEASE, git, CI variables)."""
    assert options()["release"] is None
