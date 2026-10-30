"""Deny-by-default authorization policy (TEN-02, ADM-01)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.kernel.authorization import Permission, authorize, is_allowed
from app.kernel.errors import ForbiddenError
from app.kernel.tenancy import ContextKind, Role, TenantContext
from app.modules.billing.domain import BILLING_MANAGE, PRO_FEATURE_USE
from app.modules.identity.domain import is_valid_external_org_id, role_from_external
from app.modules.llm.domain import LLM_CHECK
from app.modules.projects.domain import PROJECT_CREATE, PROJECT_DELETE, PROJECT_READ

USER = uuid.uuid4()
ORG = uuid.uuid4()
ALL = (PROJECT_READ, PROJECT_CREATE, PROJECT_DELETE, BILLING_MANAGE, PRO_FEATURE_USE, LLM_CHECK)


def personal() -> TenantContext:
    return TenantContext(USER, "user_a", ContextKind.PERSONAL)


def org(role: Role) -> TenantContext:
    return TenantContext(USER, "user_a", ContextKind.ORGANIZATION, ORG, "org_x", role)


def test_personal_context_allows_own_actions() -> None:
    for permission in ALL:
        assert is_allowed(personal(), permission)


def test_permissions_are_denied_unless_granted() -> None:
    """A new permission grants nothing until a context or role is listed explicitly."""
    bare = Permission("thing:do", personal=False)
    assert not is_allowed(personal(), bare)
    assert not is_allowed(org(Role.ADMIN), bare)
    assert not is_allowed(org(Role.MEMBER), bare)


def test_permission_names_are_validated() -> None:
    with pytest.raises(ValueError, match="resource:verb"):
        Permission("Delete Everything", personal=True)


def test_member_cannot_delete_or_manage_billing() -> None:
    ctx = org(Role.MEMBER)
    assert is_allowed(ctx, PROJECT_READ)
    assert is_allowed(ctx, PROJECT_CREATE)
    assert not is_allowed(ctx, PROJECT_DELETE)
    assert not is_allowed(ctx, BILLING_MANAGE)


def test_admin_can_delete_but_never_manages_billing_in_org() -> None:
    ctx = org(Role.ADMIN)
    assert is_allowed(ctx, PROJECT_DELETE)
    assert not is_allowed(ctx, BILLING_MANAGE)


def test_authorize_raises_forbidden() -> None:
    with pytest.raises(ForbiddenError):
        authorize(org(Role.MEMBER), PROJECT_DELETE)


def test_context_invariants() -> None:
    with pytest.raises(ValueError, match="personal"):
        TenantContext(USER, "user_a", ContextKind.PERSONAL, ORG, "org_x", Role.ADMIN)
    with pytest.raises(ValueError, match="organization"):
        TenantContext(USER, "user_a", ContextKind.ORGANIZATION)


@pytest.mark.parametrize(
    ("raw", "role"),
    [("org:admin", Role.ADMIN), ("org:member", Role.MEMBER), ("org:owner", Role.MEMBER), ("", Role.MEMBER)],
)
def test_unknown_roles_map_to_least_privilege(raw: str, role: Role) -> None:
    assert role_from_external(raw) is role


@pytest.mark.parametrize("value", ["org_abc123", "org_A1"])
def test_valid_org_ids(value: str) -> None:
    assert is_valid_external_org_id(value)


@pytest.mark.parametrize("value", ["", "org_", "org_../x", "user_abc", "org_" + "a" * 65, "org_abc'--"])
def test_invalid_org_ids(value: str) -> None:
    assert not is_valid_external_org_id(value)


# -- A route's declared permission must be evaluated (TEN-02) ------------------------------


def declared_app(handler: Any, requirement: Any) -> Any:
    from fastapi import APIRouter
    from fastapi.testclient import TestClient

    from app.kernel.http.app import build_fastapi
    from app.kernel.http.auth import requires
    from tests.conftest import make_settings

    router = APIRouter()
    router.get("/probe", dependencies=[requires(requirement)])(handler)
    app = build_fastapi(settings=make_settings(), title="t", routers=[router], worker_threads=4)
    return TestClient(app, raise_server_exceptions=False)


def test_a_route_that_evaluates_its_declared_permission_is_served() -> None:
    def handler() -> dict[str, str]:
        authorize(personal(), PROJECT_READ)
        return {"data": "ok"}

    assert declared_app(handler, PROJECT_READ).get("/api/v1/probe").status_code == 200


def test_a_route_that_never_evaluates_its_declared_permission_fails_closed() -> None:
    """A forgotten ``authorize`` would serve every member; the route answers 500 instead."""

    def handler() -> dict[str, str]:
        return {"data": "served to every member"}

    response = declared_app(handler, PROJECT_DELETE).get("/api/v1/probe")
    assert response.status_code == 500
    assert response.json()["code"] == "INTERNAL_ERROR"
    assert "served" not in response.text


def test_a_route_that_evaluates_another_permission_fails_closed() -> None:
    def handler() -> dict[str, str]:
        authorize(personal(), PROJECT_READ)
        return {}

    assert declared_app(handler, PROJECT_DELETE).get("/api/v1/probe").status_code == 500


def test_a_refusal_is_not_turned_into_a_500() -> None:
    def handler() -> dict[str, str]:
        raise ForbiddenError()

    assert declared_app(handler, PROJECT_DELETE).get("/api/v1/probe").status_code == 403


def test_a_route_with_an_access_kind_evaluates_nothing() -> None:
    from app.kernel.authorization import Access

    def handler() -> dict[str, str]:
        return {}

    assert declared_app(handler, Access.OWN_ACCOUNT).get("/api/v1/probe").status_code == 200


def test_evaluations_are_recorded_only_inside_a_route() -> None:
    from app.kernel.authorization import recording_evaluations

    assert is_allowed(personal(), PROJECT_READ)  # outside any recording: nothing to keep
    with recording_evaluations() as seen:
        is_allowed(personal(), PROJECT_READ)
        authorize(org(Role.ADMIN), PROJECT_DELETE)
    assert seen == {"project:read", "project:delete"}
