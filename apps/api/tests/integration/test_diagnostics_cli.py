"""``app.cli diagnose``: the /integrations checks without a sign-in, against the test database and fake providers."""

from __future__ import annotations

import io
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from app import cli, doctor
from app.bootstrap import build_container
from app.kernel.infrastructure.config import REPO_ROOT
from app.kernel.infrastructure.observability import NoopAnalytics, NoopErrorReporter
from tests.conftest import PRICE_MONTHLY, PRICE_YEARLY, Harness
from tests.integration.test_diagnostics import SANDBOX, FakeErrorReporter, _portal, _price

pytestmark = pytest.mark.db

assert REPO_ROOT is not None
ENV_EXAMPLE = REPO_ROOT / ".env.example"
FIXED = {"commit": lambda: "abc1234"}


def local_env(tmp_path: Path, database_url: str) -> str:
    """The shipped .env.example, pointed at the test database."""
    lines = []
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        lines.append(f"DATABASE_URL={database_url}" if line.startswith("DATABASE_URL=") else line)
    path = tmp_path / "env"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


@pytest.fixture
def sandbox_container(make_harness: Callable[..., Harness]) -> Callable[..., Harness]:
    def factory(settings: dict[str, Any] | None = None, **overrides: Any) -> Harness:
        overrides.setdefault("error_reporter", FakeErrorReporter())
        h = make_harness({**SANDBOX, **(settings or {})}, **overrides)
        h.billing.set_price(_price(PRICE_MONTHLY, "month"))
        h.billing.set_price(_price(PRICE_YEARLY, "year"))
        h.billing.set_portal(_portal())
        return h

    return factory


def test_the_shipped_defaults_pass_the_database_and_skip_everything_else(
    tmp_path: Path, db: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["diagnose", "--env-file", local_env(tmp_path, db)]) == 0
    out = capsys.readouterr().out
    assert "PASS     PostgreSQL" in out
    for name in ("Clerk", "Stripe", "LLM provider", "PostHog", "Sentry"):
        assert f"SKIPPED  {name}" in out
    assert "| PostgreSQL | local | PASS | PASS |" in out
    assert "| Stripe | mocked | SKIPPED | NOT VERIFIED |" in out
    assert "Manual steps still needed:" in out
    assert "To keep this result as evidence" in out


def test_without_the_flag_no_analytics_or_error_reporting_adapter_is_built(
    tmp_path: Path, db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    built: list[dict[str, Any]] = []

    def spy(settings: Any, **adapters: Any) -> Any:
        built.append(adapters)
        return build_container(settings, **adapters)

    monkeypatch.setattr(doctor, "build_container", spy)
    path = local_env(tmp_path, db)
    assert doctor.diagnose(path, False, io.StringIO(), io.StringIO()) == 0
    assert isinstance(built[0]["analytics"], NoopAnalytics)
    assert isinstance(built[0]["error_reporter"], NoopErrorReporter)


def test_sandbox_checks_pass_and_test_events_wait_for_the_flag(sandbox_container: Callable[..., Harness]) -> None:
    h = sandbox_container()
    out = io.StringIO()
    assert doctor.run_diagnose(h.container, send_test_events=False, out=out, **FIXED) == 0
    text = out.getvalue()
    for line in ("PASS     PostgreSQL", "PASS     Clerk", "PASS     Stripe", "PASS     LLM provider"):
        assert line in text, line
    assert "SKIPPED  PostHog" in text
    assert "SKIPPED  Sentry" in text
    assert "no session token was verified" in text
    assert "| Stripe | sandbox | PASS | PASS |" in text
    assert "| Commit | abc1234 |" in text
    assert h.container.kernel.error_reporter.sent == 0  # type: ignore[attr-defined]
    assert not [e for e in h.analytics.events if e[1] == "integration_test_sent"]


def test_with_the_flag_one_posthog_event_and_one_sentry_issue_are_sent(
    sandbox_container: Callable[..., Harness],
) -> None:
    h = sandbox_container()
    out = io.StringIO()
    assert doctor.run_diagnose(h.container, send_test_events=True, out=out, **FIXED) == 0
    text = out.getvalue()
    assert "PASS     PostHog" in text
    assert "SENT     Sentry" in text
    assert "| Sentry | sandbox | SENT (unconfirmed) | NOT VERIFIED |" in text
    assert "reference: 0123456789abcdef0123456789abcdef" in text
    assert h.container.kernel.error_reporter.sent == 1  # type: ignore[attr-defined]
    assert [e[1] for e in h.analytics.events].count("integration_test_sent") == 1


def test_a_failed_check_is_reported_and_fails_the_run(sandbox_container: Callable[..., Harness]) -> None:
    h = sandbox_container()
    h.billing.set_price(_price(PRICE_MONTHLY, "year"))  # bills yearly, expected monthly
    out = io.StringIO()
    assert doctor.run_diagnose(h.container, send_test_events=False, out=out, **FIXED) == 1
    text = out.getvalue()
    assert "FAIL     Stripe" in text
    assert "error code: price_mismatch" in text
    assert "Fix the FAIL lines above" in text


def test_nothing_secret_reaches_the_output(sandbox_container: Callable[..., Harness]) -> None:
    h = sandbox_container()
    out = io.StringIO()
    doctor.run_diagnose(h.container, send_test_events=True, out=out, **FIXED)
    for name in ("clerk_secret_key", "clerk_webhook_signing_secret", "stripe_secret_key", "llm_api_key"):
        assert SANDBOX[name] not in out.getvalue(), name
    assert SANDBOX["sentry_dsn"] not in out.getvalue()
