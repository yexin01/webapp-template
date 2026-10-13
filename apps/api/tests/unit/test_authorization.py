"""Deny-by-default authorization policy (TEN-02, ADM-01)."""

from __future__ import annotations

import uuid

import pytest

from app.domain.authorization import Action, authorize, is_allowed
from app.domain.errors import ForbiddenError
from app.domain.tenancy import ContextKind, Role, TenantContext, is_valid_external_org_id, role_from_external

USER = uuid.uuid4()
ORG = uuid.uuid4()


def personal() -> TenantContext:
    return TenantContext(USER, "user_a", ContextKind.PERSONAL)


def org(role: Role) -> TenantContext:
    return TenantContext(USER, "user_a", ContextKind.ORGANIZATION, ORG, "org_x", role)


def test_personal_context_allows_own_actions() -> None:
    for action in Action:
        assert is_allowed(personal(), action)


def test_member_cannot_delete_or_manage_billing() -> None:
    ctx = org(Role.MEMBER)
    assert is_allowed(ctx, Action.PROJECT_READ)
    assert is_allowed(ctx, Action.PROJECT_CREATE)
    assert not is_allowed(ctx, Action.PROJECT_DELETE)
    assert not is_allowed(ctx, Action.BILLING_MANAGE)


def test_admin_can_delete_but_never_manages_billing_in_org() -> None:
    ctx = org(Role.ADMIN)
    assert is_allowed(ctx, Action.PROJECT_DELETE)
    assert not is_allowed(ctx, Action.BILLING_MANAGE)


def test_authorize_raises_forbidden() -> None:
    with pytest.raises(ForbiddenError):
        authorize(org(Role.MEMBER), Action.PROJECT_DELETE)


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
