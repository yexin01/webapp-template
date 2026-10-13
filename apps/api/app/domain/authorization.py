"""Deny-by-default authorization policy (TEN-02).

Authorization answers "may this actor do this in this tenant?". Whether the
actor has paid for a feature is a separate question answered by
``entitlements.decide``.
"""

from __future__ import annotations

from enum import StrEnum

from app.domain.errors import ForbiddenError
from app.domain.tenancy import ContextKind, Role, TenantContext


class Action(StrEnum):
    PROJECT_READ = "project:read"
    PROJECT_CREATE = "project:create"
    PROJECT_DELETE = "project:delete"
    BILLING_MANAGE = "billing:manage"
    PRO_FEATURE_USE = "pro_feature:use"
    LLM_CHECK = "llm:check"


_PERSONAL_ALLOWED: frozenset[Action] = frozenset(Action)

# Billing is owned by individuals, so it is never managed from an
# organization context.
_ORGANIZATION_ALLOWED: dict[Role, frozenset[Action]] = {
    Role.MEMBER: frozenset({Action.PROJECT_READ, Action.PROJECT_CREATE, Action.PRO_FEATURE_USE, Action.LLM_CHECK}),
    Role.ADMIN: frozenset(
        {
            Action.PROJECT_READ,
            Action.PROJECT_CREATE,
            Action.PROJECT_DELETE,
            Action.PRO_FEATURE_USE,
            Action.LLM_CHECK,
        }
    ),
}

ADMIN_ACTIONS: frozenset[Action] = _ORGANIZATION_ALLOWED[Role.ADMIN] - _ORGANIZATION_ALLOWED[Role.MEMBER]


def is_allowed(ctx: TenantContext, action: Action) -> bool:
    if ctx.kind is ContextKind.PERSONAL:
        return action in _PERSONAL_ALLOWED
    if ctx.role is None:
        return False
    return action in _ORGANIZATION_ALLOWED.get(ctx.role, frozenset())


def authorize(ctx: TenantContext, action: Action) -> None:
    if not is_allowed(ctx, action):
        raise ForbiddenError()
