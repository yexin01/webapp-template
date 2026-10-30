"""Integration diagnostics for operators and template adopters.

Disabled in production unless DIAGNOSTICS_ENABLED=true; when disabled, every
route here returns 404. Responses never contain secrets.
"""

# No ``from __future__ import annotations`` here: FastAPI must evaluate the
# dependency aliases defined inside the router factories at definition time.

from typing import Annotated, Literal, Self

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.kernel.authorization import Access
from app.kernel.diagnostics import CheckResult, IntegrationInfo, IntegrationKey, IntegrationMode, Outcome
from app.kernel.http.auth import AuthDeps, requires
from app.kernel.http.schemas import errors
from app.kernel.request_context import request_id_var
from app.kernel.tenancy import TenantContext
from app.modules.diagnostics.service import DiagnosticsService

Environment = Literal["local", "test", "preview", "production"]


class DiagnosticsAccess(BaseModel):
    available: bool = Field(
        description="Whether this caller may use the diagnostics routes: false when diagnostics are off for them"
    )


class IntegrationStatus(BaseModel):
    key: IntegrationKey
    name: str
    mode: IntegrationMode
    details: list[str]

    @classmethod
    def from_info(cls, info: IntegrationInfo) -> Self:
        return cls(key=info.key, name=info.name, mode=info.mode, details=list(info.details))


class IntegrationsReport(BaseModel):
    environment: Environment
    integrations: list[IntegrationStatus]


class IntegrationCheck(BaseModel):
    key: IntegrationKey
    mode: IntegrationMode
    outcome: Outcome
    summary: str
    latency_ms: int
    error_code: str | None
    reference: str | None = Field(description="Provider reference for this run, such as a Sentry event ID")

    @classmethod
    def from_result(cls, result: CheckResult) -> Self:
        return cls(
            key=result.key,
            mode=result.mode,
            outcome=result.outcome,
            summary=result.summary,
            latency_ms=result.latency_ms,
            error_code=result.error_code,
            reference=result.reference,
        )


def build_router(diagnostics: DiagnosticsService, environment: Environment, auth: AuthDeps) -> APIRouter:
    # Checks call external services, so no request-scoped transaction.
    router = APIRouter(tags=["diagnostics"])
    Personal = Annotated[TenantContext, Depends(auth.personal)]
    responses = errors(401, 403, 404, 429, 503)

    @router.get(
        "/diagnostics/access",
        response_model=DiagnosticsAccess,
        responses=errors(401),
        dependencies=[requires(Access.OWN_ACCOUNT)],
    )
    def diagnostics_access(ctx: Personal) -> DiagnosticsAccess:
        """Whether the caller may use the diagnostics routes. Any signed-in caller gets an answer, never a 404;
        the routes themselves decide again on every request."""
        return DiagnosticsAccess(available=diagnostics.may_use(ctx))

    @router.get(
        "/diagnostics/integrations",
        response_model=IntegrationsReport,
        responses=responses,
        dependencies=[requires(Access.OPERATOR)],
    )
    def list_integrations(ctx: Personal) -> IntegrationsReport:
        infos = diagnostics.integrations(ctx)
        return IntegrationsReport(
            environment=environment, integrations=[IntegrationStatus.from_info(info) for info in infos]
        )

    @router.post(
        "/diagnostics/integrations/{key}/check",
        response_model=IntegrationCheck,
        responses=responses,
        dependencies=[requires(Access.OPERATOR)],
    )
    def run_check(key: IntegrationKey, ctx: Personal) -> IntegrationCheck:
        return IntegrationCheck.from_result(diagnostics.run(ctx, key, request_id_var.get()))

    return router
