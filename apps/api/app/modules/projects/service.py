"""Project use cases."""

from __future__ import annotations

from uuid import UUID

from app.kernel.authorization import authorize, is_allowed
from app.kernel.errors import ForbiddenError, NotFoundError
from app.kernel.events import StoredEvent
from app.kernel.pagination import PageCursor, bounded_limit
from app.kernel.ports import Analytics, AuditEntry, Transactions
from app.kernel.tenancy import ContextKind, Role, TenantContext
from app.modules.projects.domain import (
    PROJECT_CREATE,
    PROJECT_DELETE,
    PROJECT_READ,
    Project,
    ProjectCreated,
    clean_name,
)
from app.modules.projects.ports import ProjectRepository


class ProjectService:
    def __init__(self, tx: Transactions[ProjectRepository], analytics: Analytics) -> None:
        self._tx = tx
        self._analytics = analytics

    def list(self, ctx: TenantContext, limit: int, after: PageCursor | None) -> list[Project]:
        authorize(ctx, PROJECT_READ)
        with self._tx.as_tenant(ctx) as t:
            return t.repo.list(ctx, bounded_limit(limit), after)

    def create(self, ctx: TenantContext, name: str) -> Project:
        authorize(ctx, PROJECT_CREATE)
        clean = clean_name(name)
        with self._tx.as_tenant(ctx) as t:
            project = t.repo.create(ctx, clean)
            t.record(ProjectCreated(project_id=project.id, user_id=ctx.user_id, org_id=ctx.org_id, context=ctx.kind))
        return project

    def delete(self, ctx: TenantContext, project_id: UUID, request_id: str | None) -> None:
        if not is_allowed(ctx, PROJECT_DELETE):
            with self._tx.as_tenant(ctx) as t:
                t.audit(_audit(ctx, project_id, "denied", request_id))
            raise ForbiddenError()

        with self._tx.as_tenant(ctx) as t:
            # Re-check the role inside the writing transaction and lock the
            # membership row, so a concurrent downgrade cannot commit before
            # this privileged write (TEN-07, NT-06).
            if ctx.kind is ContextKind.ORGANIZATION and (
                ctx.org_id is None or t.lock_my_role(ctx.org_id) is not Role.ADMIN
            ):
                raise ForbiddenError()
            if not t.repo.delete(ctx, project_id):
                raise NotFoundError()
            if ctx.kind is ContextKind.ORGANIZATION:
                t.audit(_audit(ctx, project_id, "succeeded", request_id))

    def capture_created(self, event: StoredEvent) -> None:
        """Outbox handler: product analytics, after commit."""
        self._analytics.capture(
            UUID(str(event.payload["user_id"])), "project_created", {"context": str(event.payload["context"])}
        )


def _audit(ctx: TenantContext, project_id: UUID, outcome: str, request_id: str | None) -> AuditEntry:
    return AuditEntry(ctx.user_id, ctx.org_id, PROJECT_DELETE.name, "project", str(project_id), outcome, request_id)
