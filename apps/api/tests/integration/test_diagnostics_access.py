"""GET /diagnostics/access: whether the caller may use the diagnostics routes, for the web app to decide what to show.

It answers every signed-in caller, applies exactly the policy of the diagnostics routes, and has no effect: no
audit entry, no rate-limit counter, and nothing about who else is an operator. The routes themselves stay the
authority. The policy per environment is tests/unit/test_diagnostics_access.py.
"""

from __future__ import annotations

import base64
from collections.abc import Callable
from typing import Any

import pytest

from app.kernel.authorization import Access
from tests.conftest import Harness, superuser_query
from tests.integration.test_platform import _api_routes, _declarations

pytestmark = pytest.mark.db

ACCESS = "/api/v1/diagnostics/access"
INTEGRATIONS = "/api/v1/diagnostics/integrations"
OPERATORS = {"diagnostics_operator_ids": "user_ops,user_second"}
# Local development holds a Clerk instance, so its settings name one (test keys, as the other tests build them).
LOCAL = {
    "app_env": "local",
    "clerk_publishable_key": "pk_test_" + base64.b64encode(b"example.clerk.accounts.dev$").decode(),
    "clerk_secret_key": "sk_test_" + "c" * 30,
}


def asked(h: Harness, user: str, actor: str | None = None) -> bool:
    """The answer for ``user``, after checking its shape: a 200 with the one field, for anyone signed in."""
    response = h.client.get(ACCESS, headers=h.login(user, actor))
    assert response.status_code == 200, response.text
    body = response.json()
    assert list(body) == ["available"], body
    assert isinstance(body["available"], bool), body
    available: bool = body["available"]
    return available


def test_requires_authentication(harness: Harness) -> None:
    assert harness.client.get(ACCESS).status_code == 401
    assert harness.client.get(ACCESS, headers={"Authorization": "Bearer not-a-token"}).status_code == 401


def test_declares_the_access_it_needs(harness: Harness) -> None:
    app = harness.client.app
    [(_, _, route, _)] = [r for r in _api_routes(list(app.routes)) if r[1] == ACCESS]  # type: ignore[attr-defined]
    assert _declarations(route.dependant) == [Access.OWN_ACCOUNT]


def test_an_operator_may_and_anyone_else_signed_in_is_told_so_without_an_error(
    make_harness: Callable[..., Harness],
) -> None:
    h = make_harness(OPERATORS)
    assert asked(h, "user_ops") is True
    assert asked(h, "user_second") is True
    assert asked(h, "user_a") is False


def test_disabled_diagnostics_are_unavailable_to_everyone(make_harness: Callable[..., Harness]) -> None:
    h = make_harness({**OPERATORS, "diagnostics_enabled": False})
    assert asked(h, "user_ops") is False
    assert asked(h, "user_a") is False


def test_no_operators_listed_opens_diagnostics_to_a_signed_in_user_only_in_local_development(
    make_harness: Callable[..., Harness],
) -> None:
    assert asked(make_harness(LOCAL), "user_a") is True
    assert asked(make_harness(), "user_a") is False  # APP_ENV=test
    listed = make_harness({**LOCAL, **OPERATORS})
    assert asked(listed, "user_a") is False
    assert asked(listed, "user_ops") is True


def test_a_session_someone_else_started_is_unavailable_like_the_routes_say(
    make_harness: Callable[..., Harness],
) -> None:
    h = make_harness({**OPERATORS, "clerk_allow_impersonation": True})
    assert asked(h, "user_ops") is True
    assert asked(h, "user_ops", actor="user_staff") is False


@pytest.mark.parametrize(
    "settings",
    [
        OPERATORS,
        {**OPERATORS, "diagnostics_enabled": False},
        {**OPERATORS, "diagnostics_enabled": True},
        {},
        LOCAL,
        {**LOCAL, **OPERATORS},
        {**LOCAL, "diagnostics_enabled": False},
        {**OPERATORS, "clerk_allow_impersonation": True},
    ],
    ids=["operators", "disabled", "enabled", "none listed", "local", "local with operators", "local off", "actors"],
)
def test_it_agrees_with_the_integrations_route_for_everyone(
    make_harness: Callable[..., Harness], settings: dict[str, Any]
) -> None:
    """One policy: the integrations report answers 200 exactly where this says available."""
    h = make_harness(settings)
    for user, actor in [("user_ops", None), ("user_second", None), ("user_a", None), ("user_ops", "user_staff")]:
        if actor is not None and not settings.get("clerk_allow_impersonation"):
            continue  # the API refuses such a session before any route (403): there is nothing to compare
        listed = h.client.get(INTEGRATIONS, headers=h.login(user, actor))
        assert listed.status_code in (200, 404), (user, actor, listed.text)
        assert asked(h, user, actor) is (listed.status_code == 200), (user, actor)


def test_it_writes_no_audit_entry_and_spends_no_rate_limit(make_harness: Callable[..., Harness]) -> None:
    h = make_harness({**OPERATORS, "diagnostics_runs_per_user_per_minute": 2})
    for _ in range(6):
        asked(h, "user_ops")
        asked(h, "user_a")
    assert superuser_query(h.database_url, "SELECT count(*) FROM app.audit_log") == [(0,)]
    assert superuser_query(h.database_url, "SELECT count(*) FROM app.rate_limit_counters") == [(0,)]
    # Nothing was spent: the operator still has the whole budget for running checks, and a refused attempt is
    # the first audited one.
    operator, outsider = h.login("user_ops"), h.login("user_a")
    check = f"{INTEGRATIONS}/database/check"
    assert [h.client.post(check, headers=operator).status_code for _ in range(3)] == [200, 200, 429]
    assert h.client.post(check, headers=outsider).status_code == 404
    assert superuser_query(h.database_url, "SELECT outcome FROM app.audit_log WHERE outcome = 'denied'") == [
        ("denied",)
    ]


def test_it_says_nothing_about_who_else_is_an_operator(make_harness: Callable[..., Harness]) -> None:
    h = make_harness(OPERATORS)
    for user in ("user_ops", "user_a"):
        response = h.client.get(ACCESS, headers=h.login(user))
        assert response.json() == {"available": user == "user_ops"}
        for other in ("user_ops", "user_second", "user_a"):
            if other != user:
                assert other not in response.text
        assert all("user_" not in value for value in response.headers.values())
