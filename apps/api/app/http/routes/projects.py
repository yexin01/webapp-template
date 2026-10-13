"""Example tenant-scoped resource, available in personal and organization contexts."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query, Response

from app.domain.tenancy import TenantContext
from app.http.deps import ContainerDep, OrganizationContext, PersonalContext, current_request_id
from app.http.schemas import Problem, Project, ProjectCreate, ProjectPage, decode_cursor, encode_cursor

router = APIRouter(tags=["projects"])

Limit = Annotated[int, Query(ge=1, le=100)]
Cursor = Annotated[str | None, Query(max_length=200)]
ERRORS: dict[int | str, dict[str, Any]] = {401: {"model": Problem}, 403: {"model": Problem}, 404: {"model": Problem}}


def _page(container: ContainerDep, ctx: TenantContext, limit: int, cursor: str | None) -> ProjectPage:
    records = container.projects.list(ctx, limit, decode_cursor(cursor))
    next_cursor = encode_cursor(records[-1]) if len(records) == limit else None
    return ProjectPage(items=[Project.from_record(r) for r in records], next_cursor=next_cursor)


@router.get("/projects", response_model=ProjectPage, responses=ERRORS)
def list_personal(
    ctx: PersonalContext, container: ContainerDep, limit: Limit = 20, cursor: Cursor = None
) -> ProjectPage:
    return _page(container, ctx, limit, cursor)


@router.post("/projects", response_model=Project, status_code=201, responses=ERRORS)
def create_personal(body: ProjectCreate, ctx: PersonalContext, container: ContainerDep) -> Project:
    return Project.from_record(container.projects.create(ctx, body.name))


@router.delete("/projects/{project_id}", status_code=204, responses=ERRORS)
def delete_personal(project_id: UUID, ctx: PersonalContext, container: ContainerDep) -> Response:
    container.projects.delete(ctx, project_id, current_request_id())
    return Response(status_code=204)


@router.get("/orgs/{org_id}/projects", response_model=ProjectPage, responses=ERRORS)
def list_org(
    ctx: OrganizationContext, container: ContainerDep, limit: Limit = 20, cursor: Cursor = None
) -> ProjectPage:
    return _page(container, ctx, limit, cursor)


@router.post("/orgs/{org_id}/projects", response_model=Project, status_code=201, responses=ERRORS)
def create_org(body: ProjectCreate, ctx: OrganizationContext, container: ContainerDep) -> Project:
    return Project.from_record(container.projects.create(ctx, body.name))


@router.delete("/orgs/{org_id}/projects/{project_id}", status_code=204, responses=ERRORS)
def delete_org(project_id: UUID, ctx: OrganizationContext, container: ContainerDep) -> Response:
    container.projects.delete(ctx, project_id, current_request_id())
    return Response(status_code=204)
