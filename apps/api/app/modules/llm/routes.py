"""LLM connectivity check: for operators, rate-limited, and token-free."""

# No ``from __future__ import annotations`` here: FastAPI must evaluate the
# dependency aliases defined inside the router factories at definition time.

from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.kernel.errors import NotFoundError
from app.kernel.http.auth import AuthDeps, requires
from app.kernel.http.schemas import errors
from app.kernel.tenancy import TenantContext
from app.modules.llm.domain import LLM_CHECK
from app.modules.llm.service import LLMService


class LLMCheck(BaseModel):
    ok: bool
    provider: str
    model: str
    latency_ms: int
    error_code: str | None


def build_router(llm: LLMService, auth: AuthDeps, may_use_operator_tools: Callable[[TenantContext], bool]) -> APIRouter:
    # Calls an external provider, so no request-scoped transaction.
    router = APIRouter(tags=["llm"])
    Personal = Annotated[TenantContext, Depends(auth.personal)]

    @router.post(
        "/llm/check",
        response_model=LLMCheck,
        responses=errors(401, 403, 404, 429, 503),
        dependencies=[requires(LLM_CHECK)],
    )
    def llm_check(ctx: Personal) -> LLMCheck:
        # Every account shares the daily budget, so only operators may spend it. Anyone else gets the 404 the
        # diagnostics routes give, before a limit is touched.
        if not may_use_operator_tools(ctx):
            raise NotFoundError()
        result = llm.check(ctx)
        return LLMCheck(
            ok=result.ok,
            provider=result.provider,
            model=result.model,
            latency_ms=result.latency_ms,
            error_code=result.error_code,
        )

    return router
