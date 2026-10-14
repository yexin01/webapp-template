"""Deny-by-default authorization (TEN-02).

Authorization answers "may this actor do this in this tenant?". Whether the
actor has paid for a feature is a separate question, answered by the billing
module.

Each module declares its own permissions next to its domain rules. A
permission lists exactly where it is granted: in the personal context, and to
which organization roles. Anything not listed is denied, so a new permission is
never granted implicitly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.kernel.errors import ForbiddenError
from app.kernel.tenancy import ContextKind, Role, TenantContext

_NAME = re.compile(r"^[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$")


@dataclass(frozen=True, slots=True)
class Permission:
    """One action, and the contexts and roles in which it is granted.

    ``name`` is ``<resource>:<verb>``; it is also the action recorded in the
    audit log.
    """

    name: str
    personal: bool
    org_roles: frozenset[Role] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not _NAME.fullmatch(self.name):
            raise ValueError(f"permission name must look like 'resource:verb': {self.name!r}")


def is_allowed(ctx: TenantContext, permission: Permission) -> bool:
    if ctx.kind is ContextKind.PERSONAL:
        return permission.personal
    return ctx.role is not None and ctx.role in permission.org_roles


def authorize(ctx: TenantContext, permission: Permission) -> None:
    if not is_allowed(ctx, permission):
        raise ForbiddenError()


ALL_ROLES: frozenset[Role] = frozenset(Role)
ADMIN_ONLY: frozenset[Role] = frozenset({Role.ADMIN})
