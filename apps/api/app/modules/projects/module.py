"""Wiring for the projects module."""

from __future__ import annotations

from dataclasses import dataclass

from app.kernel.http.auth import AuthDeps
from app.kernel.wiring import Contribution, Kernel
from app.modules.projects.adapters import PgProjectRepository
from app.modules.projects.domain import ProjectCreated
from app.modules.projects.routes import build_router
from app.modules.projects.service import ProjectService


@dataclass
class ProjectsModule:
    service: ProjectService
    contribution: Contribution


def build_projects(kernel: Kernel, auth: AuthDeps) -> ProjectsModule:
    service = ProjectService(kernel.transactions(PgProjectRepository), kernel.analytics)
    kernel.handlers.subscribe(ProjectCreated, "projects.analytics", service.capture_created)
    contribution = Contribution(
        routers=[build_router(service, auth, kernel.request_transaction, kernel.limit_writes(auth))]
    )
    return ProjectsModule(service, contribution)
