"""Project endpoints, available in personal and organization contexts."""

# No ``from __future__ import annotations`` here: FastAPI must evaluate the
# dependency aliases defined inside the router factories at definition time.

from datetime import datetime
from typing import Annotated, Any, Self
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field

from app.kernel.http.auth import AuthDeps
from app.kernel.http.schemas import StrictInput, errors
from app.kernel.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, decode_cursor, encode_cursor
from app.kernel.request_context import request_id_var
from app.kernel.tenancy import TenantContext
from app.modules.projects.domain import MAX_NAME_LENGTH
from app.modules.projects.domain import Project as ProjectRecord
from app.modules.projects.service import ProjectService

Limit = Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)]
Cursor = Annotated[str | None, Query(max_length=200)]
ERRORS = errors(401, 403, 404)


class Project(BaseModel):
    id: UUID
    name: str
    organization_scoped: bool
    created_at: datetime

    @classmethod
    def from_record(cls, record: ProjectRecord) -> Self:
        return cls(
            id=record.id,
            name=record.name,
            organization_scoped=record.org_id is not None,
            created_at=record.created_at,
        )


class ProjectPage(BaseModel):
    items: list[Project]
    next_cursor: str | None


class ProjectCreate(StrictInput):
    name: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)


def build_router(projects: ProjectService, auth: AuthDeps, request_transaction: Any) -> APIRouter:
    # Database-only: each request is one request-scoped transaction.
    router = APIRouter(tags=["projects"], dependencies=[Depends(request_transaction, scope="function")])
    Personal = Annotated[TenantContext, Depends(auth.personal)]
    Organization = Annotated[TenantContext, Depends(auth.organization)]

    def page(ctx: TenantContext, limit: int, cursor: str | None) -> ProjectPage:
        records = projects.list(ctx, limit, decode_cursor(cursor))
        last = records[-1] if len(records) == limit else None
        return ProjectPage(
            items=[Project.from_record(r) for r in records],
            next_cursor=encode_cursor(last.created_at, last.id) if last else None,
        )

    @router.get("/projects", response_model=ProjectPage, responses=ERRORS)
    def list_personal(ctx: Personal, limit: Limit = DEFAULT_PAGE_SIZE, cursor: Cursor = None) -> ProjectPage:
        return page(ctx, limit, cursor)

    @router.post("/projects", response_model=Project, status_code=201, responses=ERRORS)
    def create_personal(body: ProjectCreate, ctx: Personal) -> Project:
        return Project.from_record(projects.create(ctx, body.name))

    @router.delete("/projects/{project_id}", status_code=204, responses=ERRORS)
    def delete_personal(project_id: UUID, ctx: Personal) -> Response:
        projects.delete(ctx, project_id, request_id_var.get())
        return Response(status_code=204)

    @router.get("/orgs/{org_id}/projects", response_model=ProjectPage, responses=ERRORS)
    def list_org(ctx: Organization, limit: Limit = DEFAULT_PAGE_SIZE, cursor: Cursor = None) -> ProjectPage:
        return page(ctx, limit, cursor)

    @router.post("/orgs/{org_id}/projects", response_model=Project, status_code=201, responses=ERRORS)
    def create_org(body: ProjectCreate, ctx: Organization) -> Project:
        return Project.from_record(projects.create(ctx, body.name))

    @router.delete("/orgs/{org_id}/projects/{project_id}", status_code=204, responses=ERRORS)
    def delete_org(project_id: UUID, ctx: Organization) -> Response:
        projects.delete(ctx, project_id, request_id_var.get())
        return Response(status_code=204)

    return router
