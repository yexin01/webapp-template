"""Shared test fixtures.

Safety rules enforced here:
* Configuration never comes from the developer's ``.env`` or shell: known
  variables are removed and settings are built with ``_env_file=None``.
* Database tests run only against a local PostgreSQL server, in a freshly
  created database that is dropped at the end of the session (DB-08).
* No test contacts a real external service; integrations are mocked.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse, urlunparse

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql

from app.bootstrap import Container, build_container, create_app
from app.kernel.errors import AuthenticationError
from app.kernel.infrastructure import migrations
from app.kernel.infrastructure.observability import AnalyticsEvents
from app.kernel.tenancy import VerifiedIdentity
from app.modules.billing.adapters.fakes import InMemoryBillingProvider
from app.modules.billing.adapters.stripe import StripeWebhookVerifier
from app.modules.identity.adapters.fakes import StaticMembershipDirectory
from app.modules.llm.adapters import MockLLMProvider
from app.settings import Settings, load_settings
from tests.openapi_contract import RECORDS, Contract, Recorder

DEFAULT_TEST_DATABASE_URL = "postgresql://postgres:postgres@127.0.0.1:54329/app"
TEST_STRIPE_WEBHOOK_SECRET = "whsec_test_only_not_a_real_secret_000000"
PRICE_MONTHLY = "price_test_monthly"
PRICE_YEARLY = "price_test_yearly"

_ENV_PREFIXES = (
    "APP_",
    "DATABASE_",
    "CLERK_",
    "NEXT_PUBLIC_",
    "STRIPE_",
    "BILLING_",
    "LLM_",
    "ANALYTICS_",
    "POSTHOG_",
    "ERROR_REPORTING_",
    "SENTRY_",
    "VERCEL_GIT_COMMIT_SHA",
    "VERCEL_ENV",
    "VERCEL_URL",
    "VERCEL_BRANCH_URL",
    "MEMBERSHIP_",
    "MIGRATION_",
    "CHECKOUTS_",
    "PORTAL_",
    "MAX_REQUEST_",
    "LOG_LEVEL",
    "DIAGNOSTICS_",
    "CRON_",
    "OUTBOX_",
)


def _is_settings_variable(name: str) -> bool:
    # The settings classes match environment names in any case (case_sensitive=False).
    return name.upper().startswith(_ENV_PREFIXES)


@pytest.fixture(autouse=True, scope="session")
def _isolate_environment() -> Iterator[None]:
    saved = {k: v for k, v in os.environ.items() if _is_settings_variable(k)}
    for key in saved:
        del os.environ[key]
    yield
    os.environ.update(saved)


# ---------------------------------------------------------------------------
# Clock and identity fakes (test code only; never shipped in the application)
# ---------------------------------------------------------------------------


@dataclass
class FrozenClock:
    current: datetime = field(default_factory=lambda: datetime(2026, 6, 1, 12, 0, tzinfo=UTC))

    def now(self) -> datetime:
        return self.current

    def advance(self, **kwargs: float) -> None:
        self.current += timedelta(**kwargs)


class TokenTableVerifier:
    """Maps opaque test tokens to identities. Unknown tokens are rejected."""

    def __init__(self) -> None:
        self.tokens: dict[str, str] = {}
        self.actors: dict[str, str] = {}

    def issue(self, external_user_id: str, actor: str | None = None) -> str:
        """A token for the user; with ``actor``, a session someone else started as that user."""
        token = f"test-token-{uuid.uuid4().hex}"
        self.tokens[token] = external_user_id
        if actor is not None:
            self.actors[token] = actor
        return token

    def revoke(self, token: str) -> None:
        self.tokens.pop(token, None)

    def verify(self, token: str) -> VerifiedIdentity:
        user = self.tokens.get(token)
        if user is None:
            raise AuthenticationError()
        return VerifiedIdentity(external_user_id=user, actor_external_id=self.actors.get(token))


class RecordingAnalytics:
    def __init__(self) -> None:
        self.allowed: AnalyticsEvents | None = None  # the container's, once built: events modules register count too
        self.events: list[tuple[str, str, dict[str, Any]]] = []
        # The (event_id, occurred_at) each recorded event carried, in the order of ``events``.
        self.identities: list[tuple[uuid.UUID | None, datetime | None]] = []
        self.export_ok = True  # False: flush() reports a failed export

    def capture(
        self,
        user_id: uuid.UUID,
        event: str,
        properties: Any,
        *,
        event_id: uuid.UUID | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        from app.kernel.infrastructure.observability import _filter

        filtered = _filter(event, properties, self.allowed) if self.allowed else _filter(event, properties)
        if filtered is not None:
            self.events.append((str(user_id), event, filtered))
            self.identities.append((event_id, occurred_at))

    def flush(self) -> bool:
        return self.export_ok

    def shutdown(self) -> None:
        return None


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


def _admin_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL)
    migrations.require_local_database(url, "run tests")
    return url


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--require-db",
        action="store_true",
        help="fail, instead of skipping the database tests, when PostgreSQL is unreachable (pnpm test, pnpm verify)",
    )


@pytest.fixture(autouse=True)
def _contract_exempt(request: pytest.FixtureRequest) -> Iterator[None]:
    Recorder.paused = request.node.get_closest_marker("contract_exempt") is not None
    yield
    Recorder.paused = False


# Every response the harness's apps sent during the run is checked against packages/contracts/openapi.json
# (DEC-007) when the session ends: a status the document does not declare, another content type, or a body
# that does not validate fails the run. tests/integration/test_contract_conformance.py drives every operation.
CONTRACT_PROBLEMS = pytest.StashKey[list[str]]()


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    problems = Contract().check(RECORDS) if RECORDS else []
    session.config.stash[CONTRACT_PROBLEMS] = problems
    if problems and session.exitstatus == pytest.ExitCode.OK:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_terminal_summary(terminalreporter: Any, exitstatus: int, config: pytest.Config) -> None:
    problems = config.stash.get(CONTRACT_PROBLEMS, [])
    if problems:
        terminalreporter.write_sep(
            "=", f"{len(problems)} responses differ from packages/contracts/openapi.json", red=True
        )
        for problem in problems:
            terminalreporter.write_line(problem)


def _database_required(config: pytest.Config) -> bool:
    """True in CI, when TEST_DATABASE_URL is set, or with --require-db: database tests must run, not skip."""
    ci = os.environ.get("CI", "").strip().lower() not in ("", "0", "false")
    return ci or bool(os.environ.get("TEST_DATABASE_URL")) or bool(config.getoption("--require-db"))


@pytest.fixture(scope="session")
def database_url(request: pytest.FixtureRequest) -> Iterator[str]:
    admin = _admin_url()
    name = f"app_test_{uuid.uuid4().hex[:10]}"
    try:
        with psycopg.connect(admin, autocommit=True, connect_timeout=3) as conn:
            conn.execute(f'CREATE DATABASE "{name}"')
    except psycopg.OperationalError as error:
        # Skipping is a local convenience only: in CI, with a configured database, or with --require-db,
        # an unreachable server must end the run, once, rather than let every database test skip (or fail).
        if _database_required(request.config):
            where = urlparse(admin)
            pytest.exit(
                f"PostgreSQL is not reachable for tests at {where.hostname}:{where.port or 5432}: {error}\n"
                "The database tests are required here (CI, TEST_DATABASE_URL, or --require-db). "
                "Start the local one with `pnpm db:up` (Docker), or set TEST_DATABASE_URL to a local server.",
                returncode=2,
            )
        pytest.skip("PostgreSQL is not available; start it with `pnpm db:up`.")
    parsed = urlparse(admin)
    url = urlunparse(parsed._replace(path=f"/{name}"))
    migrations.migrate(url)
    yield url
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()",
            (name,),
        )
        conn.execute(f'DROP DATABASE IF EXISTS "{name}"')


@pytest.fixture
def db(database_url: str) -> Iterator[str]:
    yield database_url
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute(
            "TRUNCATE app.users, app.organizations, app.organization_memberships, app.projects, "
            "app.billing_customers, app.subscriptions, app.billing_events, app.identity_events, "
            "app.audit_log, app.rate_limit_counters, app.outbox RESTART IDENTITY CASCADE"
        )


def superuser_query(url: str, sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    """Run ``sql`` as the test database's administrator: a superuser on plain PostgreSQL, and the non-superuser
    ``postgres`` (CREATEROLE, BYPASSRLS) on Supabase's image, so it can do no more than a migration role can."""
    with psycopg.connect(url, autocommit=True) as conn:
        cur = conn.execute(sql, params)  # type: ignore[arg-type]
        return cur.fetchall() if cur.description is not None else []


def require_superuser(url: str) -> None:
    """Skip a test that needs the administrator to be a superuser (to create or alter one)."""
    if superuser_query(url, "SELECT rolsuper FROM pg_roles WHERE rolname = current_user") != [(True,)]:
        pytest.skip("the test database's administrator is not a superuser (Supabase's image)")


def grant_to_administrator(url: str, role: str) -> None:
    """Let the administrator SET ROLE to ``role`` and use its privileges.

    A non-superuser that creates a role is given ADMIN on it but neither SET nor INHERIT, and cannot hand it
    relations or switch to it. The grant names both roles: ``TO CURRENT_USER`` crashes old Supabase images.
    """
    with psycopg.connect(url, autocommit=True) as conn:
        [(administrator,)] = conn.execute("SELECT current_user::text").fetchall()
        conn.execute(sql.SQL("GRANT {} TO {}").format(sql.Identifier(role), sql.Identifier(administrator)))


# ---------------------------------------------------------------------------
# Settings, container, and client
# ---------------------------------------------------------------------------


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "_env_file": None,
        "app_env": "test",
        "app_base_url": "http://localhost:3000",
        "database_url": "postgresql://unused:unused@127.0.0.1:1/unused",
        "billing_mode": "mock",
        "stripe_price_pro_monthly": PRICE_MONTHLY,
        "stripe_price_pro_yearly": PRICE_YEARLY,
        "stripe_webhook_secret": TEST_STRIPE_WEBHOOK_SECRET,
        "llm_mode": "mock",
        "analytics_mode": "disabled",
        "error_reporting_mode": "disabled",
    }
    values.update(overrides)
    return load_settings(**values)


@dataclass
class Harness:
    container: Container
    client: TestClient
    verifier: TokenTableVerifier
    directory: StaticMembershipDirectory
    billing: InMemoryBillingProvider
    clock: FrozenClock
    analytics: RecordingAnalytics
    database_url: str

    def login(self, external_user_id: str, actor: str | None = None) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.verifier.issue(external_user_id, actor)}"}


@pytest.fixture
def make_harness(db: str) -> Iterator[Callable[..., Harness]]:
    created: list[Harness] = []

    def factory(settings_overrides: dict[str, Any] | None = None, **container_overrides: Any) -> Harness:
        settings = make_settings(database_url=db, **(settings_overrides or {}))
        verifier = TokenTableVerifier()
        directory = StaticMembershipDirectory()
        billing = InMemoryBillingProvider()
        clock = FrozenClock()
        analytics = RecordingAnalytics()
        overrides: dict[str, Any] = {
            "verifier": verifier,
            "directory": directory,
            "billing_provider": billing,
            "billing_webhook_verifier": StripeWebhookVerifier(TEST_STRIPE_WEBHOOK_SECRET),
            "clock": clock,
            "analytics": analytics,
            "llm_provider": MockLLMProvider(),
        }
        overrides.update(container_overrides)
        container = build_container(settings, **overrides)
        analytics.allowed = container.kernel.analytics_events
        app = create_app(container)
        app.add_middleware(Recorder, sink=RECORDS)  # outermost: sees every response, for the contract check below
        client = TestClient(app, raise_server_exceptions=False)
        harness = Harness(container, client, verifier, directory, billing, clock, analytics, db)
        created.append(harness)
        return harness

    yield factory
    for harness in created:
        harness.container.close()


@pytest.fixture
def harness(make_harness: Callable[..., Harness]) -> Harness:
    return make_harness()
