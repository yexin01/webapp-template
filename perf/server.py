"""Local performance-test server (spec §15.2). LOCAL USE ONLY.

Runs the real API (routes, services, repositories, RLS) against a dedicated,
freshly seeded local database, with every external integration mocked.

Authentication approach for load tests (ENV-08): this launcher lives outside
the application package and is never deployed. It composes the application
with a token table that maps ``perf-token-<user>`` to a synthetic user, the
same technique the test suite uses. The application itself contains no
test-only authentication path.

    cd apps/api && uv run python ../../perf/server.py [--users 200] [--orgs 20] [--projects 50]

On exit (Ctrl+C) it prints database statistics as JSON: webhook ledger
counts and the peak number of connections observed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

API_DIR = Path(__file__).resolve().parents[1] / "apps" / "api"
sys.path.insert(0, str(API_DIR))

import psycopg  # noqa: E402
import uvicorn  # noqa: E402

from app.composition import build_container  # noqa: E402
from app.domain.entitlements import PlanKey, SubscriptionSnapshot, SubscriptionStatus  # noqa: E402
from app.domain.errors import AuthenticationError  # noqa: E402
from app.domain.tenancy import ExternalMembership, Role, VerifiedIdentity  # noqa: E402
from app.http.app import create_app  # noqa: E402
from app.infrastructure import migrations  # noqa: E402
from app.infrastructure.config import load_settings  # noqa: E402
from app.infrastructure.fakes import InMemoryBillingProvider, StaticMembershipDirectory  # noqa: E402
from app.infrastructure.llm_providers import MockLLMProvider  # noqa: E402
from app.infrastructure.observability import NoopAnalytics, configure_logging  # noqa: E402

ADMIN_URL = os.environ.get("PERF_ADMIN_DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:54329/app")
PERF_DB = "app_perf"
# Test-only signing secret shared with perf/k6/scenarios.js. Not a credential.
WEBHOOK_SECRET = "whsec_perf_test_only_not_a_real_secret"  # noqa: S105  # gitleaks:allow
PRICE_MONTHLY = "price_perf_monthly"
PRICE_YEARLY = "price_perf_yearly"
PERIOD_END = datetime(2099, 1, 1, tzinfo=UTC)


def user_id(i: int) -> str:
    return f"perf_user_{i:04d}"


def org_id(i: int) -> str:
    return f"org_perf{i:03d}"


def customer_id(i: int) -> str:
    return f"cus_perf{i:04d}"


class TokenTable:
    """Maps ``perf-token-<clerk user id>`` to that user; everything else is rejected."""

    def __init__(self, users: int) -> None:
        self._users = {f"perf-token-{user_id(i)}": user_id(i) for i in range(users)}

    def verify(self, token: str) -> VerifiedIdentity:
        user = self._users.get(token)
        if user is None:
            raise AuthenticationError()
        return VerifiedIdentity(external_user_id=user)


def recreate_database() -> str:
    migrations.require_local_database(ADMIN_URL, "run performance tests")
    with psycopg.connect(ADMIN_URL, autocommit=True, connect_timeout=3) as conn:
        conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()",
            (PERF_DB,),
        )
        conn.execute(f'DROP DATABASE IF EXISTS "{PERF_DB}"')
        conn.execute(f'CREATE DATABASE "{PERF_DB}"')
    url = urlunparse(urlparse(ADMIN_URL)._replace(path=f"/{PERF_DB}"))
    migrations.migrate(url)
    return url


def seed(url: str, users: int, orgs: int, projects: int) -> dict[str, int]:
    """Deterministic synthetic dataset: identifiers derive from indexes only."""
    params = {"users": users, "orgs": orgs, "projects": projects}
    statements = [
        # Users perf_user_0000..; UUIDs are md5 of the external ID.
        """INSERT INTO app.users (id, clerk_user_id)
           SELECT md5(u)::uuid, u FROM (
             SELECT 'perf_user_' || lpad(i::text, 4, '0') AS u FROM generate_series(0, %(users)s - 1) i) s""",
        """INSERT INTO app.organizations (id, clerk_org_id)
           SELECT md5(o)::uuid, o FROM (
             SELECT 'org_perf' || lpad(i::text, 3, '0') AS o FROM generate_series(0, %(orgs)s - 1) i) s""",
        # Every user belongs to one organization; the first user of each is its admin.
        """INSERT INTO app.organization_memberships (org_id, user_id, role)
           SELECT md5('org_perf' || lpad((i %% %(orgs)s)::text, 3, '0'))::uuid,
                  md5('perf_user_' || lpad(i::text, 4, '0'))::uuid,
                  CASE WHEN i < %(orgs)s THEN 'admin' ELSE 'member' END
           FROM generate_series(0, %(users)s - 1) i""",
        """INSERT INTO app.projects (id, org_id, owner_user_id, created_by, name, created_at)
           SELECT md5('pp' || u.id || '-' || p)::uuid, NULL, u.id, u.id, 'Personal project ' || p,
                  timestamptz '2026-01-01' + p * interval '1 minute'
           FROM app.users u CROSS JOIN generate_series(1, %(projects)s) p""",
        """INSERT INTO app.projects (id, org_id, owner_user_id, created_by, name, created_at)
           SELECT md5('op' || o.id || '-' || p)::uuid, o.id, NULL,
                  (SELECT m.user_id FROM app.organization_memberships m
                   WHERE m.org_id = o.id AND m.role = 'admin' LIMIT 1),
                  'Organization project ' || p, timestamptz '2026-01-01' + p * interval '1 minute'
           FROM app.organizations o CROSS JOIN generate_series(1, %(projects)s) p""",
        # Even-numbered users are Pro (active monthly subscription).
        """INSERT INTO app.billing_customers (user_id, stripe_customer_id)
           SELECT md5('perf_user_' || lpad(i::text, 4, '0'))::uuid, 'cus_perf' || lpad(i::text, 4, '0')
           FROM generate_series(0, %(users)s - 1, 2) i""",
        """INSERT INTO app.subscriptions (stripe_subscription_id, user_id, stripe_customer_id, status, plan_key,
                                          price_id, current_period_end)
           SELECT 'sub_perf' || lpad(i::text, 4, '0'), md5('perf_user_' || lpad(i::text, 4, '0'))::uuid,
                  'cus_perf' || lpad(i::text, 4, '0'), 'active', 'pro_monthly', 'price_perf_monthly',
                  timestamptz '2099-01-01'
           FROM generate_series(0, %(users)s - 1, 2) i""",
    ]
    with psycopg.connect(url) as conn, conn.transaction():
        for sql in statements:
            conn.execute(sql, params)  # type: ignore[arg-type]
        # Table names come from this fixed tuple, never from input.
        return {
            table: conn.execute(f"SELECT count(*) FROM app.{table}").fetchone()[0]  # type: ignore[index]  # noqa: S608
            for table in ("users", "organizations", "organization_memberships", "projects", "subscriptions")
        }


def build_app(url: str, users: int, orgs: int, pool_size: int) -> tuple[Any, InMemoryBillingProvider]:
    settings = load_settings(
        _env_file=None,
        app_env="test",
        app_base_url="http://localhost:3000",
        database_url=url,
        database_pool_max_size=pool_size,
        billing_mode="mock",
        stripe_price_pro_monthly=PRICE_MONTHLY,
        stripe_price_pro_yearly=PRICE_YEARLY,
        stripe_webhook_secret=WEBHOOK_SECRET,
        llm_mode="mock",
        analytics_mode="disabled",
        error_reporting_mode="disabled",
        log_level="WARNING",
    )
    directory = StaticMembershipDirectory()
    billing = InMemoryBillingProvider()
    for i in range(users):
        role = Role.ADMIN if i < orgs else Role.MEMBER
        directory.set(user_id(i), [ExternalMembership(org_id(i % orgs), role)])
    for i in range(0, users, 2):
        snapshot = SubscriptionSnapshot(
            stripe_subscription_id=f"sub_perf{i:04d}",
            stripe_customer_id=customer_id(i),
            status=SubscriptionStatus.ACTIVE,
            plan_key=PlanKey.PRO_MONTHLY,
            price_id=PRICE_MONTHLY,
            current_period_end=PERIOD_END,
            cancel_at_period_end=False,
            cancel_at=None,
            ended_at=None,
        )
        billing.set_subscriptions(customer_id(i), [snapshot])
    container = build_container(
        settings,
        verifier=TokenTable(users),
        directory=directory,
        billing_provider=billing,
        analytics=NoopAnalytics(),
        llm_provider=MockLLMProvider(),
    )
    return create_app(container), billing


class ConnectionSampler(threading.Thread):
    """Samples the number of server connections to the perf database."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.peak = 0
        self._stop = threading.Event()

    def run(self) -> None:
        with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
            while not self._stop.wait(0.5):
                row = conn.execute("SELECT count(*) FROM pg_stat_activity WHERE datname = %s", (PERF_DB,)).fetchone()
                self.peak = max(self.peak, int(row[0]) if row else 0)

    def stop(self) -> None:
        self._stop.set()


def ledger_stats(url: str) -> dict[str, Any]:
    with psycopg.connect(url) as conn:
        rows = conn.execute(
            "SELECT status, count(*), coalesce(sum(attempts), 0) FROM app.billing_events GROUP BY status"
        ).fetchall()
    return {status: {"events": count, "deliveries": int(attempts)} for status, count, attempts in rows}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--users", type=int, default=200)
    parser.add_argument("--orgs", type=int, default=20)
    parser.add_argument("--projects", type=int, default=50, help="projects per tenant")
    parser.add_argument("--pool-size", type=int, default=10)
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    if args.users < args.orgs or args.orgs < 1:
        parser.error("--users must be at least --orgs, and --orgs at least 1")

    configure_logging("WARNING")
    started = time.monotonic()
    url = recreate_database()
    counts = seed(url, args.users, args.orgs, args.projects)
    app, _ = build_app(url, args.users, args.orgs, args.pool_size)
    print(
        json.dumps({"dataset": counts, "seed_seconds": round(time.monotonic() - started, 2), "port": args.port}),
        flush=True,
    )

    sampler = ConnectionSampler()
    sampler.start()
    try:
        # Loopback only: this server accepts synthetic tokens.
        uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning", access_log=False)
    finally:
        sampler.stop()
        print(
            json.dumps(
                {
                    "peak_database_connections": sampler.peak,
                    "pool_max_size": args.pool_size,
                    "billing_events": ledger_stats(url),
                }
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
