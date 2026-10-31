"""``app.cli check-config`` and the pure parts of ``app.cli diagnose`` (no database, no network)."""

from __future__ import annotations

import base64
import io
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app import cli, doctor
from app.kernel.diagnostics import (
    CheckResult,
    IntegrationInfo,
    IntegrationKey,
    IntegrationMode,
    Outcome,
    Verdict,
)
from app.kernel.errors import DependencyUnavailableError
from app.kernel.infrastructure.config import REPO_ROOT, AppEnv, ConfigError
from tests.conftest import make_settings

assert REPO_ROOT is not None
ENV_EXAMPLE = REPO_ROOT / ".env.example"
# Assembled at runtime so secret scanners do not flag them.
STRIPE_KEY = "sk_" + "test_" + "x" * 30
PUBLISHABLE = "pk_test_" + base64.b64encode(b"example.clerk.accounts.dev$").decode()


@pytest.fixture
def env_file(tmp_path: Path) -> Callable[[str], str]:
    def write(text: str) -> str:
        path = tmp_path / "env"
        path.write_text(text, encoding="utf-8")
        return str(path)

    return write


def example_with(env_file: Callable[[str], str], **replacements: str) -> str:
    """The shipped .env.example with some variables replaced."""
    lines = []
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        name = line.split("=", 1)[0]
        lines.append(f"{name}={replacements[name]}" if name in replacements and not line.startswith("#") else line)
    return env_file("\n".join(lines) + "\n")


def run(function: Callable[..., int], *args: Any) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    status = function(*args, out, err)
    return status, out.getvalue(), err.getvalue()


# --- check-config -----------------------------------------------------------------------------


def test_the_shipped_env_example_is_valid_and_reports_clerk_as_not_configured() -> None:
    status, out, err = run(doctor.check_config, str(ENV_EXAMPLE))
    assert (status, err) == (0, "")
    assert "APP_ENV=local" in out
    for name in ("PostgreSQL", "Clerk", "Stripe", "LLM provider", "PostHog", "Sentry"):
        assert name in out
    assert "Clerk          mock - Not configured" in out
    assert "placeholders" in out


def test_the_shipped_placeholder_pair_cannot_start_in_production(env_file: Callable[[str], str]) -> None:
    status, _, err = run(doctor.check_config, example_with(env_file, APP_ENV="production"))
    assert status == 1
    assert "  - NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY must be a live key when APP_ENV=production" in err
    assert "  - CLERK_SECRET_KEY must be a live key when APP_ENV=production" in err


def test_real_clerk_keys_are_not_reported_as_placeholders(env_file: Callable[[str], str]) -> None:
    path = example_with(
        env_file,
        NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=PUBLISHABLE,
        CLERK_SECRET_KEY="sk_" + "test_" + "c" * 30,
    )
    status, out, _ = run(doctor.check_config, path)
    assert status == 0
    assert "Clerk          sandbox" in out
    assert "placeholders" not in out


def test_the_old_placeholder_pair_is_refused_without_a_traceback(env_file: Callable[[str], str]) -> None:
    path = example_with(env_file, NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY="pk_test_replace_me")
    status, out, err = run(doctor.check_config, path)
    assert status == 1
    assert out == ""
    assert "  - NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY is still the .env.example placeholder" in err
    assert "Traceback" not in err
    assert "docs/integrations.md" in err


def test_each_problem_is_on_its_own_line_and_names_variables_only(env_file: Callable[[str], str]) -> None:
    path = example_with(env_file, BILLING_MODE="sandbox", STRIPE_SECRET_KEY="sk_" + "live_" + "z" * 30)
    status, _, err = run(doctor.check_config, path)
    assert status == 1
    problem_lines = [line for line in err.splitlines() if line.startswith("  - ")]
    for name in (
        "STRIPE_SECRET_KEY",
        "STRIPE_WEBHOOK_SECRET",
        "STRIPE_PRICE_PRO_MONTHLY",
        "STRIPE_PRICE_PRO_YEARLY",
        "STRIPE_PRODUCT_PRO",
    ):
        assert any(line.startswith(f"  - {name}") for line in problem_lines), name
    assert all("; " not in line.removeprefix("  - ").split(" is a live key")[0] for line in problem_lines)
    assert "z" * 30 not in err


def test_a_missing_env_file_is_reported(tmp_path: Path) -> None:
    status, _, err = run(doctor.check_config, str(tmp_path / "missing"))
    assert status == 1
    assert "does not exist" in err


def test_the_command_is_wired_into_the_cli(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["check-config", "--env-file", str(ENV_EXAMPLE)]) == 0
    assert "APP_ENV=local" in capsys.readouterr().out


def test_problems_split_on_variable_names_not_on_sentences() -> None:
    error = ConfigError(
        "Invalid configuration: A_VAR is a live key; live keys are only allowed when APP_ENV=production; "
        "B_VAR is required | C_VAR must be set"
    )
    assert doctor._problems(error) == [
        "A_VAR is a live key; live keys are only allowed when APP_ENV=production",
        "B_VAR is required",
        "C_VAR must be set",
    ]


# --- diagnose: refusal and checks ---------------------------------------------------------------


def test_diagnose_refuses_production_before_building_anything(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor, "load", lambda env_file: SimpleNamespace(app_env=AppEnv.PRODUCTION))

    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("a container was built for production")

    monkeypatch.setattr(doctor, "build_container", fail)
    out, err = io.StringIO(), io.StringIO()
    assert doctor.diagnose(None, False, out, err) == 2
    assert "APP_ENV=production" in err.getvalue()
    assert out.getvalue() == ""


def test_diagnose_reports_invalid_configuration_without_running_anything(env_file: Callable[[str], str]) -> None:
    out, err = io.StringIO(), io.StringIO()
    path = example_with(env_file, NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY="pk_test_replace_me")
    assert doctor.diagnose(path, False, out, err) == 1
    assert "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY" in err.getvalue()
    assert out.getvalue() == ""


class FakeProbe:
    def __init__(
        self,
        key: IntegrationKey,
        mode: IntegrationMode,
        verdict: Verdict | Exception | None = None,
    ) -> None:
        self.info = IntegrationInfo(key, key.value.title(), mode)
        self._verdict = verdict or Verdict(Outcome.PASSED, "Fine.")
        self.calls = 0

    def check(self, ctx: object) -> Verdict:
        self.calls += 1
        if isinstance(self._verdict, Exception):
            raise self._verdict
        return self._verdict


def sandbox_probes() -> dict[IntegrationKey, FakeProbe]:
    modes = {
        IntegrationKey.DATABASE: IntegrationMode.LOCAL,
        IntegrationKey.IDENTITY: IntegrationMode.SANDBOX,
        IntegrationKey.BILLING: IntegrationMode.SANDBOX,
        IntegrationKey.LLM: IntegrationMode.SANDBOX,
        IntegrationKey.ANALYTICS: IntegrationMode.SANDBOX,
        IntegrationKey.ERROR_REPORTING: IntegrationMode.SANDBOX,
    }
    probes = {key: FakeProbe(key, mode) for key, mode in modes.items()}
    probes[IntegrationKey.IDENTITY] = FakeProbe(
        IntegrationKey.IDENTITY,
        IntegrationMode.SANDBOX,
        Verdict(
            Outcome.PASSED, "Session token verified, and the Clerk Backend API returned 0 organization memberships."
        ),
    )
    probes[IntegrationKey.ERROR_REPORTING] = FakeProbe(
        IntegrationKey.ERROR_REPORTING, IntegrationMode.SANDBOX, Verdict(Outcome.SENT, "Queued.", reference="abc123")
    )
    return probes


def test_test_events_are_sent_only_when_asked() -> None:
    probes = sandbox_probes()
    results = doctor.run_checks(list(probes.values()), send_test_events=False)
    by_key = {r.key: r for r in results}
    assert probes[IntegrationKey.ANALYTICS].calls == probes[IntegrationKey.ERROR_REPORTING].calls == 0
    for key in (IntegrationKey.ANALYTICS, IntegrationKey.ERROR_REPORTING):
        assert by_key[key].outcome is Outcome.SKIPPED
        assert "--send-test-events" in by_key[key].summary
    assert all(probes[key].calls == 1 for key in probes if key not in doctor._TEST_EVENT_KEYS)

    results = doctor.run_checks(list(probes.values()), send_test_events=True)
    assert probes[IntegrationKey.ANALYTICS].calls == probes[IntegrationKey.ERROR_REPORTING].calls == 1
    assert {r.key: r.outcome for r in results}[IntegrationKey.ERROR_REPORTING] is Outcome.SENT


def test_results_follow_the_catalog_and_reuse_the_pages_skip_and_failure_wording() -> None:
    probes = sandbox_probes()
    probes[IntegrationKey.BILLING] = FakeProbe(IntegrationKey.BILLING, IntegrationMode.MOCK)
    probes[IntegrationKey.LLM] = FakeProbe(IntegrationKey.LLM, IntegrationMode.DISABLED)
    probes[IntegrationKey.DATABASE] = FakeProbe(
        IntegrationKey.DATABASE, IntegrationMode.LOCAL, DependencyUnavailableError()
    )
    results = doctor.run_checks(list(reversed(probes.values())), send_test_events=True)
    assert [r.key for r in results] == list(IntegrationKey)
    by_key = {r.key: r for r in results}
    assert by_key[IntegrationKey.BILLING].outcome is Outcome.SKIPPED
    assert by_key[IntegrationKey.BILLING].summary.startswith("Mock mode: no network call was made")
    assert probes[IntegrationKey.BILLING].calls == 0
    assert by_key[IntegrationKey.LLM].summary.startswith("Disabled: set its mode to sandbox")
    assert by_key[IntegrationKey.DATABASE].outcome is Outcome.FAILED
    assert by_key[IntegrationKey.DATABASE].error_code == "dependency_unavailable"


def test_the_clerk_pass_does_not_claim_a_verified_session_token() -> None:
    results = doctor.run_checks(list(sandbox_probes().values()), send_test_events=False)
    identity = next(r for r in results if r.key is IntegrationKey.IDENTITY)
    assert identity.outcome is Outcome.PASSED
    assert "no session token was verified" in identity.summary
    assert "Session token verified" not in identity.summary


# --- evidence and manual steps -------------------------------------------------------------------------


def results_of(*pairs: tuple[IntegrationKey, IntegrationMode, Outcome, str]) -> list[CheckResult]:
    return [CheckResult(key, mode, outcome, summary) for key, mode, outcome, summary in pairs]


def test_the_evidence_block_follows_spec_section_20_and_labels_by_mode() -> None:
    settings = make_settings()
    infos = {p.info.key: p.info for p in sandbox_probes().values()}
    results = results_of(
        (IntegrationKey.DATABASE, IntegrationMode.LOCAL, Outcome.PASSED, "Connected."),
        (IntegrationKey.BILLING, IntegrationMode.MOCK, Outcome.SKIPPED, "Mock mode: no network call was made."),
        (IntegrationKey.LLM, IntegrationMode.SANDBOX, Outcome.PASSED, "Accepted | the key."),
        (
            IntegrationKey.ERROR_REPORTING,
            IntegrationMode.SANDBOX,
            Outcome.FAILED,
            "Sentry did not queue the test error.",
        ),
    )
    block = doctor.evidence_block(
        settings, results, infos, commit="abc1234", today="2026-10-30", send_test_events=False
    )
    assert "| Date | 2026-10-30 |" in block
    assert "| Commit | abc1234 |" in block
    assert "| Environment | APP_ENV=test |" in block
    assert "| Test events sent | none" in block
    assert "| Billing | mocked | SKIPPED | NOT VERIFIED |" in block
    assert "| Database | local | PASS | PASS |" in block
    assert "| Llm | sandbox | PASS | PASS | Accepted \\| the key. |" in block
    assert "| Error_Reporting | sandbox | FAIL | FAIL |" in block
    assert "Providers contacted | Llm, Error_Reporting |" in block
    assert "| mocked |" in block
    assert "| mock |" not in block


def test_a_sent_test_event_is_never_a_pass() -> None:
    result = CheckResult(
        IntegrationKey.ERROR_REPORTING, IntegrationMode.SANDBOX, Outcome.SENT, "Queued.", reference="e1"
    )
    infos = {
        IntegrationKey.ERROR_REPORTING: IntegrationInfo(
            IntegrationKey.ERROR_REPORTING, "Sentry", IntegrationMode.SANDBOX
        )
    }
    block = doctor.evidence_block(make_settings(), [result], infos, commit="c", today="d", send_test_events=True)
    assert "| Sentry | sandbox | SENT (unconfirmed) | NOT VERIFIED | Queued. Reference: e1. |" in block
    assert "one PostHog event and one Sentry issue" in block


def steps_for(settings: Any, results: list[CheckResult], send: bool = False) -> str:
    return "\n".join(doctor.manual_steps(settings, results, send_test_events=send))


def test_manual_steps_follow_what_was_skipped_or_failed() -> None:
    placeholder = make_settings(
        app_env="local",
        clerk_publishable_key="pk_test_" + base64.b64encode(b"clerk.not-configured-yet.invalid$").decode(),
        clerk_secret_key="sk_test_clerk_not_configured_yet",
        clerk_authorized_parties="http://localhost:3000",
    )
    results = results_of(
        (IntegrationKey.DATABASE, IntegrationMode.LOCAL, Outcome.FAILED, "x"),
        (IntegrationKey.IDENTITY, IntegrationMode.MOCK, Outcome.SKIPPED, "x"),
        (IntegrationKey.BILLING, IntegrationMode.MOCK, Outcome.SKIPPED, "x"),
        (IntegrationKey.LLM, IntegrationMode.MOCK, Outcome.SKIPPED, "x"),
        (IntegrationKey.ANALYTICS, IntegrationMode.DISABLED, Outcome.SKIPPED, "x"),
        (IntegrationKey.ERROR_REPORTING, IntegrationMode.DISABLED, Outcome.SKIPPED, "x"),
    )
    steps = steps_for(placeholder, results)
    for expected in (
        "pnpm db:up",
        "Clerk: create a free development instance",
        "pnpm setup:stripe",
        "LLM_MODE=sandbox",
        "PostHog (optional)",
        "Sentry (optional)",
        "Fix the FAIL lines",
    ):
        assert expected in steps, expected
    assert "Organizations enabled" not in steps


def test_manual_steps_name_what_no_api_can_check_once_keys_are_in() -> None:
    settings = make_settings(
        app_env="local",
        clerk_publishable_key=PUBLISHABLE,
        clerk_secret_key="sk_test_" + "c" * 30,
        clerk_authorized_parties="http://localhost:3000",
    )
    results = results_of(
        (IntegrationKey.IDENTITY, IntegrationMode.SANDBOX, Outcome.PASSED, "x"),
        (IntegrationKey.BILLING, IntegrationMode.SANDBOX, Outcome.PASSED, "x"),
        (IntegrationKey.LLM, IntegrationMode.SANDBOX, Outcome.PASSED, "x"),
        (IntegrationKey.ANALYTICS, IntegrationMode.SANDBOX, Outcome.SKIPPED, "x"),
        (IntegrationKey.ERROR_REPORTING, IntegrationMode.SANDBOX, Outcome.SKIPPED, "x"),
    )
    steps = steps_for(settings, results)
    assert "Organizations enabled" in steps
    assert "Clerk membership webhook" in steps
    assert "Customer Portal" in steps
    assert "api/v1/webhooks/stripe" in steps
    assert "spending limit" in steps
    assert "pnpm check:setup --send-test-events" in steps
    assert "Fix the FAIL" not in steps
    assert "pnpm check:setup --send-test-events" not in steps_for(settings, results, send=True)
    assert "no emails, tokens, or query strings" in steps_for(settings, results, send=True)
