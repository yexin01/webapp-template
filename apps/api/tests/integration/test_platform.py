"""Cross-cutting behavior: route manifest, error contract, limits, resilience,
telemetry leakage, identity webhooks, LLM limits, and migration safety."""

from __future__ import annotations

import base64
import importlib
import json
import logging
import pkgutil
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi.routing import APIRoute
from svix.webhooks import Webhook

import app.modules
from app.kernel.authorization import Access, Permission
from app.kernel.http.auth import Requires
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
# Authenticated by a bearer secret of its own (CRON_SECRET), not a session, and hidden from the
# contract; test_outbox_jobs checks that it refuses a missing or wrong secret.
SECRET_AUTHENTICATED_ROUTES = {("GET", "/api/v1/jobs/{name}")}

Discovered = tuple[str, str, APIRoute, tuple[Any, ...]]


def _api_routes(routes: list[Any], prefix: str = "", inherited: tuple[Any, ...] = ()) -> list[Discovered]:
    """Every ``APIRoute`` of the app, hidden ones included, as (method, full path, route, include dependencies).

    FastAPI 0.143 keeps an included router as one ``_IncludedRouter`` (``original_router`` plus an
    include context with the prefix and dependencies); a ``Mount``, or older FastAPI versions, expose
    ``routes`` instead.
    """
    found: list[Discovered] = []
    for route in routes:
        if isinstance(route, APIRoute):
            found += [(method, prefix + route.path_format, route, inherited) for method in sorted(route.methods)]
        elif (included := getattr(route, "original_router", None)) is not None:
            context = route.include_context
            found += _api_routes(included.routes, prefix + context.prefix, inherited + tuple(context.dependencies))
        elif (nested := getattr(route, "routes", None)) is not None:
            found += _api_routes(list(nested), prefix + getattr(route, "path", ""), inherited)
    return found


def _requires_auth(dependant: Any, authenticate: Any) -> bool:
    return any(d.call is authenticate or _requires_auth(d, authenticate) for d in dependant.dependencies)


def unprotected_routes(app: Any, authenticate: Any) -> list[tuple[str, str]]:
    """Routes neither allowlisted nor depending on ``authenticate``, directly, through another
    dependency, or through the include that added them."""
    allowlisted = PUBLIC_ROUTES | SECRET_AUTHENTICATED_ROUTES
    return sorted(
        (method, path)
        for method, path, route, inherited in _api_routes(list(app.routes))
        if (method, path) not in allowlisted
        and not _requires_auth(route.dependant, authenticate)
        and not any(getattr(d, "dependency", None) is authenticate for d in inherited)
    )


def test_route_manifest_every_route_declares_authentication(harness: Harness) -> None:
    """TEN-02: every route is authenticated unless explicitly allowlisted, checked on the routes the
    app serves (not on an anonymous probe, which a route refusing an empty header would also pass)."""
    app = harness.client.app
    discovered = {(method, path) for method, path, _, _ in _api_routes(list(app.routes))}  # type: ignore[attr-defined]
    operations = {(m.upper(), p) for p, ops in app.openapi()["paths"].items() for m in ops}  # type: ignore[attr-defined]
    assert len(operations) >= 20
    assert operations <= discovered, "route discovery missed operations of the contract"
    assert discovered >= SECRET_AUTHENTICATED_ROUTES, "route discovery missed routes hidden from the contract"
    assert unprotected_routes(app, harness.container.identity.auth.authenticate) == []


def test_the_route_manifest_reports_a_route_that_skips_authentication(harness: Harness) -> None:
    """The check itself: a route that only looks for an Authorization header (an anonymous probe gets
    401, yet any token is accepted), or one hidden from the contract, is reported."""
    from fastapi import APIRouter, Request

    from app.kernel.errors import AuthenticationError

    router = APIRouter()

    @router.get("/manifest-probe/header-only")
    def header_only(request: Request) -> dict[str, str]:
        if not request.headers.get("authorization"):
            raise AuthenticationError()
        return {"data": "any token works"}

    @router.get("/manifest-probe/hidden", include_in_schema=False)
    def hidden() -> dict[str, str]:
        return {"data": "anyone"}

    app = harness.client.app
    app.include_router(router, prefix="/api/v1")  # type: ignore[attr-defined]
    assert unprotected_routes(app, harness.container.identity.auth.authenticate) == [
        ("GET", "/api/v1/manifest-probe/header-only"),
        ("GET", "/api/v1/manifest-probe/hidden"),
    ]


def _declarations(dependant: Any) -> list[Permission | Access]:
    found: list[Permission | Access] = []
    for dependency in dependant.dependencies:
        if isinstance(dependency.call, Requires):
            found.append(dependency.call.requirement)
        found += _declarations(dependency)
    return found


def registered_permissions() -> set[Permission]:
    """Every permission a module declares in its domain."""
    found = set()
    for module in pkgutil.iter_modules(app.modules.__path__):
        domain = importlib.import_module(f"app.modules.{module.name}.domain")
        found |= {value for value in vars(domain).values() if isinstance(value, Permission)}
    return found


def authorization_problems(app: Any) -> list[str]:
    """Routes that do not declare, exactly once, what authorizes them (TEN-02).

    The declaration is ``requires(...)`` in the route's ``dependencies``: a permission a module
    registers in its domain, or an ``Access`` kind for a route no permission governs. An access kind
    must fit the path: ``ANY_MEMBER`` names an organization in it, the others do not.
    """
    registered = registered_permissions()
    problems = []
    allowlisted = PUBLIC_ROUTES | SECRET_AUTHENTICATED_ROUTES
    for method, path, route, _ in _api_routes(list(app.routes)):
        if (method, path) in allowlisted:
            continue
        declared = _declarations(route.dependant)
        if len(declared) != 1:
            problems.append(f"{method} {path}: declares {len(declared)} authorization requirements, not one")
            continue
        [requirement] = declared
        if isinstance(requirement, Permission):
            if requirement not in registered:
                problems.append(f"{method} {path}: {requirement.name} is not a permission a module registers")
        elif (requirement is Access.ANY_MEMBER) != ("{org_id}" in path):
            problems.append(f"{method} {path}: {requirement.value!r} does not fit the path")
    return sorted(problems)


def test_route_manifest_every_route_declares_what_authorizes_it(harness: Harness) -> None:
    """TEN-02: not only authentication. Each route says which permission, or which kind of access,
    it needs, and a permission it names is evaluated while the route runs (see test_authorization)."""
    app = harness.client.app
    assert authorization_problems(app) == []
    declared = [d for _, _, route, _ in _api_routes(list(app.routes)) for d in _declarations(route.dependant)]  # type: ignore[attr-defined]
    assert {Access.OWN_ACCOUNT, Access.ANY_MEMBER, Access.OPERATOR} <= set(declared)
    assert len({d for d in declared if isinstance(d, Permission)}) >= 5


def test_the_route_manifest_reports_a_route_without_a_valid_authorization_declaration(harness: Harness) -> None:
    """A route that authenticates, resolves its context, and even calls ``authorize``, but declares nothing
    for the manifest, declares two things, declares a permission no module registers, or an access kind
    that does not fit its path."""
    from fastapi import APIRouter, Depends

    from app.kernel.http.auth import requires
    from app.modules.projects.domain import PROJECT_READ

    auth = harness.container.identity.auth
    unregistered = Permission("thing:do", personal=True)
    router = APIRouter(dependencies=[Depends(auth.authenticate)])

    @router.get("/manifest-probe/none")
    def none() -> dict[str, str]:
        return {}

    @router.get("/manifest-probe/two", dependencies=[requires(PROJECT_READ), requires(Access.OWN_ACCOUNT)])
    def two() -> dict[str, str]:
        return {}

    @router.get("/manifest-probe/unregistered", dependencies=[requires(unregistered)])
    def unknown_permission() -> dict[str, str]:
        return {}

    @router.get("/manifest-probe/orgs/{org_id}/own", dependencies=[requires(Access.OWN_ACCOUNT)])
    def own_in_org(org_id: str) -> dict[str, str]:
        return {}

    @router.get("/manifest-probe/member", dependencies=[requires(Access.ANY_MEMBER)])
    def member_without_org() -> dict[str, str]:
        return {}

    @router.get("/manifest-probe/ok", dependencies=[requires(PROJECT_READ)])
    def declared_ok() -> dict[str, str]:
        return {}

    app = harness.client.app
    app.include_router(router, prefix="/api/v1")  # type: ignore[attr-defined]
    assert authorization_problems(app) == [
        "GET /api/v1/manifest-probe/member: 'any member' does not fit the path",
        "GET /api/v1/manifest-probe/none: declares 0 authorization requirements, not one",
        "GET /api/v1/manifest-probe/orgs/{org_id}/own: 'own account' does not fit the path",
        "GET /api/v1/manifest-probe/two: declares 2 authorization requirements, not one",
        "GET /api/v1/manifest-probe/unregistered: thing:do is not a permission a module registers",
    ]


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


def test_text_with_a_nul_character_is_invalid_input_not_a_server_error(harness: Harness) -> None:
    """PostgreSQL text cannot hold U+0000, so every input model refuses it (API-02)."""
    headers = harness.login("user_a")
    harness.directory.set("user_a", [ExternalMembership("org_nul", Role.ADMIN)])
    for path in ("/api/v1/projects", "/api/v1/orgs/org_nul/projects"):
        response = harness.client.post(path, json={"name": "a\u0000b"}, headers=headers)
        assert response.status_code == 422, path
        assert response.json()["code"] == "VALIDATION_FAILED"
        assert "a\\u0000b" not in response.text  # the submitted value is never echoed (JSON escapes NUL)


def test_a_body_of_many_unknown_keys_is_refused_as_one_error(harness: Harness) -> None:
    """Each unknown key would be a validation error of its own, and building them for a body within the
    size limit costs many times its size in memory. Past what a response lists, the body is refused as
    a whole, with one error (§15.3)."""
    headers = {**harness.login("user_a"), "Content-Type": "application/json"}
    body = '{"name":"x",' + ",".join(f'"k{i}":0' for i in range(20_000)) + "}"
    response = harness.client.post("/api/v1/projects", content=body, headers=headers)
    assert response.status_code == 422
    problem = response.json()
    assert problem["code"] == "VALIDATION_FAILED"
    assert [(error["loc"], error["type"]) for error in problem["errors"]] == [(["body"], "value_error")]
    assert "Too many unknown fields." in problem["errors"][0]["msg"]
    assert "errors_truncated" not in problem


def test_a_validation_response_lists_at_most_20_errors(harness: Harness) -> None:
    """A body can fail in more ways than a response lists: it copies the first 20 errors, and says
    that there were more."""
    headers = {**harness.login("user_a"), "Content-Type": "application/json"}
    body = "{" + ",".join(f'"k{i}":0' for i in range(20)) + "}"  # 20 unknown keys and no name: 21 errors
    problem = harness.client.post("/api/v1/projects", content=body, headers=headers).json()
    assert problem["code"] == "VALIDATION_FAILED"
    assert len(problem["errors"]) == 20
    assert problem["errors_truncated"] is True
    one = harness.client.post("/api/v1/projects", json={"name": ""}, headers=headers).json()
    assert len(one["errors"]) == 1
    assert "errors_truncated" not in one


def test_the_contract_declares_the_validation_problem_the_api_returns(harness: Harness) -> None:
    """DEC-007: the OpenAPI document is the frontend's source of truth, so its 422 is the problem body
    with field errors that the API sends, under the content type it sends it with, not FastAPI's default
    {"detail": [...]} as application/json."""
    from app.kernel.http.schemas import ValidationProblem

    document = harness.client.app.openapi()  # type: ignore[attr-defined]
    declared = [
        operation["responses"]["422"]["content"]["application/problem+json"]["schema"]
        for path_item in document["paths"].values()
        for operation in path_item.values()
        if "422" in operation["responses"]
    ]
    assert len(declared) >= 10
    assert all(schema == {"$ref": "#/components/schemas/ValidationProblem"} for schema in declared)
    schemas = document["components"]["schemas"]
    assert "HTTPValidationError" not in schemas
    assert "ValidationError" not in schemas
    assert set(schemas["ValidationProblem"]["required"]) == {
        "type",
        "title",
        "status",
        "code",
        "detail",
        "request_id",
        "errors",
    }
    headers = {**harness.login("user_a"), "Content-Type": "application/json"}
    truncated = "{" + ",".join(f'"k{i}":0' for i in range(20)) + "}"
    refused_whole = '{"name":"x",' + ",".join(f'"k{i}":0' for i in range(100)) + "}"
    for body in ('{"name": ""}', truncated, refused_whole):
        response = harness.client.post("/api/v1/projects", content=body, headers=headers)
        assert response.status_code == 422
        ValidationProblem.model_validate(response.json(), strict=True)


@pytest.mark.parametrize(
    ("method", "path", "allowed"),
    [
        ("PUT", "/api/v1/health", {"GET"}),
        # One route per method share these paths; Allow names the methods of all of them.
        ("PUT", "/api/v1/projects", {"GET", "POST"}),
        ("DELETE", "/api/v1/orgs/org_probe/projects", {"GET", "POST"}),
        ("PATCH", "/api/v1/projects/00000000-0000-4000-8000-000000000000", {"DELETE"}),
    ],
)
def test_method_not_allowed_names_the_allowed_methods(
    harness: Harness, method: str, path: str, allowed: set[str]
) -> None:
    """RFC 9110 §15.5.6: a 405 carries Allow, listing every method the target resource supports."""
    response = harness.client.request(method, path)
    assert response.status_code == 405
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == "METHOD_NOT_ALLOWED"
    assert {m.strip() for m in response.headers["allow"].split(",")} == allowed


def test_deeply_nested_input_is_invalid_not_a_server_error(harness: Harness) -> None:
    headers = harness.login("user_a")
    body = '{"name": ' + "[" * 900 + "]" * 900 + "}"  # deep, yet within what the JSON parser accepts
    response = harness.client.post(
        "/api/v1/projects", content=body, headers={**headers, "Content-Type": "application/json"}
    )
    assert response.status_code == 422


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


def test_a_readiness_flood_holds_one_thread_and_liveness_stays_fast(make_harness: Any) -> None:
    """API-06: concurrent /ready calls share one database check per instance, so a flood of them
    (anonymous, during an outage) holds at most one thread, and /health never waits for a thread."""
    import threading

    import anyio
    import httpx

    from app.kernel.infrastructure.db import REQUEST_THREADS, Database
    from tests.integration.test_request_admission import silent_database

    inside = peak = 0
    lock = threading.Lock()
    checking = threading.Event()
    statuses: list[tuple[int, Any]] = []
    slowest = 0.0
    connect_timeout = 1.0
    with silent_database() as url:
        database = Database(
            url, api_pool_size=5, worker_pool_size=3, connect_timeout=connect_timeout, statement_timeout_ms=1000
        )
        transaction = database.transaction

        @contextmanager
        def counted(role: str, context: Any) -> Iterator[Any]:
            nonlocal inside, peak
            with lock:
                inside += 1
                peak = max(peak, inside)
            checking.set()
            try:
                with transaction(role, context) as conn:
                    yield conn
            finally:
                with lock:
                    inside -= 1

        database.transaction = counted  # type: ignore[method-assign]
        h = make_harness(database=database)

        async def main() -> float:
            anyio.to_thread.current_default_thread_limiter().total_tokens = REQUEST_THREADS  # as at startup
            transport = httpx.ASGITransport(app=h.client.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://api.test") as client:

                async def ready() -> None:
                    nonlocal slowest
                    started = time.monotonic()
                    response = await client.get("/api/v1/ready")
                    slowest = max(slowest, time.monotonic() - started)
                    statuses.append((response.status_code, response.json()))

                with anyio.fail_after(30):
                    async with anyio.create_task_group() as tg:
                        for _ in range(REQUEST_THREADS + 5):
                            tg.start_soon(ready)
                        while not checking.is_set():
                            await anyio.sleep(0.01)
                        started = time.monotonic()
                        assert (await client.get("/api/v1/health")).status_code == 200
                        return time.monotonic() - started

        health_seconds = anyio.run(main)
    assert health_seconds < 0.5, f"/health took {health_seconds:.2f} s"
    assert peak == 1, f"{peak} threads checked the database at once"
    # Each caller waited for at most the check in progress, not for the checks of those ahead of it.
    assert slowest < 2 * connect_timeout + 1, f"the slowest /ready took {slowest:.1f} s"
    not_ready = {"status": "not_ready", "checks": {"database": "unavailable"}}
    assert statuses == [(503, not_ready)] * (REQUEST_THREADS + 5)


def get_while_every_request_thread_is_busy(app: Any, path: str) -> int:
    """The status of ``GET path`` while every request thread (AnyIO's default limiter) is held."""
    import threading

    import anyio
    import httpx

    async def main() -> int:
        threads = anyio.to_thread.current_default_thread_limiter()
        threads.total_tokens = 2
        release = threading.Event()
        async with anyio.create_task_group() as tg:
            try:
                for _ in range(threads.total_tokens):
                    tg.start_soon(anyio.to_thread.run_sync, release.wait, 10)
                while threads.borrowed_tokens < threads.total_tokens:
                    await anyio.sleep(0.01)
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(transport=transport, base_url="http://api.test") as client:
                    with anyio.fail_after(2):
                        return (await client.get(path)).status_code
            finally:
                release.set()

    return anyio.run(main)


def test_liveness_needs_no_thread(harness: Harness) -> None:
    """API-06: /health makes no dependency call, so it answers even when every request thread is
    busy, for example waiting for a database that stopped responding."""
    assert get_while_every_request_thread_is_busy(harness.client.app, "/api/v1/health") == 200


def test_readiness_checks_the_database_on_a_thread_of_its_own(harness: Harness) -> None:
    """API-06: the database check needs a thread, but never one of the request threads, so /ready
    still answers when they are all busy, for example with threads waiting for connections."""
    assert get_while_every_request_thread_is_busy(harness.client.app, "/api/v1/ready") == 200


def test_analytics_failure_does_not_break_requests(make_harness: Any) -> None:
    class Broken:
        def capture(self, *_args: Any, **_kwargs: Any) -> None:
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
    responses = [h.client.post("/api/v1/llm/check", headers=a) for _ in range(3)]
    assert [r.status_code for r in responses] == [200, 200, 429]
    assert responses[2].json()["code"] == "RATE_LIMITED"  # the caller's own limit
    assert h.client.post("/api/v1/llm/check", headers=b).status_code == 200  # global count reaches 3
    exhausted = h.client.post("/api/v1/llm/check", headers=b)
    assert exhausted.status_code == 429
    assert exhausted.json()["code"] == "BUDGET_EXCEEDED"  # LLM-02: told apart from the caller's own limit
    assert int(exhausted.headers["Retry-After"]) > 60
    h.clock.advance(minutes=1)
    still_over = h.client.post("/api/v1/llm/check", headers=a)
    assert still_over.json()["code"] == "BUDGET_EXCEEDED"  # the daily budget outlasts the per-user window


def test_llm_check_refused_by_the_concurrency_bound_uses_no_quota(make_harness: Any) -> None:
    h = make_harness({"llm_checks_per_user_per_minute": 1, "llm_max_concurrent_checks": 1})
    headers = h.login("user_a")
    service = h.container.llm.service
    assert service._in_flight.acquire(blocking=False)  # another check is in flight
    try:
        assert h.client.post("/api/v1/llm/check", headers=headers).status_code == 429
    finally:
        service._in_flight.release()
    assert h.client.post("/api/v1/llm/check", headers=headers).status_code == 200  # the quota is untouched


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


def clerk_send(
    h: Harness, event_type: str, data: dict[str, Any], msg_id: str | None = None, timestamp: int | None = None
) -> Any:
    payload = json.dumps({"type": event_type, "data": data, "object": "event"}).encode()
    msg_id = msg_id or f"msg_{time.time_ns()}"
    ts = int(time.time()) if timestamp is None else timestamp
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


def test_a_clerk_webhook_signed_an_hour_ago_is_refused_and_leaves_no_trace(clerk_harness: Harness) -> None:
    """SEC-13: a captured, correctly signed delivery cannot be replayed later (the signature covers the timestamp).
    The refusal is a 400 and nothing is recorded, so the same message signed now is still processed."""
    h = clerk_harness
    stale = clerk_send(h, "user.deleted", {"id": "user_x"}, msg_id="msg_replay", timestamp=int(time.time()) - 3600)
    assert stale.status_code == 400
    assert stale.json()["code"] == "WEBHOOK_SIGNATURE_INVALID"
    assert superuser_query(h.database_url, "SELECT count(*) FROM app.identity_events") == [(0,)]
    fresh = clerk_send(h, "user.deleted", {"id": "user_x"}, msg_id="msg_replay")
    assert (fresh.status_code, fresh.json()["status"]) == (200, "processed")


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


SUPABASE_CLIENT_ROLES = ("anon", "authenticated", "service_role")
LEDGER_WRITES = "SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER"


@contextmanager
def supabase_client_roles(url: str) -> Iterator[None]:
    """Supabase's three client roles, created if the server lacks them and dropped afterwards if this made them.

    Roles belong to the whole server, which other test sessions share, so a role that already exists is left alone.
    """
    made: list[str] = []
    for role in SUPABASE_CLIENT_ROLES:
        if not superuser_query(url, "SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)):
            try:
                superuser_query(url, f"CREATE ROLE {role} NOLOGIN")
                made.append(role)
            except (psycopg.errors.DuplicateObject, psycopg.errors.UniqueViolation):
                pass  # another session made it first
    try:
        yield
    finally:
        for role in made:
            superuser_query(url, f"DROP OWNED BY {role}")
            superuser_query(url, f"DROP ROLE IF EXISTS {role}")


def test_the_migration_ledger_is_private(db: str) -> None:
    """On Supabase, ``public`` is exposed through the Data API, and its tables start out granted to anon,
    authenticated, and service_role (which also bypasses row-level security). The ledger must not be."""
    assert superuser_query(
        db, "SELECT relrowsecurity FROM pg_class WHERE oid = 'public.app_schema_migrations'::regclass"
    ) == [(True,)]
    with supabase_client_roles(db):
        for role in SUPABASE_CLIENT_ROLES:  # what Supabase's default privileges give a new table in public
            superuser_query(db, f"GRANT ALL ON public.app_schema_migrations TO {role}")
        migrations.migrate(db)  # the ledger statements run on every migrate, so an existing ledger is fixed too
        for role in ("app_api", "app_worker", *SUPABASE_CLIENT_ROLES):
            privilege = f"SELECT has_table_privilege(%s, 'public.app_schema_migrations', '{LEDGER_WRITES}')"
            assert superuser_query(db, privilege, (role,)) == [(False,)], role
            read = "SELECT has_table_privilege(%s, 'public.app_schema_migrations', 'SELECT')"
            assert superuser_query(db, read, (role,)) == [(False,)], role


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
