"""Project rules: permissions, validation, and events. No frameworks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar
from uuid import UUID

from app.kernel.authorization import ADMIN_ONLY, ALL_ROLES, Permission
from app.kernel.errors import InvalidRequestError
from app.kernel.events import DomainEvent
from app.kernel.tenancy import ContextKind

MAX_NAME_LENGTH = 120

PROJECT_READ = Permission("project:read", personal=True, org_roles=ALL_ROLES)
PROJECT_CREATE = Permission("project:create", personal=True, org_roles=ALL_ROLES)
PROJECT_DELETE = Permission("project:delete", personal=True, org_roles=ADMIN_ONLY)


@dataclass(frozen=True, slots=True)
class Project:
    id: UUID
    name: str
    org_id: UUID | None
    created_by: UUID
    created_at: datetime


def clean_name(name: str) -> str:
    clean = name.strip()
    if not 1 <= len(clean) <= MAX_NAME_LENGTH:
        raise InvalidRequestError(f"Project name must be 1 to {MAX_NAME_LENGTH} characters.")
    return clean


@dataclass(frozen=True, slots=True, kw_only=True)
class ProjectCreated(DomainEvent):
    event_type: ClassVar[str] = "projects.created"
    project_id: UUID
    user_id: UUID
    org_id: UUID | None
    context: ContextKind
