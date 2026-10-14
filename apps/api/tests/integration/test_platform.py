"""Cross-cutting behavior: route manifest, error contract, limits, resilience,
telemetry leakage, identity webhooks, LLM limits, and migration safety."""

from __future__ import annotations

import base64
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.routing import APIRoute
from svix.webhooks import Webhook

from app.kernel.infrastructure import migrations
from app.kernel.infrastructure.observability import JsonFormatter
from app.kernel.tenancy import Role
from app.modules.identity.adapters.clerk import ClerkWebhookVerifier
from app.modules.identity.domain import ExternalMembership
from app.modules.llm.adapters import MockLLMProvider
from tests.conftest import Harness, superuser_query

pytestmark = pytest.mark.db

PUBLIC_ROUTES = {
    ("GET", "/api/v1/health"),
    ("GET", "/api/v1/ready"),
    ("GET", "/api/v1/openapi.json"),
    # Authenticated by webhook signature on the raw body instead of a session.
    ("POST", "/api/v1/webhooks/stripe"),
    ("POST", "/api/v1/webhooks/clerk"),
}


def _api_routes(routes: list[Any]) -> list[APIRoute]:
    found: list[APIRoute] = []
    for route in routes:
        if isinstance(route, APIRoute):
            found.append(route)
        for attr in ("routes", "router"):
            nested = getattr(route, attr, None)
            if nested is not None and nested is not routes:
                found += _api_routes(nested.routes if hasattr(nested, "routes") else nested)
    return found


def _requires_auth(dependant: Any, authenticate: Any) -> bool:
    return any(d.call is authenticate or _requires_auth(d, authenticate) for d in dependant.dependencies)


def test_route_manifest_every_route_declares_authentication(harness: Harness) -> None:
    """TEN-02: every route is authenticated unless explicitly allowlisted."""
    app = harness.client.app
    spec_paths = {(m.upper(), p) for p, ops in app.openapi()["paths"].items() for m in ops}  # type: ignore[attr-defined]
    routes = {r.path: r for r in _api_routes(list(app.routes))}  # type: ignore[attr-defined]
    assert len(spec_paths) >= 17
    unprotected = []
    for method, path in spec_paths:
        if (method, path) in PUBLIC_ROUTES:
            continue
        route = routes.get(path)
        if route is None:
            # Fall back to an HTTP probe: an unauthenticated call must be refused.
            probe_path = path.replace("{org_id}", "org_probe").replace(
                "{project_id}", "00000000-0000-4000-8000-000000000000"
            )
            status = harness.client.request(method, probe_path).status_code
            if status != 401:
                unprotected.append((method, path, status))
        elif not _requires_auth(route.dependant, harness.container.identity.auth.authenticate):
            unprotected.append((method, path, "no authenticate dependency"))
    assert unprotected == []


def test_unauthenticated_probe_of_every_protected_route(harness: Harness) -> None:
    for path, ops in harness.client.app.openapi()["paths"].items():  # type: ignore[attr-defined]
        for method in ops:
            if (method.upper(), path) in PUBLIC_ROUTES:
                continue
            probe = path.replace("{org_id}", "org_probe").replace(
                "{project_id}", "00000000-0000-4000-8000-000000000000"
            )
            r = harness.client.request(method.upper(), probe, json={})
            assert r.status_code == 401, (method, path, r.status_code)


def test_error_contract_and_security_headers(harness: Harness) -> None:
    r = harness.client.get("/api/v1/does-not-exist", headers={"X-Request-ID": "req-test-12345"})
    assert r.status_code == 404
    assert r.headers["content-type"] == "application/problem+json"
    body = r.json()
    assert set(body) >= {"type", "title", "status", "code", "detail", "request_id"}
    assert body["request_id"] == "req-test-12345"
    assert r.headers["x-request-id"] == "req-test-12345"
    for header in ("x-content-type-options", "x-frame-options", "content-security-policy", "cache-control"):
        assert header in r.headers


def test_malicious_request_id_replaced(harness: Harness) -> None:
    r = harness.client.get("/api/v1/health", headers={"X-Request-ID": "<script>alert(1)</script>"})
    assert "<" not in r.headers["x-request-id"]


def test_body_size_limit(make_harness: Any) -> None:
    h = make_harness({"max_request_body_bytes": 2048})
    headers = h.login("user_a")
    r = h.client.post("/api/v1/projects", content=b"x" * 4096, headers={**headers, "Content-Type": "application/json"})
    assert r.status_code == 413
    assert r.json()["code"] == "PAYLOAD_TOO_LARGE"


def test_body_size_limit_without_content_length(make_harness: Any) -> None:
    """A chunked body has no Content-Length; the streaming count still answers 413."""
    h = make_harness({"max_request_body_bytes": 2048})

    def chunks() -> Any:
        for _ in range(4):
            yield b"x" * 1024

    headers = {**h.login("user_a"), "Content-Type": "application/json"}
    r = h.client.post("/api/v1/projects", content=chunks(), headers=headers)
    assert "content-length" not in {k.lower() for k in r.request.headers}
    assert r.status_code == 413
    assert r.json()["code"] == "PAYLOAD_TOO_LARGE"


def test_unhandled_errors_are_sanitized(make_harness: Any) -> None:
    class Exploding:
        name = "boom"

        def check(self) -> Any:
            raise RuntimeError("secret detail sk_test_LEAKCANARY0000 at /internal/path")

    h = make_harness(llm_provider=Exploding())
    r = h.client.post("/api/v1/llm/check", headers=h.login("user_a"))
    assert r.status_code == 500
    assert r.json()["code"] == "INTERNAL_ERROR"
    assert "LEAKCANARY" not in r.text
    assert "internal" not in r.text
    # Correlated like every other response (OBS-05), with the security headers.
    assert r.json()["request_id"] == r.headers["x-request-id"]
    assert r.headers["x-content-type-options"] == "nosniff"


def test_unhandled_error_message_never_reaches_the_logs(make_harness: Any, capsys: pytest.CaptureFixture[str]) -> None:
    """The raw exception message stays out of the application's log lines; only its type is logged."""
    import logging

    from app.kernel.infrastructure.observability import JsonFormatter

    class Exploding:
        name = "boom"

        def check(self) -> Any:
            raise RuntimeError("detail sk_test_LEAKCANARY0000 alice@example.com")

    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        h = make_harness(llm_provider=Exploding())
        r = h.client.post("/api/v1/llm/check", headers=h.login("user_a"))
    finally:
        root.removeHandler(handler)
    assert r.status_code == 500
    captured = capsys.readouterr()
    assert "LEAKCANARY" not in captured.out + captured.err
    assert '"exc_type": "RuntimeError"' in captured.err


def test_pagination_is_bounded_and_cursor_based(harness: Harness) -> None:
    headers = harness.login("user_a")
    for i in range(5):
        harness.client.post("/api/v1/projects", json={"name": f"p{i}"}, headers=headers)
    first = harness.client.get("/api/v1/projects?limit=2", headers=headers).json()
    assert len(first["items"]) == 2
    assert first["next_cursor"]
    second = harness.client.get(f"/api/v1/projects?limit=2&cursor={first['next_cursor']}", headers=headers).json()
    assert {p["id"] for p in first["items"]}.isdisjoint({p["id"] for p in second["items"]})
    assert harness.client.get("/api/v1/projects?limit=1000", headers=headers).status_code == 422
    assert harness.client.get("/api/v1/projects?cursor=!!!", headers=headers).status_code == 400


def test_project_name_validation(harness: Harness) -> None:
    headers = harness.login("user_a")
    for name in ("", "   ", "x" * 121):
        assert harness.client.post("/api/v1/projects", json={"name": name}, headers=headers).status_code in (400, 422)


# -- Resilience -----------------------------------------------------------------


def test_readiness_reports_database_unavailable(make_harness: Any) -> None:
    from app.kernel.infrastructure.db import Database

    dead = Database(
        "postgresql://u:p@127.0.0.1:1/x",
        api_pool_size=1,
        worker_pool_size=1,
        connect_timeout=1,
        statement_timeout_ms=1000,
    )
    h = make_harness(database=dead)
    assert h.client.get("/api/v1/health").status_code == 200
    r = h.client.get("/api/v1/ready")
    assert r.status_code == 503
    assert r.json() == {"status": "not_ready", "checks": {"database": "unavailable"}}
    r = h.client.get("/api/v1/me", headers=h.login("user_a"))
    assert r.status_code == 503
    assert "127.0.0.1" not in r.text


def test_analytics_failure_does_not_break_requests(make_harness: Any) -> None:
    class Broken:
        def capture(self, *_args: Any) -> None:
            raise ConnectionError("analytics down")

        def shutdown(self) -> None:
            raise ConnectionError("analytics down")

    from app.kernel.infrastructure.observability import PostHogAnalytics

    analytics = PostHogAnalytics(api_key="phc_test_not_real", host="http://127.0.0.1:1")
    analytics._client = Broken()  # type: ignore[assignment]
    h = make_harness(analytics=analytics)
    headers = h.login("user_a")
    assert h.client.post("/api/v1/projects", json={"name": "still works"}, headers=headers).status_code == 201
    assert h.client.get("/api/v1/billing/status", headers=headers).status_code == 200


def test_llm_outage_only_affects_llm_feature(make_harness: Any) -> None:
    h = make_harness(llm_provider=MockLLMProvider(ok=False, error_code="unavailable"))
    headers = h.login("user_a")
    body = h.client.post("/api/v1/llm/check", headers=headers).json()
    assert body["ok"] is False
    assert body["error_code"] == "unavailable"
    assert h.client.get("/api/v1/projects", headers=headers).status_code == 200


def test_llm_per_user_and_global_limits(make_harness: Any) -> None:
    h = make_harness({"llm_checks_per_user_per_minute": 2, "llm_checks_global_per_day": 3})
    a, b = h.login("user_a"), h.login("user_b")
    assert [h.client.post("/api/v1/llm/check", headers=a).status_code for _ in range(3)] == [200, 200, 429]
    assert h.client.post("/api/v1/llm/check", headers=b).status_code == 200  # global count reaches 3
    assert h.client.post("/api/v1/llm/check", headers=b).status_code == 429  # global budget exhausted
    h.clock.advance(minutes=1)
    assert h.client.post("/api/v1/llm/check", headers=a).status_code == 429  # still over the daily budget


# -- Telemetry leakage (OBS-08) -------------------------------------------------


def test_logs_do_not_contain_tokens_or_bodies(harness: Harness, caplog: pytest.LogCaptureFixture) -> None:
    canary_token = harness.verifier.issue("user_a")
    secret_name = "sk_test_NAMECANARY12345 owner@example.com"
    with caplog.at_level(logging.DEBUG):
        harness.client.post(
            "/api/v1/projects?token=" + canary_token,
            json={"name": secret_name},
            headers={"Authorization": f"Bearer {canary_token}", "Cookie": "__session=COOKIECANARY"},
        )
    formatter = JsonFormatter()
    rendered = "\n".join(formatter.format(r) for r in caplog.records)
    for canary in (canary_token, "NAMECANARY", "owner@example.com", "COOKIECANARY"):
        assert canary not in rendered


def test_analytics_events_are_allowlisted(harness: Harness) -> None:
    headers = harness.login("user_a")
    harness.client.post("/api/v1/projects", json={"name": "sk_test_PROPCANARY123 me@example.com"}, headers=headers)
    harness.container.kernel.analytics.capture(uuid.uuid4(), "custom_event", {"email": "x@example.com"})
    serialized = json.dumps(harness.analytics.events)
    assert "PROPCANARY" not in serialized
    assert "example.com" not in serialized
    assert {e[1] for e in harness.analytics.events} <= {
        "project_created",
        "checkout_started",
        "billing_portal_opened",
        "llm_check_run",
    }


# -- Clerk identity webhooks ----------------------------------------------------

SVIX_SECRET = "whsec_" + base64.b64encode(b"test-only-clerk-signing-secret!!").decode()


def clerk_send(h: Harness, event_type: str, data: dict[str, Any], msg_id: str | None = None) -> Any:
    payload = json.dumps({"type": event_type, "data": data, "object": "event"}).encode()
    msg_id = msg_id or f"msg_{time.time_ns()}"
    ts = int(time.time())
    from datetime import UTC, datetime

    signature = Webhook(SVIX_SECRET).sign(msg_id, datetime.fromtimestamp(ts, UTC), payload.decode())
    headers = {
        "svix-id": msg_id,
        "svix-timestamp": str(ts),
        "svix-signature": signature,
        "content-type": "application/json",
    }
    return h.client.post("/api/v1/webhooks/clerk", content=payload, headers=headers)


@pytest.fixture
def clerk_harness(make_harness: Any) -> Harness:
    return make_harness(identity_webhook_verifier=ClerkWebhookVerifier(SVIX_SECRET))


def test_membership_webhook_resyncs_from_directory(clerk_harness: Harness) -> None:
    h = clerk_harness
    headers = h.login("user_m")
    h.directory.set("user_m", [ExternalMembership("org_hook", Role.MEMBER)])
    assert h.client.get("/api/v1/orgs/org_hook/projects", headers=headers).status_code == 200

    h.directory.set("user_m", [])  # removed in Clerk
    r = clerk_send(
        h,
        "organizationMembership.deleted",
        {"organization": {"id": "org_hook"}, "public_user_data": {"user_id": "user_m"}},
    )
    assert r.json()["status"] == "processed"
    # Access revoked immediately, without waiting for the staleness bound.
    assert h.client.get("/api/v1/orgs/org_hook/projects", headers=headers).status_code == 404


def test_out_of_order_membership_created_does_not_resurrect(clerk_harness: Harness) -> None:
    h = clerk_harness
    h.login("user_m")
    h.directory.set("user_m", [])
    clerk_send(
        h,
        "organizationMembership.created",
        {"organization": {"id": "org_hook"}, "public_user_data": {"user_id": "user_m"}},
    )
    assert superuser_query(h.database_url, "SELECT count(*) FROM app.organization_memberships") == [(0,)]


def test_org_deleted_revokes_all_memberships(clerk_harness: Harness) -> None:
    h = clerk_harness
    headers = h.login("user_m")
    h.directory.set("user_m", [ExternalMembership("org_gone", Role.ADMIN)])
    h.client.get("/api/v1/orgs/org_gone/projects", headers=headers)
    clerk_send(h, "organization.deleted", {"id": "org_gone", "deleted": True})
    assert h.client.get("/api/v1/orgs/org_gone/projects", headers=headers).status_code == 404


def test_clerk_webhook_forgery_and_duplicates(clerk_harness: Harness) -> None:
    h = clerk_harness
    payload = json.dumps({"type": "user.deleted", "data": {"id": "user_x"}}).encode()
    bad = {
        "svix-id": "msg_1",
        "svix-timestamp": str(int(time.time())),
        "svix-signature": "v1,AAAA",
        "content-type": "application/json",
    }
    assert h.client.post("/api/v1/webhooks/clerk", content=payload, headers=bad).status_code == 400
    assert h.client.post("/api/v1/webhooks/clerk", content=payload).status_code == 400
    # Malformed signature headers and non-UTF-8 bodies are rejected as unauthenticated, never a 500.
    for signature, body in (("v1", payload), ("v1,a,b", payload), ("v1,AAAA", b"\xff\xfe")):
        malformed = {**bad, "svix-signature": signature}
        assert h.client.post("/api/v1/webhooks/clerk", content=body, headers=malformed).status_code == 400
    first = clerk_send(h, "user.deleted", {"id": "user_x"}, msg_id="msg_same")
    second = clerk_send(h, "user.deleted", {"id": "user_x"}, msg_id="msg_same")
    assert first.json()["status"] == second.json()["status"] == "processed"


# -- Migration safety -------------------------------------------------------------


def test_migrations_are_idempotent(db: str) -> None:
    assert migrations.migrate(db) == []


def test_modified_migration_is_detected(db: str, tmp_path: Path) -> None:
    for src in migrations.discover():
        (tmp_path / src.path.name).write_text(src.path.read_text() + "\n-- edited after apply\n")
    with pytest.raises(migrations.MigrationError, match="modified after being applied"):
        migrations.migrate(db, tmp_path)


def test_a_migration_numbered_before_an_applied_one_is_refused(db: str, tmp_path: Path) -> None:
    for src in migrations.discover():
        (tmp_path / src.path.name).write_text(src.path.read_text())
    (tmp_path / "20200101000000_late_branch.sql").write_text("SELECT 1;\n")
    with pytest.raises(migrations.MigrationError, match="numbered before"):
        migrations.migrate(db, tmp_path)


def test_concurrent_migration_runs_apply_each_migration_once(db: str, tmp_path: Path) -> None:
    import threading

    for src in migrations.discover():
        (tmp_path / src.path.name).write_text(src.path.read_text())
    (tmp_path / "29991231000000_concurrent.sql").write_text(
        "SELECT pg_sleep(0.3); CREATE TABLE app.concurrent_probe (id int);\n"
    )
    results: list[list[str]] = []
    errors: list[BaseException] = []

    def run() -> None:
        try:
            results.append(migrations.migrate(db, tmp_path))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    try:
        assert not errors
        assert sorted(len(r) for r in results) == [0, 0, 1]
    finally:
        superuser_query(db, "DROP TABLE IF EXISTS app.concurrent_probe")
        superuser_query(db, "DELETE FROM public.app_schema_migrations WHERE version = '29991231000000'")


def test_the_migration_ledger_is_private(db: str) -> None:
    """On Supabase, ``public`` is exposed through the Data API; the ledger must not be."""
    assert superuser_query(
        db, "SELECT relrowsecurity FROM pg_class WHERE oid = 'public.app_schema_migrations'::regclass"
    ) == [(True,)]
    assert superuser_query(db, "SELECT has_table_privilege('app_api', 'public.app_schema_migrations', 'SELECT')") == [
        (False,)
    ]


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://u:p@localhost/app",
        "postgresql://u:p@127.0.0.1:5432/app",
        "postgresql://u:p@[::1]/app",
        "postgresql:///app?host=/var/run/postgresql",
    ],
)
def test_local_database_urls_are_recognized(url: str) -> None:
    assert migrations.is_local_database(url)


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://u:p@db.example.com:5432/app",
        "postgresql://u:p@10.0.0.5/app",
        # libpq parameters override the URL's host.
        "postgresql://u:p@localhost/app?host=db.example.com",
        "postgresql://u:p@127.0.0.1/app?hostaddr=203.0.113.7",
        "postgresql://u:p@localhost,db.example.com/app",
        "postgresql://u:p@localhost/app?service=production",
        "not a connection string",
    ],
)
def test_non_local_database_urls_are_detected(url: str) -> None:
    assert not migrations.is_local_database(url)


def test_environment_overrides_count_for_locality(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PGHOST", "db.example.com")
    assert not migrations.is_local_database("postgresql:///app")


def test_development_commands_refuse_other_environments(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import cli

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost/app")
    assert cli.main(["seed"]) == 1
    assert cli.main(["reset", "--yes"]) == 1


@pytest.mark.parametrize("url", ["postgresql://u:p@db.example.com:5432/app", "postgresql://u:p@10.0.0.5/app"])
def test_destructive_commands_refuse_non_local_targets(url: str) -> None:
    with pytest.raises(migrations.UnsafeTargetError):
        migrations.reset(url)
    with pytest.raises(migrations.UnsafeTargetError):
        migrations.seed(url)


def test_llm_checks_in_flight_are_bounded_per_instance(make_harness: Any) -> None:
    """LLM-05: a per-instance concurrency bound, on top of the per-user and global limits."""
    import threading

    from app.modules.llm.domain import LLMCheckResult

    entered, release = threading.Event(), threading.Event()

    class Slow:
        name = "slow"

        def check(self) -> LLMCheckResult:
            entered.set()
            assert release.wait(10)
            return LLMCheckResult(ok=True, provider="slow", model="m", latency_ms=1, error_code=None)

    h = make_harness({"llm_max_concurrent_checks": 1}, llm_provider=Slow())
    codes: list[int] = []
    first = threading.Thread(
        target=lambda: codes.append(h.client.post("/api/v1/llm/check", headers=h.login("user_a")).status_code)
    )
    first.start()
    assert entered.wait(10)
    second = h.client.post("/api/v1/llm/check", headers=h.login("user_b"))
    release.set()
    first.join(10)
    assert second.status_code == 429
    assert codes == [200]
