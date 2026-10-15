"""Reading memberships from the Clerk Backend API (DEC-009, §16.4).

The HTTP client is pointed at an in-process mock transport; nothing leaves the machine.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.kernel.errors import DependencyUnavailableError
from app.kernel.tenancy import Role
from app.modules.identity.adapters import clerk
from app.modules.identity.adapters.clerk import ClerkMembershipDirectory
from app.modules.identity.domain import ExternalMembership

API = "https://api.clerk.invalid"


class FakeMonotonic:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def directory(
    handler: Callable[[httpx.Request], httpx.Response], clock: FakeMonotonic | None = None
) -> ClerkMembershipDirectory:
    d = ClerkMembershipDirectory(secret_key="sk_test_only_x", api_url=API, clock=clock or FakeMonotonic())
    d._client = httpx.Client(base_url=API, transport=httpx.MockTransport(handler))
    return d


def membership(org_id: str, role: str = "org:member") -> dict[str, Any]:
    return {"object": "organization_membership", "role": role, "organization": {"id": org_id, "name": "x"}}


def test_reads_memberships_and_roles() -> None:
    body = {"data": [membership("org_a", "org:admin"), membership("org_b")], "total_count": 2}
    d = directory(lambda request: httpx.Response(200, json=body))
    assert d.list_user_memberships("user_1") == [
        ExternalMembership("org_a", Role.ADMIN),
        ExternalMembership("org_b", Role.MEMBER),
    ]


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
    d = ClerkMembershipDirectory(secret_key="sk_test_only_x", api_url=API)
    try:
        assert d._client.timeout == httpx.Timeout(5.0)
    finally:
        d.close()
