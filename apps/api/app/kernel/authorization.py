"""Deny-by-default authorization (TEN-02).

Authorization answers "may this actor do this in this tenant?". Whether the
actor has paid for a feature is a separate question, answered by the billing
module.

Each module declares its own permissions next to its domain rules. A
permission lists exactly where it is granted: in the personal context, and to
which organization roles. Anything not listed is denied, so a new permission is
never granted implicitly.

Every route declares what authorizes it (``app.kernel.http.auth.requires``): a
``Permission``, or one of the ``Access`` kinds for routes no permission governs.
A declared ``Permission`` must be evaluated while the request runs, so a route
that forgets its ``authorize`` fails closed instead of serving every member.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import StrEnum

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


class Access(StrEnum):
    """Who may call a route that no ``Permission`` governs."""

    OWN_ACCOUNT = "own account"  # any signed-in caller, on their own data in the personal context
    ANY_MEMBER = "any member"  # any member of the organization in the path, whatever the role
    OPERATOR = "operator"  # an operator on the allowlist, which the use case checks


_evaluated: ContextVar[set[str] | None] = ContextVar("evaluated_permissions", default=None)


@contextmanager
def recording_evaluations() -> Iterator[set[str]]:
    """The names of the permissions ``is_allowed`` evaluates, in the context entered, as the block runs."""
    seen: set[str] = set()
    token = _evaluated.set(seen)
    try:
        yield seen
    finally:
        _evaluated.reset(token)


def is_allowed(ctx: TenantContext, permission: Permission) -> bool:
    seen = _evaluated.get()
    if seen is not None:
        seen.add(permission.name)
    if ctx.kind is ContextKind.PERSONAL:
        return permission.personal
    return ctx.role is not None and ctx.role in permission.org_roles


def authorize(ctx: TenantContext, permission: Permission) -> None:
    if not is_allowed(ctx, permission):
        raise ForbiddenError()


ALL_ROLES: frozenset[Role] = frozenset(Role)
ADMIN_ONLY: frozenset[Role] = frozenset({Role.ADMIN})
