"""Tenant context: who is acting, and in which tenant (personal or organization)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID


class Role(StrEnum):
    ADMIN = "admin"
    MEMBER = "member"


class ContextKind(StrEnum):
    PERSONAL = "personal"
    ORGANIZATION = "organization"


@dataclass(frozen=True, slots=True)
class VerifiedIdentity:
    """Identity established by server-side token verification.

    ``active_org_id`` is the organization the signed session names as active.
    It may prompt a membership re-check, but never grants access by itself.
    """

    external_user_id: str
    session_id: str | None = None
    active_org_id: str | None = None


@dataclass(frozen=True, slots=True)
class TenantContext:
    """Server-authoritative context for one request.

    Never constructed from client-supplied roles or identifiers alone: the
    organization and role are verified against the membership store first.
    """

    user_id: UUID
    external_user_id: str
    kind: ContextKind
    org_id: UUID | None = None
    external_org_id: str | None = None
    role: Role | None = None

    def __post_init__(self) -> None:
        if self.kind is ContextKind.PERSONAL:
            if self.org_id is not None or self.role is not None:
                raise ValueError("personal context cannot carry an organization or role")
        elif self.org_id is None or self.role is None or self.external_org_id is None:
            raise ValueError("organization context requires organization and role")
