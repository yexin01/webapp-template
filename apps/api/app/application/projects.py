"""Example tenant-scoped resource demonstrating authorization, RLS, and pagination."""

from __future__ import annotations

from uuid import UUID

from app.domain.authorization import Action, authorize, is_allowed
from app.domain.errors import ForbiddenError, InvalidRequestError, NotFoundError
from app.domain.tenancy import ContextKind, Role, TenantContext
from app.ports import Analytics, AuditEntry, PageCursor, ProjectRecord, UnitOfWork

MAX_PAGE_SIZE = 100
MAX_NAME_LENGTH = 120


class ProjectService:
    def __init__(self, uow: UnitOfWork, analytics: Analytics) -> None:
        self._uow = uow
        self._analytics = analytics

    def list(self, ctx: TenantContext, limit: int, after: PageCursor | None) -> list[ProjectRecord]:
        authorize(ctx, Action.PROJECT_READ)
        bounded = max(1, min(limit, MAX_PAGE_SIZE))
        with self._uow.as_tenant(ctx) as repo:
            return repo.list_projects(ctx, bounded, after)

    def create(self, ctx: TenantContext, name: str) -> ProjectRecord:
        authorize(ctx, Action.PROJECT_CREATE)
        clean = name.strip()
        if not 1 <= len(clean) <= MAX_NAME_LENGTH:
            raise InvalidRequestError(f"Project name must be 1 to {MAX_NAME_LENGTH} characters.")
        with self._uow.as_tenant(ctx) as repo:
            project = repo.create_project(ctx, clean)
        self._analytics.capture(ctx.user_id, "project_created", {"context": ctx.kind.value})
        return project

    def delete(self, ctx: TenantContext, project_id: UUID, request_id: str | None) -> None:
        if not is_allowed(ctx, Action.PROJECT_DELETE):
            self._audit_denied(ctx, project_id, request_id)
            raise ForbiddenError()

        is_org = ctx.kind is ContextKind.ORGANIZATION
        with self._uow.as_tenant(ctx) as repo:
            # Re-check the role inside the writing transaction and lock the
            # membership row, so a concurrent downgrade cannot commit before
            # this privileged write (TEN-07, NT-06).
            if is_org and (ctx.org_id is None or repo.lock_my_membership_role(ctx.org_id) is not Role.ADMIN):
                raise ForbiddenError()
            if not repo.delete_project(ctx, project_id):
                raise NotFoundError()
            if ctx.kind is ContextKind.ORGANIZATION:
                repo.insert_audit(
                    AuditEntry(
                        ctx.user_id,
                        ctx.org_id,
                        Action.PROJECT_DELETE.value,
                        "project",
                        str(project_id),
                        "succeeded",
                        request_id,
                    )
                )

    def _audit_denied(self, ctx: TenantContext, project_id: UUID, request_id: str | None) -> None:
        with self._uow.as_tenant(ctx) as repo:
            repo.insert_audit(
                AuditEntry(
                    ctx.user_id,
                    ctx.org_id,
                    Action.PROJECT_DELETE.value,
                    "project",
                    str(project_id),
                    "denied",
                    request_id,
                )
            )
