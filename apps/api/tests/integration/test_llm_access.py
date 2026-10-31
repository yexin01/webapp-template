"""POST /llm/check follows the diagnostics operator policy (spec section 10, LLM-06; ADR-0004 section 23).

The check spends a daily budget that every account shares, so it is an operator tool like the Integrations page:
whoever may not use diagnostics gets the 404 a missing route gets, and a refused call spends neither that
person's limit nor the shared budget. The policy itself is tests/unit/test_diagnostics_access.py; here it is
shown to be the same one.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from tests.conftest import Harness, superuser_query
from tests.integration.test_diagnostics_access import LOCAL, OPERATORS, asked

pytestmark = pytest.mark.db

CHECK = "/api/v1/llm/check"
INTEGRATIONS = "/api/v1/diagnostics/integrations"


def test_an_operator_runs_the_check(make_harness: Callable[..., Harness]) -> None:
    h = make_harness(OPERATORS)
    response = h.client.post(CHECK, headers=h.login("user_ops"))
    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "provider": "mock",
        "model": "mock-model",
        "latency_ms": 0,
        "error_code": None,
    }


def test_anyone_else_signed_in_gets_the_answer_of_a_missing_route(make_harness: Callable[..., Harness]) -> None:
    h = make_harness(OPERATORS)
    refused = h.client.post(CHECK, headers=h.login("user_a"))
    assert refused.status_code == 404
    assert refused.headers["content-type"] == "application/problem+json"
    # The same answer as the diagnostics routes give: nothing tells an outsider this route exists.
    diagnostics = h.client.get(INTEGRATIONS, headers=h.login("user_a"))
    drop = {"request_id"}
    assert {k: v for k, v in refused.json().items() if k not in drop} == {
        k: v for k, v in diagnostics.json().items() if k not in drop
    }
    assert refused.json()["code"] == "NOT_FOUND"


def test_a_refused_call_spends_nothing_so_outsiders_cannot_use_up_the_shared_budget(
    make_harness: Callable[..., Harness],
) -> None:
    h = make_harness({**OPERATORS, "llm_checks_per_user_per_minute": 2, "llm_checks_global_per_day": 3})
    outsider = h.login("user_a")
    assert [h.client.post(CHECK, headers=outsider).status_code for _ in range(10)] == [404] * 10
    assert superuser_query(h.database_url, "SELECT count(*) FROM app.rate_limit_counters") == [(0,)]
    assert superuser_query(h.database_url, "SELECT count(*) FROM app.audit_log") == [(0,)]
    assert h.analytics.events == []
    # The whole budget is still there for the operators.
    first, second = h.login("user_ops"), h.login("user_second")
    assert [h.client.post(CHECK, headers=first).status_code for _ in range(2)] == [200, 200]
    assert h.client.post(CHECK, headers=second).status_code == 200  # the third check of the day
    exhausted = h.client.post(CHECK, headers=second)
    assert (exhausted.status_code, exhausted.json()["code"]) == (429, "BUDGET_EXCEEDED")


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
def test_it_answers_exactly_where_diagnostics_say_a_caller_may_use_them(
    make_harness: Callable[..., Harness], settings: dict[str, Any]
) -> None:
    """One policy: the check runs (200) where /diagnostics/access says available, and is a 404 everywhere else,
    for a listed operator, another listed one, an account that is no operator, and an operator's impersonated session."""
    h = make_harness(settings)
    for user, actor in [("user_ops", None), ("user_second", None), ("user_a", None), ("user_ops", "user_staff")]:
        if actor is not None and not settings.get("clerk_allow_impersonation"):
            continue  # the API refuses such a session before any route (403): there is nothing to compare
        response = h.client.post(CHECK, headers=h.login(user, actor))
        assert response.status_code in (200, 404), (user, actor, response.text)
        assert (response.status_code == 200) is asked(h, user, actor), (user, actor)


def test_the_check_still_needs_a_session(make_harness: Callable[..., Harness]) -> None:
    h = make_harness(OPERATORS)
    assert h.client.post(CHECK).status_code == 401
    assert h.client.post(CHECK, headers={"Authorization": "Bearer not-a-token"}).status_code == 401
