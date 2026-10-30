"""Project use cases."""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID

from app.kernel.authorization import authorize, is_allowed
from app.kernel.errors import DependencyUnavailableError, DomainError, ForbiddenError, NotFoundError
from app.kernel.events import StoredEvent
from app.kernel.pagination import PageCursor, bounded_limit
from app.kernel.ports import Analytics, AuditEntry, Transactions, Tx
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
            # A removal since the context was resolved is a 404, not an RLS error (TEN-05, TEN-07).
            if ctx.kind is ContextKind.ORGANIZATION and _my_locked_role(t, ctx) is None:
                raise NotFoundError()
            project = t.repo.create(ctx, clean)
            t.record(ProjectCreated(project_id=project.id, user_id=ctx.user_id, org_id=ctx.org_id, context=ctx.kind))
        return project

    def delete(self, ctx: TenantContext, project_id: UUID, request_id: str | None) -> None:
        with self._tx.as_tenant(ctx) as t:
            refusal = _delete_refusal(t, ctx)
            if refusal is None:
                if not t.repo.delete(ctx, project_id):
                    raise NotFoundError()
                if ctx.kind is ContextKind.ORGANIZATION:
                    t.audit(_audit(ctx, project_id, "succeeded", request_id))
            else:
                # The block ends normally so the denial's audit entry commits (ADM-01, NT-07).
                t.audit(_audit(ctx, project_id, "denied", request_id))
        if refusal is not None:
            raise refusal

    def capture_created(self, event: StoredEvent) -> None:
        """Outbox handler: product analytics, after commit.

        A redelivery sends the same PostHog event (the outbox event's ID and
        time), which PostHog deduplicates. Raises unless the export is
        confirmed, so the outbox retries it instead of marking it processed.
        """
        self._analytics.capture(
            UUID(str(event.payload["user_id"])),
            "project_created",
            {"context": str(event.payload["context"])},
            event_id=event.id,
            occurred_at=event.occurred_at,
        )
        if not self._analytics.flush():
            raise DependencyUnavailableError("Analytics export could not be confirmed.")


def _delete_refusal(t: Tx[ProjectRepository], ctx: TenantContext) -> DomainError | None:
    """Why the caller may not delete, decided inside the writing transaction."""
    if not is_allowed(ctx, PROJECT_DELETE):
        return ForbiddenError()
    if ctx.kind is not ContextKind.ORGANIZATION:
        return None
    # Re-read under a row lock, so a concurrent downgrade cannot commit first (TEN-07, NT-06).
    role = _my_locked_role(t, ctx)
    if role is None:
        return NotFoundError()  # removed since the context was resolved: a non-member (TEN-05)
    return None if is_allowed(replace(ctx, role=role), PROJECT_DELETE) else ForbiddenError()


def _my_locked_role(t: Tx[ProjectRepository], ctx: TenantContext) -> Role | None:
    """The caller's current role in the context organization, locked until the transaction ends."""
    return None if ctx.org_id is None else t.lock_my_role(ctx.org_id)


def _audit(ctx: TenantContext, project_id: UUID, outcome: str, request_id: str | None) -> AuditEntry:
    return AuditEntry(ctx.user_id, ctx.org_id, PROJECT_DELETE.name, "project", str(project_id), outcome, request_id)
