"""A signed-in user's writes are limited per minute (SEC-09, DEC-011, WRITES_PER_USER_PER_MINUTE).

The limit is a router dependency that runs after authentication and before the request takes a database
place. It counts every POST, PUT, PATCH and DELETE of one user, whatever the context and the outcome, and
answers 429 with Retry-After past the limit. Routes with a limit of their own and the webhooks do not use it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi import APIRouter, Depends

from app.kernel.authorization import Access
from app.kernel.http.auth import requires
from app.kernel.http.write_limit import limits_writes
from app.kernel.tenancy import Role
from app.modules.identity.domain import ExternalMembership
from tests.conftest import Harness, superuser_query
from tests.integration.test_platform import _api_routes

pytestmark = pytest.mark.db

PROJECTS = "/api/v1/projects"
ACME = "org_acme"
GLOBEX = "org_globex"
UNKNOWN = "00000000-0000-4000-8000-000000000000"


def write(h: Harness, user: str, path: str = PROJECTS, name: str = "p") -> int:
    return h.client.post(path, json={"name": name}, headers=h.login(user)).status_code


def counters(h: Harness) -> dict[str, int]:
    rows = superuser_query(
        h.database_url, "SELECT bucket_key, hits FROM app.rate_limit_counters WHERE bucket_key LIKE %s", ("writes:%",)
    )
    return dict(rows)


def test_the_61st_write_within_a_minute_is_refused(harness: Harness) -> None:
    headers = harness.login("user_a")
    statuses = [harness.client.post(PROJECTS, json={"name": f"p{i}"}, headers=headers).status_code for i in range(60)]
    assert statuses == [201] * 60
    refused = harness.client.post(PROJECTS, json={"name": "one too many"}, headers=headers)
    assert refused.status_code == 429
    assert refused.headers["content-type"] == "application/problem+json"
    assert 1 <= int(refused.headers["retry-after"]) <= 60
    body = refused.json()
    assert (body["status"], body["code"]) == (429, "RATE_LIMITED")
    assert body["request_id"] == refused.headers["x-request-id"]
    # The refused write did nothing, and reads are not limited.
    assert superuser_query(harness.database_url, "SELECT count(*) FROM app.projects") == [(60,)]
    assert harness.client.get(PROJECTS, headers=headers).status_code == 200


def test_another_user_is_not_affected(make_harness: Callable[..., Harness]) -> None:
    h = make_harness({"writes_per_user_per_minute": 2})
    assert [write(h, "user_a") for _ in range(3)] == [201, 201, 429]
    # Same client address, another user: the counter is the user's, not the address's.
    assert [write(h, "user_b") for _ in range(3)] == [201, 201, 429]


def test_the_counter_belongs_to_the_user_across_every_context(make_harness: Callable[..., Harness]) -> None:
    """One counter for a user in the personal context and in each organization; none for an organization."""
    h = make_harness({"writes_per_user_per_minute": 4})
    h.directory.set("user_a", [ExternalMembership(ACME, Role.ADMIN), ExternalMembership(GLOBEX, Role.MEMBER)])
    h.directory.set("user_b", [ExternalMembership(ACME, Role.MEMBER)])
    paths = [PROJECTS, f"/api/v1/orgs/{ACME}/projects", f"/api/v1/orgs/{GLOBEX}/projects", PROJECTS]
    assert [write(h, "user_a", path) for path in paths] == [201] * 4
    assert write(h, "user_a", f"/api/v1/orgs/{ACME}/projects") == 429
    assert write(h, "user_a", PROJECTS) == 429
    # Another member of the same organization still has the whole allowance.
    assert [write(h, "user_b", f"/api/v1/orgs/{ACME}/projects") for _ in range(5)] == [201] * 4 + [429]
    assert sorted(counters(h).values()) == [5, 6]  # an attempt counts, refused or not: one counter per user


def test_reads_are_not_counted(make_harness: Callable[..., Harness]) -> None:
    h = make_harness({"writes_per_user_per_minute": 2})
    headers = h.login("user_a")
    assert [h.client.get(PROJECTS, headers=headers).status_code for _ in range(5)] == [200] * 5
    assert counters(h) == {}
    assert [write(h, "user_a") for _ in range(3)] == [201, 201, 429]
    assert h.client.get(PROJECTS, headers=headers).status_code == 200  # a refused writer can still read


def test_every_attempt_counts_whatever_its_outcome(make_harness: Callable[..., Harness]) -> None:
    """A delete that finds nothing, and a body that is refused, cost the server work too (and a denial an audit row)."""
    h = make_harness({"writes_per_user_per_minute": 3})
    headers = h.login("user_a")
    assert h.client.delete(f"{PROJECTS}/{UNKNOWN}", headers=headers).status_code == 404
    assert h.client.post(PROJECTS, json={}, headers=headers).status_code == 422
    assert h.client.post(PROJECTS, json={"name": "fine"}, headers=headers).status_code == 201
    assert h.client.delete(f"{PROJECTS}/{UNKNOWN}", headers=headers).status_code == 429
    assert list(counters(h).values()) == [4]


def test_a_denied_write_counts_too(make_harness: Callable[..., Harness]) -> None:
    h = make_harness({"writes_per_user_per_minute": 2})
    h.directory.set("user_admin", [ExternalMembership(ACME, Role.ADMIN)])
    h.directory.set("user_member", [ExternalMembership(ACME, Role.MEMBER)])
    path = f"/api/v1/orgs/{ACME}/projects"
    shared = h.client.post(path, json={"name": "shared"}, headers=h.login("user_admin")).json()["id"]
    member = h.login("user_member")  # members may create but not delete: each attempt is audited
    assert [h.client.delete(f"{path}/{shared}", headers=member).status_code for _ in range(3)] == [403, 403, 429]
    audited = superuser_query(h.database_url, "SELECT count(*) FROM app.audit_log WHERE outcome = 'denied'")
    assert audited == [(2,)]


def test_the_window_rolls_over(make_harness: Callable[..., Harness]) -> None:
    h = make_harness({"writes_per_user_per_minute": 2})
    headers = h.login("user_a")
    assert [write(h, "user_a") for _ in range(2)] == [201, 201]
    refused = h.client.post(PROJECTS, json={"name": "late"}, headers=headers)
    assert refused.status_code == 429
    h.clock.advance(seconds=int(refused.headers["retry-after"]))
    assert [write(h, "user_a") for _ in range(3)] == [201, 201, 429]


def test_a_request_without_a_valid_token_spends_nothing(make_harness: Callable[..., Harness]) -> None:
    h = make_harness({"writes_per_user_per_minute": 1})
    assert h.client.post(PROJECTS, json={"name": "x"}).status_code == 401
    assert h.client.post(PROJECTS, json={"name": "x"}, headers={"Authorization": "Bearer nope"}).status_code == 401
    assert counters(h) == {}
    assert write(h, "user_a") == 201


def test_a_refused_write_never_takes_a_request_transaction(
    make_harness: Callable[..., Harness], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The limit runs before the request-scoped transaction, so a flood of refused writes holds no admission place."""
    h = make_harness({"writes_per_user_per_minute": 1})
    database = h.container.kernel.database
    begun: list[int] = []
    original = database.begin_request_scope

    def counting(*args: Any, **kwargs: Any) -> Any:
        begun.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(database, "begin_request_scope", counting)
    assert write(h, "user_a") == 201
    assert len(begun) == 1
    assert [write(h, "user_a") for _ in range(3)] == [429] * 3
    assert len(begun) == 1


def test_the_token_is_still_verified_once_per_request(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    verified: list[str] = []
    original = harness.verifier.verify

    def counting(token: str) -> Any:
        verified.append(token)
        return original(token)

    monkeypatch.setattr(harness.verifier, "verify", counting)
    assert harness.client.post(PROJECTS, json={"name": "x"}, headers=harness.login("user_a")).status_code == 201
    assert len(verified) == 1


def test_routes_with_a_limit_of_their_own_and_the_webhooks_are_not_counted_twice(
    make_harness: Callable[..., Harness],
) -> None:
    h = make_harness({"writes_per_user_per_minute": 1, "diagnostics_operator_ids": "user_a"})
    headers = h.login("user_a")
    assert h.client.post("/api/v1/llm/check", headers=headers).status_code == 200
    assert h.client.post("/api/v1/billing/portal", headers=headers).status_code == 409  # no billing account yet
    assert h.client.post("/api/v1/billing/sync", headers=headers).status_code == 200
    assert h.client.post("/api/v1/diagnostics/integrations/database/check", headers=headers).status_code == 200
    assert h.client.post("/api/v1/webhooks/stripe", content=b"{}").status_code == 400
    assert counters(h) == {}
    assert write(h, "user_a") == 201  # the whole allowance is still there
    kinds = {
        key.split(":")[0]
        for (key,) in superuser_query(h.database_url, "SELECT bucket_key FROM app.rate_limit_counters")
    }
    assert {"llm", "billing-sync", "diagnostics", "writes"} <= kinds  # the others counted under their own keys


# -- Which routes carry the limit ---------------------------------------------------------------

# Write routes that do not use the router dependency, and why. A new write route is limited unless it is listed here.
NOT_COUNTED = {
    ("POST", "/api/v1/billing/checkout"): "its own limit: CHECKOUTS_PER_USER_PER_HOUR",
    ("POST", "/api/v1/billing/portal"): "its own limit: PORTAL_SESSIONS_PER_USER_PER_HOUR",
    ("POST", "/api/v1/billing/sync"): "its own limit: BILLING_SYNCS_PER_USER_PER_HOUR",
    ("POST", "/api/v1/llm/check"): "its own limit: LLM_CHECKS_PER_USER_PER_MINUTE",
    ("POST", "/api/v1/diagnostics/integrations/{key}/check"): "its own limit: DIAGNOSTICS_RUNS_PER_USER_PER_MINUTE",
    ("POST", "/api/v1/webhooks/stripe"): "signature on the raw body, no user",
    ("POST", "/api/v1/webhooks/clerk"): "signature on the raw body, no user",
}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _limits_writes(dependant: Any) -> bool:
    return any(limits_writes(d.call) or _limits_writes(d) for d in dependant.dependencies)


def uncounted_write_routes(app: Any) -> list[tuple[str, str]]:
    return sorted(
        (method, path)
        for method, path, route, _ in _api_routes(list(app.routes))
        if method not in SAFE_METHODS and not _limits_writes(route.dependant)
    )


def test_every_write_route_is_counted_or_says_why_not(harness: Harness) -> None:
    app = harness.client.app
    assert uncounted_write_routes(app) == sorted(NOT_COUNTED)  # type: ignore[attr-defined]
    counted = [
        (method, path)
        for method, path, route, _ in _api_routes(list(app.routes))  # type: ignore[attr-defined]
        if _limits_writes(route.dependant)
    ]
    assert ("POST", PROJECTS) in counted
    assert ("DELETE", f"{PROJECTS}/{{project_id}}") in counted
    assert len([m for m, _ in counted if m == "POST"]) == 2  # personal and organization create


def test_the_manifest_reports_a_write_route_that_skips_the_limit(harness: Harness) -> None:
    auth = harness.container.identity.auth
    kernel = harness.container.kernel
    router = APIRouter(dependencies=[Depends(auth.authenticate)])

    @router.post("/manifest-probe/unlimited", dependencies=[requires(Access.OWN_ACCOUNT)])
    def unlimited() -> dict[str, str]:
        return {}

    limited = APIRouter(dependencies=[Depends(auth.authenticate), kernel.limit_writes(auth)])

    @limited.put("/manifest-probe/limited", dependencies=[requires(Access.OWN_ACCOUNT)])
    def counted() -> dict[str, str]:
        return {}

    @limited.get("/manifest-probe/read", dependencies=[requires(Access.OWN_ACCOUNT)])
    def read() -> dict[str, str]:
        return {}

    app = harness.client.app
    app.include_router(router, prefix="/api/v1")  # type: ignore[attr-defined]
    app.include_router(limited, prefix="/api/v1")  # type: ignore[attr-defined]
    found = uncounted_write_routes(app)
    assert ("POST", "/api/v1/manifest-probe/unlimited") in found
    assert ("PUT", "/api/v1/manifest-probe/limited") not in found


def test_a_counted_route_declares_the_429_it_can_answer(harness: Harness) -> None:
    document = harness.client.app.openapi()  # type: ignore[attr-defined]
    for method, path, route, _ in _api_routes(list(harness.client.app.routes)):  # type: ignore[attr-defined]
        if method in SAFE_METHODS or (method, path) in NOT_COUNTED or not route.include_in_schema:
            continue
        assert "429" in document["paths"][path][method.lower()]["responses"], (method, path)
