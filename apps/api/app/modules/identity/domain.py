"""Identity rules that do not depend on any framework or provider SDK."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

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


def role_from_external(value: str) -> Role:
    """Map an identity-provider role to a domain role.

    Only the explicit admin role grants admin rights; every other or unknown
    role is treated as a plain member (least privilege).
    """
    return Role.ADMIN if value == "org:admin" else Role.MEMBER


def is_valid_external_org_id(value: str) -> bool:
    return bool(_EXTERNAL_ORG_ID.fullmatch(value))
