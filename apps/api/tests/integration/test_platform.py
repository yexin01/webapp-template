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


@pytest.mark.parametrize("url", ["postgresql://u:p@db.example.com:5432/app", "postgresql://u:p@10.0.0.5/app"])
def test_destructive_commands_refuse_non_local_targets(url: str) -> None:
    with pytest.raises(migrations.UnsafeTargetError):
        migrations.reset(url)
    with pytest.raises(migrations.UnsafeTargetError):
        migrations.seed(url)
