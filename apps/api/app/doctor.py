"""Setup checks that need no browser and no sign-in: ``app.cli check-config`` and ``app.cli diagnose``.

``check-config`` only loads the settings: no network, no database. ``diagnose`` runs the integration probes
the /integrations page runs, as a synthetic user, and prints an evidence block for ``dev-docs/evidence/``
(spec section 20). It refuses production, and sends nothing to PostHog or Sentry unless asked. Nothing here
prints a secret: settings errors and probe summaries name variables, never values.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import sys
import uuid
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

from app.bootstrap import Container, build_container
from app.kernel.diagnostics import (
    CheckResult,
    IntegrationInfo,
    IntegrationKey,
    IntegrationMode,
    IntegrationProbe,
    Outcome,
)
from app.kernel.infrastructure.config import REPO_ROOT, AppEnv, ConfigError
from app.kernel.infrastructure.integrations import describe_analytics, describe_database, describe_error_reporting
from app.kernel.infrastructure.observability import (
    NoopAnalytics,
    NoopErrorReporter,
    configure_logging,
    init_error_reporting,
)
from app.kernel.tenancy import ContextKind, TenantContext
from app.modules.billing.module import describe as describe_billing
from app.modules.diagnostics.service import run_probe
from app.modules.email.module import describe as describe_email
from app.modules.identity.module import clerk_not_configured
from app.modules.identity.module import describe as describe_identity
from app.modules.llm.module import describe as describe_llm
from app.settings import Settings, load_settings

# One user for every command-line run: no row in the database, no account at any provider.
CLI_CONTEXT = TenantContext(
    user_id=uuid.uuid5(uuid.NAMESPACE_URL, "app.cli diagnose"),
    external_user_id="user_cli_diagnose",
    kind=ContextKind.PERSONAL,
)

_TEST_EVENT_KEYS = (IntegrationKey.ANALYTICS, IntegrationKey.ERROR_REPORTING)
_LABELS = {
    Outcome.PASSED: "PASS",
    Outcome.FAILED: "FAIL",
    Outcome.SKIPPED: "SKIPPED",
    Outcome.SENT: "SENT",
}
_MODE_LABELS = {IntegrationMode.MOCK: "mocked"}


def _mode_label(mode: IntegrationMode) -> str:
    return _MODE_LABELS.get(mode, mode.value)


_NOT_SENT = "Not sent: run `pnpm check:setup --send-test-events` to send {what} to your {mode} project."
_IDENTITY_NOTE = (
    "The Clerk Backend API accepted the secret key for a membership read. The command line has no session, so "
    "no session token was verified: sign in and run the Clerk check on /integrations for that."
)
_PROBLEM_PREFIX = "Invalid configuration: "


def load(env_file: str | None) -> Settings:
    """The settings from the process environment and the repository ``.env``, or from ``env_file`` instead."""
    if env_file is None:
        return load_settings()
    if not Path(env_file).is_file():
        raise ConfigError(f"The env file {env_file} does not exist")
    return load_settings(_env_file=env_file)


def _problems(error: ConfigError) -> list[str]:
    """One entry per problem. The settings joins them with " | " or "; " before a variable name."""
    text = str(error).removeprefix(_PROBLEM_PREFIX)
    return [p for chunk in text.split(" | ") for p in re.split(r"; (?=[A-Z][A-Z0-9_]{2,}[ ,=])", chunk)]


def _report_config_error(error: ConfigError, err: TextIO) -> None:
    print("The configuration is not valid:", file=err)
    for problem in _problems(error):
        print(f"  - {problem}", file=err)
    print("Edit .env (see .env.example and docs/integrations.md), then run this again.", file=err)


def _infos(settings: Settings) -> list[IntegrationInfo]:
    return [
        describe_database(settings),
        describe_identity(settings),
        describe_billing(settings),
        describe_email(settings),
        describe_llm(settings),
        describe_analytics(settings),
        describe_error_reporting(settings),
    ]


def check_config(env_file: str | None, out: TextIO | None = None, err: TextIO | None = None) -> int:
    """Validate the settings and print the environment and each integration's mode. Exit status 1 on error."""
    out, err = out or sys.stdout, err or sys.stderr
    try:
        settings = load(env_file)
    except ConfigError as exc:
        _report_config_error(exc, err)
        return 1
    print(f"APP_ENV={settings.app_env.value}", file=out)
    for info in _infos(settings):
        note = f" - {info.details[0]}" if info.key is IntegrationKey.IDENTITY else ""
        print(f"{info.name:<14} {info.mode.value}{note}", file=out)
    if clerk_not_configured(settings):
        print(
            "Note: the Clerk keys are the placeholders from .env.example: sign-in is unavailable, and `pnpm dev` "
            "cannot load pages. Paste your Clerk development keys into .env (docs/integrations.md#clerk).",
            file=out,
        )
    return 0


def _cli_result(result: CheckResult) -> CheckResult:
    if result.key is IntegrationKey.IDENTITY and result.outcome is Outcome.PASSED:
        return CheckResult(result.key, result.mode, result.outcome, _IDENTITY_NOTE, result.latency_ms)
    return result


def run_checks(probes: Sequence[IntegrationProbe], send_test_events: bool) -> list[CheckResult]:
    """Run every probe in catalog order. PostHog and Sentry are run only when test events are wanted."""
    by_key = {p.info.key: p for p in probes}
    results: list[CheckResult] = []
    for key in IntegrationKey:
        probe = by_key.get(key)
        if probe is None:
            continue
        info = probe.info
        if (
            key in _TEST_EVENT_KEYS
            and not send_test_events
            and info.mode in (IntegrationMode.SANDBOX, IntegrationMode.LIVE)
        ):
            what = "one PostHog event" if key is IntegrationKey.ANALYTICS else "one Sentry issue"
            reason = _NOT_SENT.format(what=what, mode=info.mode.value)
            results.append(CheckResult(key, info.mode, Outcome.SKIPPED, reason))
            continue
        results.append(_cli_result(run_probe(probe, CLI_CONTEXT)))
    return results


def _name(key: IntegrationKey, infos: dict[IntegrationKey, IntegrationInfo]) -> str:
    return infos[key].name if key in infos else key.value


def _evidence_status(result: CheckResult) -> str:
    if result.outcome is Outcome.PASSED:
        return "PASS"
    return "FAIL" if result.outcome is Outcome.FAILED else "NOT VERIFIED"


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def evidence_block(
    settings: Settings,
    results: Sequence[CheckResult],
    infos: dict[IntegrationKey, IntegrationInfo],
    *,
    commit: str,
    today: str,
    send_test_events: bool,
) -> str:
    """A spec section 20 record, from actual results only. Mock and skipped integrations are NOT VERIFIED."""
    contacted = [
        _name(r.key, infos)
        for r in results
        if r.outcome is not Outcome.SKIPPED and r.key is not IntegrationKey.DATABASE
    ]
    events = (
        "one PostHog event, one Sentry issue and one email to delivered@resend.dev, for each of them in sandbox mode"
        if send_test_events
        else "none (run with --send-test-events to send them)"
    )
    command = "pnpm check:setup --send-test-events" if send_test_events else "pnpm check:setup"
    lines = [
        f"## Evidence: integration diagnostics ({today})",
        "",
        "| Field | Value |",
        "|---|---|",
        "| Requirements | DEC-006, TEST-04 (integration verification) |",
        f"| Date | {today} |",
        f"| Commit | {commit} |",
        f"| Environment | APP_ENV={settings.app_env.value} |",
        f"| Command | `{command}` (`python -m app.cli diagnose`) |",
        f"| Providers contacted | {', '.join(contacted) or 'none'} |",
        f"| Test events sent | {events} |",
        "",
        "| Integration | Integration mode | Outcome | Status | Result |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        result = _cell(r.summary + (f" Reference: {r.reference}." if r.reference else ""))
        outcome = "SENT (unconfirmed)" if r.outcome is Outcome.SENT else _LABELS[r.outcome]
        row = (_name(r.key, infos), _mode_label(r.mode), outcome, _evidence_status(r), result)
        lines.append("| " + " | ".join(row) + " |")
    lines += [
        "",
        "Limitations: `mocked` and disabled integrations were not contacted and stay NOT VERIFIED; "
        "a `sandbox` pass proves connectivity with test credentials only (TEST-04); `local` is a PostgreSQL "
        "server on this machine. Sentry's SDK does not confirm delivery, and the Clerk row verified no session "
        "token. The command ran as a synthetic user, so no tenant data was read or written. "
        "No secrets, tokens, or personal data are recorded here.",
    ]
    return "\n".join(lines)


def manual_steps(settings: Settings, results: Sequence[CheckResult], *, send_test_events: bool) -> list[str]:
    """What only a person can still do, from what was skipped or failed and from what no API can check."""
    by_key = {r.key: r for r in results}
    steps: list[str] = []

    def failed(key: IntegrationKey) -> bool:
        return key in by_key and by_key[key].outcome is Outcome.FAILED

    def mode(key: IntegrationKey) -> IntegrationMode | None:
        return by_key[key].mode if key in by_key else None

    if failed(IntegrationKey.DATABASE):
        steps.append(
            "Database: start it and apply the migrations (`pnpm db:up`, then `pnpm db:migrate`), "
            "or fix DATABASE_URL in .env."
        )
    if clerk_not_configured(settings):
        steps.append(
            "Clerk: create a free development instance at https://dashboard.clerk.com, enable Organizations with "
            "Membership optional (Personal accounts), and paste its publishable and secret keys into "
            "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY and CLERK_SECRET_KEY in .env (docs/setup.md#clerk)."
        )
    else:
        steps.append(
            "Clerk (not checkable by API): Organizations enabled with Membership optional (Personal accounts) in the "
            "dashboard (docs/setup.md#clerk), and CLERK_AUTHORIZED_PARTIES listing every origin that serves the web "
            "app. Then sign in and select 'Run all server checks' on /integrations, which also verifies session "
            "tokens."
        )
        if settings.clerk_webhook_signing_secret is None:
            steps.append(
                "Clerk membership webhook (optional, recommended): add the endpoint "
                "<your-api-host>/api/v1/webhooks/clerk in the Clerk dashboard and put its signing secret in "
                "CLERK_WEBHOOK_SIGNING_SECRET."
            )
    if mode(IntegrationKey.BILLING) is IntegrationMode.MOCK:
        steps.append(
            "Stripe: create a sandbox, put its sk_test_ key in STRIPE_SECRET_KEY, run "
            "`pnpm setup:stripe --currency usd --monthly 900 --yearly 9000`, and paste the lines it prints into .env "
            "(it sets BILLING_MODE=sandbox)."
        )
    elif mode(IntegrationKey.BILLING) is not None:
        steps.append(
            "Stripe (dashboard only): save the Customer Portal (payment methods, invoices, cancel at period end, "
            "switch between the two Pro prices), and under Billing > Revenue recovery > Retries choose to cancel or "
            "mark unpaid when all retries fail. Webhooks: `pnpm dev` forwards them through the Stripe CLI; a deployed "
            "environment needs the endpoint <your-api-host>/api/v1/webhooks/stripe (payload format Snapshot) and its "
            "own signing secret in STRIPE_WEBHOOK_SECRET."
        )
    if mode(IntegrationKey.LLM) is IntegrationMode.MOCK:
        steps.append(
            "LLM provider: set LLM_MODE=sandbox, LLM_PROVIDER, LLM_API_KEY and LLM_MODEL in .env, and set a spending "
            "limit at the provider."
        )
    elif mode(IntegrationKey.LLM) is not None:
        steps.append("LLM provider: set a spending limit at the provider (not checkable by API).")
    email_mode = mode(IntegrationKey.EMAIL)
    if email_mode is IntegrationMode.MOCK:
        steps.append(
            "Resend (email, optional): create an account at https://resend.com, add and verify your sending domain "
            "under Domains, create an API key, then set EMAIL_MODE=sandbox, RESEND_API_KEY and EMAIL_FROM in .env "
            "(docs/setup.md#resend)."
        )
    elif email_mode in (None, IntegrationMode.DISABLED):
        pass  # turned off on purpose: nothing to set up
    elif not send_test_events:
        steps.append(
            "Resend: run `pnpm check:setup --send-test-events` to send one email to delivered@resend.dev, then find "
            "it under Emails in the Resend dashboard."
        )
    else:
        steps.append(
            "Resend: find the test email under Emails in the Resend dashboard (its ID is the reference above)."
        )
    for key, name, what in (
        (IntegrationKey.ANALYTICS, "PostHog", "that integration_test_sent arrived under Activity"),
        (IntegrationKey.ERROR_REPORTING, "Sentry", "the event ID shown above in Issues"),
    ):
        key_mode = mode(key)
        if key_mode in (IntegrationMode.DISABLED, IntegrationMode.MOCK):
            steps.append(
                f"{name} (optional): set its mode to sandbox with its key or DSN in .env (docs/integrations.md)."
            )
        elif key_mode is not None and not send_test_events:
            steps.append(
                f"{name}: run `pnpm check:setup --send-test-events` to send the test event, then confirm {what}."
            )
        elif key_mode is not None:
            steps.append(f"{name}: confirm {what}, and that no emails, tokens, or query strings were captured.")
    if any(r.outcome is Outcome.FAILED for r in results):
        steps.append("Fix the FAIL lines above (each says why), then run `pnpm check:setup` again.")
    steps.append(
        "To keep this result as evidence (spec section 20), paste the block above into a new file under "
        "dev-docs/evidence/. Skipped and mocked lines stay NOT VERIFIED until they run in sandbox mode and pass."
    )
    return steps


def _commit(root: Path | None) -> str:
    git = shutil.which("git")
    if git is None or root is None:
        return "unknown"
    try:
        sha = subprocess.run(  # noqa: S603 - fixed arguments
            [git, "rev-parse", "--short", "HEAD"], cwd=root, capture_output=True, text=True, timeout=5, check=True
        ).stdout.strip()
        dirty = subprocess.run(  # noqa: S603 - fixed arguments
            [git, "status", "--porcelain"], cwd=root, capture_output=True, text=True, timeout=5, check=True
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return f"{sha} (uncommitted changes)" if dirty else sha


def run_diagnose(
    container: Container,
    *,
    send_test_events: bool,
    out: TextIO,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    commit: Callable[[], str] = lambda: _commit(REPO_ROOT),
) -> int:
    """Run the container's probes and print the results, an evidence block, and the manual steps still needed."""
    settings = container.settings
    with _quiet_logs():
        results = run_checks(container.probes, send_test_events)
    infos = {probe.info.key: probe.info for probe in container.probes}
    print(f"Integration checks, APP_ENV={settings.app_env.value}:\n", file=out)
    for r in results:
        print(f"  {_LABELS[r.outcome]:<8} {_name(r.key, infos):<14} {_mode_label(r.mode):<9} {r.summary}", file=out)
        if r.error_code:
            print(f"{'':<34}error code: {r.error_code}", file=out)
        if r.reference:
            print(f"{'':<34}reference: {r.reference}", file=out)
    print(file=out)
    print(
        evidence_block(
            settings,
            results,
            infos,
            commit=commit(),
            today=now().date().isoformat(),
            send_test_events=send_test_events,
        ),
        file=out,
    )
    print("\nManual steps still needed:", file=out)
    for number, step in enumerate(manual_steps(settings, results, send_test_events=send_test_events), start=1):
        print(f"  {number}. {step}", file=out)
    return 1 if any(r.outcome is Outcome.FAILED for r in results) else 0


@contextmanager
def _quiet_logs() -> Iterator[None]:
    """Log records of the probes' libraries (a failed connection names its host) stay out of the output."""
    root = logging.getLogger()
    handler = logging.NullHandler()
    root.addHandler(handler)
    try:
        yield
    finally:
        root.removeHandler(handler)


def diagnose(env_file: str | None, send_test_events: bool, out: TextIO | None = None, err: TextIO | None = None) -> int:
    """Load the settings, build the container, and run ``run_diagnose``.

    Exit status 0 when no check failed, 1 when one did or the configuration is invalid, 2 when refused.
    Without ``send_test_events`` the container gets no-op PostHog and Sentry adapters, so even the LLM check's
    own analytics event stays local, and the Resend check reads the sending domain without sending an email.
    """
    out, err = out or sys.stdout, err or sys.stderr
    try:
        settings = load(env_file)
    except ConfigError as exc:
        _report_config_error(exc, err)
        return 1
    if settings.app_env is AppEnv.PRODUCTION:
        print(
            "Refusing to run diagnostics with APP_ENV=production (ENV-01). Use the /integrations page there, "
            "and never run this against production credentials.",
            file=err,
        )
        return 2
    if send_test_events:
        configure_logging(settings.log_level)
        init_error_reporting(settings)
        container = build_container(settings)
    else:
        container = build_container(
            settings, analytics=NoopAnalytics(), error_reporter=NoopErrorReporter(), email_test_sends=False
        )
    try:
        return run_diagnose(container, send_test_events=send_test_events, out=out)
    finally:
        container.close()
