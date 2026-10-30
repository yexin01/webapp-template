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

from app.bootstrap import Container, build_container, create_app
from app.kernel.errors import AuthenticationError
from app.kernel.infrastructure import migrations
from app.kernel.tenancy import VerifiedIdentity
from app.modules.billing.adapters.fakes import InMemoryBillingProvider
from app.modules.billing.adapters.stripe import StripeWebhookVerifier
from app.modules.identity.adapters.fakes import StaticMembershipDirectory
from app.modules.llm.adapters import MockLLMProvider
from app.settings import Settings, load_settings

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
    "MEMBERSHIP_",
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

        filtered = _filter(event, properties)
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


def _database_required() -> bool:
    """True in CI or when TEST_DATABASE_URL is set: database tests must run, not skip."""
    ci = os.environ.get("CI", "").strip().lower() not in ("", "0", "false")
    return ci or bool(os.environ.get("TEST_DATABASE_URL"))


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    admin = _admin_url()
    name = f"app_test_{uuid.uuid4().hex[:10]}"
    try:
        with psycopg.connect(admin, autocommit=True, connect_timeout=3) as conn:
            conn.execute(f'CREATE DATABASE "{name}"')
    except psycopg.OperationalError as error:
        # Skipping is a local convenience only: in CI, or when a database was
        # configured explicitly, an unreachable server must fail the run
        # rather than let every database test silently skip.
        if _database_required():
            pytest.fail(f"PostgreSQL is not reachable for tests: {error}", pytrace=False)
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
    with psycopg.connect(url, autocommit=True) as conn:
        cur = conn.execute(sql, params)  # type: ignore[arg-type]
        return cur.fetchall() if cur.description is not None else []


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
        client = TestClient(create_app(container), raise_server_exceptions=False)
        harness = Harness(container, client, verifier, directory, billing, clock, analytics, db)
        created.append(harness)
        return harness

    yield factory
    for harness in created:
        harness.container.close()


@pytest.fixture
def harness(make_harness: Callable[..., Harness]) -> Harness:
    return make_harness()
