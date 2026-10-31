"""Identity rules that do not depend on any framework or provider SDK."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar
from uuid import UUID

from app.kernel.events import DomainEvent
from app.kernel.tenancy import Role

_EXTERNAL_ORG_ID = re.compile(r"^org_[A-Za-z0-9]{1,64}$")


@dataclass(frozen=True, slots=True)
class ExternalMembership:
    """A membership as reported by the identity provider."""

    external_org_id: str
    role: Role


@dataclass(frozen=True, slots=True)
class MembershipRecord:
    """A mirrored membership, with when it was last verified against the provider."""

    org_id: UUID
    external_org_id: str
    role: Role
    verified_at: datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class UserDeleted(DomainEvent):
    """The identity provider reports that the user's account was deleted.

    Other modules end what the user left behind (billing ends the subscription); identity knows none of them.
    """

    event_type: ClassVar[str] = "identity.user_deleted"
    user_id: UUID


def role_from_external(value: str) -> Role:
    """Map an identity-provider role to a domain role.

    Only the explicit admin role grants admin rights; every other or unknown
    role is treated as a plain member (least privilege).
    """
    return Role.ADMIN if value == "org:admin" else Role.MEMBER


def is_valid_external_org_id(value: str) -> bool:
    return bool(_EXTERNAL_ORG_ID.fullmatch(value))
