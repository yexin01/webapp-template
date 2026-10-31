"""Local performance-test server (spec §15.2). LOCAL USE ONLY.

Runs the real API (routes, services, repositories, RLS) against a dedicated,
freshly seeded local database, with every external integration mocked.

Authentication approach for load tests (ENV-08): this launcher lives outside
the application package and is never deployed. It composes the application
with a token table that maps ``perf-token-<user>`` to a synthetic user, the
same technique the test suite uses. The application itself contains no
test-only authentication path.

    cd apps/api && uv run python ../../perf/server.py [--users 200] [--orgs 20] [--projects 50]

Degradation knobs for PERF-07 (all default off, so other scenarios are unchanged):

- ``--db-latency-ms N``: every API-to-PostgreSQL round trip goes through a
  loopback proxy that adds N ms (N/2 each way), like a remote database.
- ``--provider-latency-ms N``: the mocked identity directory, Stripe API, and
  LLM provider wait N ms before every call.
- ``--telemetry``: the real PostHog and Sentry SDKs run, pointed at a
  loopback sink on this machine. Nothing leaves the machine.

Pool sizes: ``--pool-size`` (request pool) and ``--worker-pool-size``
(system pool; the app default when omitted). Billing synchronization admits
``worker pool - 1`` webhooks at a time, so that caps how many deliveries
reach the database together (PERF-06). Startup and exit reports print all three.

On exit (Ctrl+C or SIGTERM) it prints JSON: webhook ledger counts, the number of Stripe
re-reads against the number of events processed (PERF-06 exactly-once), the
peak number of connections observed, and telemetry sink counts. It exits
non-zero when the re-reads and processed events differ.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sys
import threading
import time
from collections import Counter
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse, urlunparse

API_DIR = Path(__file__).resolve().parents[1] / "apps" / "api"
sys.path.insert(0, str(API_DIR))

import psycopg  # noqa: E402
import uvicorn  # noqa: E402

from app.bootstrap import build_container, create_app  # noqa: E402
from app.kernel.errors import AuthenticationError  # noqa: E402
from app.kernel.infrastructure import migrations  # noqa: E402
from app.kernel.infrastructure.observability import (  # noqa: E402
    NoopAnalytics,
    configure_logging,
    init_error_reporting,
)
from app.kernel.tenancy import Role, VerifiedIdentity  # noqa: E402
from app.modules.billing.adapters.fakes import InMemoryBillingProvider  # noqa: E402
from app.modules.billing.domain import PlanKey, SubscriptionSnapshot, SubscriptionStatus  # noqa: E402
from app.modules.identity.adapters.fakes import StaticMembershipDirectory  # noqa: E402
from app.modules.identity.domain import ExternalMembership  # noqa: E402
from app.modules.llm.adapters import MockLLMProvider  # noqa: E402
from app.settings import load_settings  # noqa: E402

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
    # Zero-padded like user_id() and k6's padStart. lpad alone would cut a longer
    # number short ('10000' to '1000'), so user 10000 would collide with user 1000.
    padded = """CREATE FUNCTION pg_temp.padded(i bigint, width int) RETURNS text IMMUTABLE LANGUAGE sql
                AS $$ SELECT lpad(i::text, greatest(width, length(i::text)), '0') $$"""
    statements = [
        # Users perf_user_0000..; UUIDs are md5 of the external ID.
        """INSERT INTO app.users (id, clerk_user_id)
           SELECT md5(u)::uuid, u FROM (
             SELECT 'perf_user_' || pg_temp.padded(i, 4) AS u FROM generate_series(0, %(users)s - 1) i) s""",
        """INSERT INTO app.organizations (id, clerk_org_id)
           SELECT md5(o)::uuid, o FROM (
             SELECT 'org_perf' || pg_temp.padded(i, 3) AS o FROM generate_series(0, %(orgs)s - 1) i) s""",
        # Every user belongs to one organization; the first user of each is its admin.
        """INSERT INTO app.organization_memberships (org_id, user_id, role)
           SELECT md5('org_perf' || pg_temp.padded(i %% %(orgs)s, 3))::uuid,
                  md5('perf_user_' || pg_temp.padded(i, 4))::uuid,
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
        # Every user has a billing customer. Even-numbered users are Pro (active
        # monthly subscription). Odd-numbered ones have no subscription: PERF-06
        # sends its events for them, so a deployed preview's empty Stripe mock has
        # nothing it would need to re-read.
        """INSERT INTO app.billing_customers (user_id, stripe_customer_id)
           SELECT md5('perf_user_' || pg_temp.padded(i, 4))::uuid, 'cus_perf' || pg_temp.padded(i, 4)
           FROM generate_series(0, %(users)s - 1) i""",
        """INSERT INTO app.subscriptions (stripe_subscription_id, user_id, stripe_customer_id, status, plan_key,
                                          price_id, current_period_end)
           SELECT 'sub_perf' || pg_temp.padded(i, 4), md5('perf_user_' || pg_temp.padded(i, 4))::uuid,
                  'cus_perf' || pg_temp.padded(i, 4), 'active', 'pro_monthly', 'price_perf_monthly',
                  timestamptz '2099-01-01'
           FROM generate_series(0, %(users)s - 1, 2) i""",
    ]
    with psycopg.connect(url) as conn, conn.transaction():
        conn.execute(padded)
        for sql in statements:
            conn.execute(sql, params)  # type: ignore[arg-type]
        # Table names come from this fixed tuple, never from input.
        return {
            table: conn.execute(f"SELECT count(*) FROM app.{table}").fetchone()[0]  # type: ignore[index]  # noqa: S608
            for table in (
                "users",
                "organizations",
                "organization_memberships",
                "projects",
                "billing_customers",
                "subscriptions",
            )
        }


PROXY_HOST = "127.0.0.1"


class DelayProxy(threading.Thread):
    """Loopback TCP proxy to PostgreSQL that adds a fixed round-trip delay (PERF-07)."""

    def __init__(self, target_host: str, target_port: int, round_trip_ms: float) -> None:
        super().__init__(daemon=True)
        self._target = (target_host, target_port)
        self._one_way = round_trip_ms / 2000
        self.port = 0
        self.ready = threading.Event()

    def run(self) -> None:
        asyncio.run(self._serve())

    async def _serve(self) -> None:
        server = await asyncio.start_server(self._handle, PROXY_HOST, 0)
        self.port = server.sockets[0].getsockname()[1]
        self.ready.set()
        async with server:
            await server.serve_forever()

    async def _handle(self, client_r: asyncio.StreamReader, client_w: asyncio.StreamWriter) -> None:
        upstream_r, upstream_w = await asyncio.open_connection(*self._target)
        await asyncio.gather(self._pipe(client_r, upstream_w), self._pipe(upstream_r, client_w))

    async def _pipe(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while data := await reader.read(65536):
                await asyncio.sleep(self._one_way)
                writer.write(data)
                await writer.drain()
        except ConnectionError:
            pass
        finally:
            writer.close()


def proxied_url(url: str, proxy_port: int) -> str:
    """``url`` with its server replaced by the loopback proxy; credentials and options are kept.

    Fails loudly rather than return a URL that still reaches the database
    directly, which would silently measure no added latency.
    """
    parsed = urlparse(url)
    overrides = {"host", "hostaddr", "port"} & set(parse_qs(parsed.query))
    if overrides:
        raise SystemExit(f"--db-latency-ms: the database URL sets {sorted(overrides)} in its query; remove them.")
    if not parsed.hostname:
        raise SystemExit("--db-latency-ms needs a database URL with a TCP host.")
    userinfo, at, _ = parsed.netloc.rpartition("@")
    host = f"[{PROXY_HOST}]" if ":" in PROXY_HOST else PROXY_HOST
    result = urlunparse(parsed._replace(netloc=f"{userinfo}{at}{host}:{proxy_port}"))
    check = urlparse(result)
    target = (check.hostname, check.port)
    if target != (PROXY_HOST, proxy_port) or target == (parsed.hostname, parsed.port or 5432):
        raise SystemExit("--db-latency-ms: could not point the database URL at the latency proxy.")
    return result


class Delayed:
    """Adds a fixed delay before every method call on a mocked provider (PERF-07)."""

    def __init__(self, inner: Any, delay_ms: float) -> None:
        self._inner = inner
        self._delay = delay_ms / 1000

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._inner, name)
        if not callable(attr):
            return attr

        def call(*args: Any, **kwargs: Any) -> Any:
            time.sleep(self._delay)
            return attr(*args, **kwargs)

        return call


class TelemetrySink(threading.Thread):
    """Loopback HTTP endpoint that accepts PostHog and Sentry uploads and counts them."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.requests: Counter[str] = Counter()
        sink = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                sink.requests[self.path.split("?")[0]] += 1
                body = b'{"status": 1}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: Any) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]

    def run(self) -> None:
        self._server.serve_forever()


def build_app(
    url: str,
    users: int,
    orgs: int,
    pool_size: int,
    *,
    worker_pool_size: int | None = None,
    provider_latency_ms: float = 0,
    telemetry_port: int | None = None,
    traces_sample_rate: float = 0.0,
) -> tuple[Any, InMemoryBillingProvider]:
    telemetry: dict[str, Any] = {"analytics_mode": "disabled", "error_reporting_mode": "disabled"}
    if telemetry_port is not None:
        # Real SDKs, loopback sink. The key and DSN are local placeholders, not credentials.
        telemetry = {
            "analytics_mode": "sandbox",
            "posthog_project_api_key": "perf-local-sink",
            "posthog_host": f"http://127.0.0.1:{telemetry_port}",
            "error_reporting_mode": "sandbox",
            "sentry_dsn": f"http://perflocal@127.0.0.1:{telemetry_port}/1",
            "sentry_traces_sample_rate": traces_sample_rate,
        }
    pools: dict[str, Any] = {"database_pool_max_size": pool_size}
    if worker_pool_size is not None:
        pools["database_worker_pool_max_size"] = worker_pool_size
    settings = load_settings(
        _env_file=None,
        app_env="test",
        app_base_url="http://localhost:3000",
        database_url=url,
        billing_mode="mock",
        stripe_price_pro_monthly=PRICE_MONTHLY,
        stripe_price_pro_yearly=PRICE_YEARLY,
        stripe_webhook_secret=WEBHOOK_SECRET,
        llm_mode="mock",
        # Generous limits so the LLM and write classes measure latency, not the rate limiter.
        llm_checks_per_user_per_minute=600,
        llm_checks_global_per_day=100_000,
        # The LLM check is an operator tool, so the synthetic users are operators: the llm_check class measures
        # the check, not a 404.
        diagnostics_operator_ids=[user_id(i) for i in range(users)],
        writes_per_user_per_minute=600,
        log_level="WARNING",
        **pools,
        **telemetry,
    )
    if telemetry_port is not None:
        init_error_reporting(settings)
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
    overrides: dict[str, Any] = {
        "verifier": TokenTable(users),
        "directory": directory,
        "billing_provider": billing,
        "llm_provider": MockLLMProvider(),
    }
    if provider_latency_ms > 0:
        for name in ("directory", "billing_provider", "llm_provider"):
            overrides[name] = Delayed(overrides[name], provider_latency_ms)
    if telemetry_port is None:
        overrides["analytics"] = NoopAnalytics()
    container = build_container(settings, **overrides)
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
    """Ledger rows by status. ``claims`` counts deliveries that reached the ledger (duplicates included)."""
    with psycopg.connect(url) as conn:
        rows = conn.execute(
            "SELECT status, count(*), coalesce(sum(attempts), 0) FROM app.billing_events GROUP BY status"
        ).fetchall()
    return {status: {"events": count, "claims": int(attempts)} for status, count, attempts in rows}


def webhook_report(url: str, billing: InMemoryBillingProvider) -> dict[str, Any]:
    """PERF-06 oracle: every distinct event must re-read Stripe exactly once.

    The ledger cannot show this on its own: a duplicate delivery that runs the
    synchronization again leaves the same final row (status and attempts). Each
    synchronization re-reads the customer's subscriptions from the mocked
    Stripe API once, so the re-reads must equal the events processed. No other
    perf scenario calls that API. A synchronization that failed after the
    re-read and was retried also adds one; such a delivery got a non-200.
    Only this local server can count the re-reads: a deployed target cannot.
    """
    ledger = ledger_stats(url)
    processed = int(ledger.get("processed", {}).get("events", 0))
    calls = Counter(billing.calls)
    resyncs = calls["list_customer_subscriptions"]
    return {
        "billing_events": ledger,
        "stripe_calls": dict(calls),
        "events_processed": processed,
        "stripe_resyncs": resyncs,
        "exactly_once": None if processed == resyncs == 0 else processed == resyncs,
    }


def pool_report(app: Any) -> dict[str, int]:
    settings = app.state.container.settings
    worker = settings.database_worker_pool_max_size
    return {
        "api_pool_max_size": settings.database_pool_max_size,
        "worker_pool_max_size": worker,
        # As wired in app/modules/billing/module.py: one worker connection stays free.
        "billing_sync_slots": worker - 1,
    }


def serve(app: Any, port: int) -> None:
    """Serve on loopback until Ctrl+C or SIGTERM, then return so the caller can report."""
    # After shutting down, uvicorn re-raises the signal that stopped it. SIGTERM's
    # default action would end the process there, so it raises KeyboardInterrupt
    # instead, which uvicorn.run swallows as it does for Ctrl+C.
    signal.signal(signal.SIGTERM, signal.default_int_handler)
    # Loopback only: this server accepts synthetic tokens.
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--users", type=int, default=200)
    parser.add_argument("--orgs", type=int, default=20)
    parser.add_argument("--projects", type=int, default=50, help="projects per tenant")
    parser.add_argument("--pool-size", type=int, default=10, help="request (app_api) pool")
    parser.add_argument(
        "--worker-pool-size",
        type=int,
        default=None,
        help="system (app_worker) pool, app default when omitted; billing sync slots are this minus one",
    )
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--db-latency-ms", type=float, default=0, help="added API-to-database round-trip time")
    parser.add_argument("--provider-latency-ms", type=float, default=0, help="added latency per mocked provider call")
    parser.add_argument("--telemetry", action="store_true", help="real PostHog and Sentry SDKs to a loopback sink")
    parser.add_argument("--traces-sample-rate", type=float, default=0.0, help="Sentry tracing rate with --telemetry")
    args = parser.parse_args()
    if args.users < args.orgs or args.orgs < 1:
        parser.error("--users must be at least --orgs, and --orgs at least 1")

    configure_logging("WARNING")
    started = time.monotonic()
    url = recreate_database()
    counts = seed(url, args.users, args.orgs, args.projects)

    app_url = url
    if args.db_latency_ms > 0:
        parsed = urlparse(url)
        if not parsed.hostname:
            parser.error("--db-latency-ms needs a database URL with a TCP host")
        proxy = DelayProxy(parsed.hostname, parsed.port or 5432, args.db_latency_ms)
        proxy.start()
        if not proxy.ready.wait(5):
            sys.exit("--db-latency-ms: the latency proxy did not start")
        app_url = proxied_url(url, proxy.port)
    sink: TelemetrySink | None = None
    if args.telemetry:
        sink = TelemetrySink()
        sink.start()

    app, billing = build_app(
        app_url,
        args.users,
        args.orgs,
        args.pool_size,
        worker_pool_size=args.worker_pool_size,
        provider_latency_ms=args.provider_latency_ms,
        telemetry_port=sink.port if sink else None,
        traces_sample_rate=args.traces_sample_rate,
    )
    pools = pool_report(app)
    degradation = {
        "db_latency_ms": args.db_latency_ms,
        "provider_latency_ms": args.provider_latency_ms,
        "telemetry": args.telemetry,
        "traces_sample_rate": args.traces_sample_rate if args.telemetry else None,
    }
    print(
        json.dumps(
            {
                "dataset": counts,
                "seed_seconds": round(time.monotonic() - started, 2),
                "port": args.port,
                "pools": pools,
                "degradation": degradation,
            }
        ),
        flush=True,
    )

    sampler = ConnectionSampler()
    sampler.start()
    try:
        serve(app, args.port)
    finally:
        sampler.stop()
        webhooks = webhook_report(url, billing)
        print(
            json.dumps(
                {
                    "peak_database_connections": sampler.peak,
                    "pools": pools,
                    "webhooks": webhooks,
                    "telemetry_sink_requests": dict(sink.requests) if sink else None,
                }
            ),
            flush=True,
        )
    if webhooks["exactly_once"] is False:
        sys.exit(
            f"PERF-06 FAILED: {webhooks['stripe_resyncs']} Stripe re-reads for {webhooks['events_processed']} "
            "processed events. Exactly-once is violated, unless deliveries that got a non-200 explain the difference."
        )


if __name__ == "__main__":
    main()
