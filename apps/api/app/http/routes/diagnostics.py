"""Integration diagnostics for operators and template adopters.

Disabled in production unless DIAGNOSTICS_ENABLED=true; when disabled, every
route here returns 404. Responses never contain secrets.
"""

from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter

from app.application.diagnostics import IntegrationKey
from app.http.deps import ContainerDep, PersonalContext
from app.http.schemas import IntegrationCheck, IntegrationsReport, IntegrationStatus, Problem

router = APIRouter(tags=["diagnostics"])
ERRORS: dict[int | str, dict[str, Any]] = {401: {"model": Problem}, 404: {"model": Problem}, 429: {"model": Problem}}


@router.get("/diagnostics/integrations", response_model=IntegrationsReport, responses=ERRORS)
def list_integrations(ctx: PersonalContext, container: ContainerDep) -> IntegrationsReport:
    infos = container.diagnostics.integrations(ctx)
    return IntegrationsReport(
        environment=cast(Any, container.settings.app_env.value),
        integrations=[IntegrationStatus.from_info(info) for info in infos],
    )


@router.post("/diagnostics/integrations/{key}/check", response_model=IntegrationCheck, responses=ERRORS)
def run_check(key: IntegrationKey, ctx: PersonalContext, container: ContainerDep) -> IntegrationCheck:
    return IntegrationCheck.from_result(container.diagnostics.run(ctx, key))
