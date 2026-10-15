"""Reading memberships from the Clerk Backend API (DEC-009, §16.4).

The adapter's own HTTP client is used, on an in-process mock transport, so its
credentials, request paths, and pagination are what the tests see. Nothing
leaves the machine.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.kernel.errors import DependencyUnavailableError
from app.kernel.tenancy import Role
from app.modules.identity.adapters import clerk
from app.modules.identity.adapters.clerk import ClerkMembershipDirectory
from app.modules.identity.domain import ExternalMembership
from app.modules.identity.ports import MembershipListTooLargeError

API = "https://api.clerk.invalid"
SECRET_KEY = "sk_test_only_x"


class FakeMonotonic:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def directory(
    handler: Callable[[httpx.Request], httpx.Response], clock: FakeMonotonic | None = None
) -> ClerkMembershipDirectory:
    return ClerkMembershipDirectory(
        secret_key=SECRET_KEY, api_url=API, clock=clock or FakeMonotonic(), transport=httpx.MockTransport(handler)
    )


def membership(org_id: str, role: str = "org:member") -> dict[str, Any]:
    return {"object": "organization_membership", "role": role, "organization": {"id": org_id, "name": "x"}}


class ClerkUsers:
    """The memberships endpoint: authenticates, routes by user, and pages by ``limit`` and ``offset``."""

    def __init__(self, memberships: dict[str, int]) -> None:
        self.memberships = memberships  # user ID -> number of organizations
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.headers.get("authorization") != f"Bearer {SECRET_KEY}":
            return httpx.Response(401, json={"errors": [{"code": "authentication_invalid"}]})
        prefix, _, rest = request.url.path.partition("/v1/users/")
        user_id, _, endpoint = rest.partition("/")
        if prefix or endpoint != "organization_memberships" or user_id not in self.memberships:
            return httpx.Response(404, json={"errors": []})
        offset, limit = int(request.url.params["offset"]), int(request.url.params["limit"])
        total = self.memberships[user_id]
        page = [membership(f"org_{n:05d}") for n in range(offset, min(offset + limit, total))]
        return httpx.Response(200, json={"data": page, "total_count": total})

    def pages(self) -> list[tuple[str, str]]:
        return [(r.url.params["limit"], r.url.params["offset"]) for r in self.requests]


def test_reads_memberships_and_roles() -> None:
    body = {"data": [membership("org_a", "org:admin"), membership("org_b")], "total_count": 2}
    d = directory(lambda request: httpx.Response(200, json=body))
    assert d.list_user_memberships("user_1") == [
        ExternalMembership("org_a", Role.ADMIN),
        ExternalMembership("org_b", Role.MEMBER),
    ]


@pytest.mark.parametrize(
    ("count", "pages"),
    [
        (3, [("500", "0")]),
        (1_000, [("500", "0"), ("500", "500"), ("500", "1000")]),  # a full last page: one more, empty, page
        (1_234, [("500", "0"), ("500", "500"), ("500", "1000")]),
        (4_999, [("500", str(offset)) for offset in range(0, 5_000, 500)]),  # the most one read returns
    ],
)
def test_sends_the_secret_and_reads_every_page(count: int, pages: list[tuple[str, str]]) -> None:
    clerk_api = ClerkUsers({"user_1": count})
    d = directory(clerk_api)
    read = d.list_user_memberships("user_1")
    assert [m.external_org_id for m in read] == [f"org_{n:05d}" for n in range(count)]
    assert clerk_api.pages() == pages
    assert {(r.url.host, r.url.path) for r in clerk_api.requests} == {
        ("api.clerk.invalid", "/v1/users/user_1/organization_memberships")
    }


@pytest.mark.parametrize("count", [5_000, 1_000_000])  # 5,000: every page full, though nothing follows
def test_a_read_that_may_be_cut_off_is_refused_without_opening_the_breaker(
    caplog: pytest.LogCaptureFixture, count: int
) -> None:
    """Every page full: the list may go on, and applying it would delete the memberships it lacks.

    The read fails closed (503). It is one user's list, not an outage, so other
    users' reads still reach Clerk.
    """
    clerk_api = ClerkUsers({"user_big": count, "user_1": 2})
    d = directory(clerk_api)
    with caplog.at_level(logging.WARNING, logger=clerk.__name__), pytest.raises(MembershipListTooLargeError):
        d.list_user_memberships("user_big")
    assert len(clerk_api.requests) == clerk._MEMBERSHIP_MAX_PAGES
    assert issubclass(MembershipListTooLargeError, DependencyUnavailableError)  # a 503, never a partial read
    records = [r for r in caplog.records if r.name == clerk.__name__]
    assert [r.getMessage() for r in records] == ["clerk_directory_truncated"]
    assert "user_big" not in str([vars(r) for r in records])

    assert [m.external_org_id for m in d.list_user_memberships("user_1")] == ["org_00000", "org_00001"]
    assert len(clerk_api.requests) == clerk._MEMBERSHIP_MAX_PAGES + 1


def test_a_rejected_secret_is_an_outage() -> None:
    d = ClerkMembershipDirectory(
        secret_key="sk_test_only_revoked", api_url=API, transport=httpx.MockTransport(ClerkUsers({"user_1": 1}))
    )
    with pytest.raises(DependencyUnavailableError):
        d.list_user_memberships("user_1")


def test_an_unknown_user_has_no_memberships() -> None:
    d = directory(lambda request: httpx.Response(404, json={"errors": []}))
    assert d.list_user_memberships("user_gone") == []


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="<html>maintenance</html>"),  # not JSON
        httpx.Response(200, json=None),
        httpx.Response(200, json={"total_count": 0}),  # no data: never read as "no memberships"
        httpx.Response(200, json={"data": "x"}),
        httpx.Response(200, json={"data": ["x"]}),
        httpx.Response(200, json={"data": [{"role": "org:admin", "organization": "org_a"}]}),
        httpx.Response(200, json=[membership("org_a"), 7]),
        # One unreadable item makes the whole read fail: dropping it would remove that membership.
        httpx.Response(200, json={"data": [membership("org_a"), {"role": "org:admin", "organization": None}]}),
        httpx.Response(200, json={"data": [membership("org_a"), {"role": "org:admin", "organization": {"id": 7}}]}),
    ],
)
def test_a_malformed_answer_is_an_outage_not_a_server_error(response: httpx.Response) -> None:
    d = directory(lambda request: response)
    with pytest.raises(DependencyUnavailableError):
        d.list_user_memberships("user_1")


def test_after_a_failure_reads_fail_fast_for_a_short_time() -> None:
    """During an outage, requests do not each wait out the timeout (§16.4)."""
    calls: list[httpx.Request] = []
    status = {"code": 503}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(status["code"], json={"data": [membership("org_a")]})

    clock = FakeMonotonic()
    d = directory(handler, clock)
    with pytest.raises(DependencyUnavailableError):
        d.list_user_memberships("user_1")
    assert len(calls) == 1
    status["code"] = 200
    clock.now += clerk._DIRECTORY_COOLDOWN_SECONDS - 0.1
    with pytest.raises(DependencyUnavailableError):
        d.list_user_memberships("user_1")
    assert len(calls) == 1  # failed fast, no request
    clock.now += 0.1
    assert d.list_user_memberships("user_1") == [ExternalMembership("org_a", Role.MEMBER)]
    assert len(calls) == 2


def test_connection_failures_open_the_breaker_too() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise httpx.ConnectTimeout("timed out")

    d = directory(handler)
    for _ in range(3):
        with pytest.raises(DependencyUnavailableError):
            d.list_user_memberships("user_1")
    assert len(calls) == 1


def test_each_page_is_bounded_by_a_short_timeout() -> None:
    d = ClerkMembershipDirectory(secret_key=SECRET_KEY, api_url=API)
    try:
        assert d._client.timeout == httpx.Timeout(5.0)
    finally:
        d.close()
