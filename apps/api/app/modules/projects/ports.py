"""Project persistence port."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.kernel.pagination import PageCursor
from app.kernel.tenancy import TenantContext
from app.modules.projects.domain import Project


class ProjectRepository(Protocol):
    """Tenant-scoped project storage. Every method takes the tenant context, and
    row-level security enforces it again in the database (TEN-03, TEN-04)."""

    def list(self, ctx: TenantContext, limit: int, after: PageCursor | None) -> list[Project]: ...
    def create(self, ctx: TenantContext, name: str) -> Project: ...
    def delete(self, ctx: TenantContext, project_id: UUID) -> bool: ...
