"""LLM connectivity check: authenticated, rate-limited, and token-free."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.http.deps import ContainerDep, PersonalContext
from app.http.schemas import LLMCheck, Problem

router = APIRouter(tags=["llm"])
ERRORS: dict[int | str, dict[str, Any]] = {401: {"model": Problem}, 429: {"model": Problem}}


@router.post("/llm/check", response_model=LLMCheck, responses=ERRORS)
def llm_check(ctx: PersonalContext, container: ContainerDep) -> LLMCheck:
    result = container.llm.check(ctx)
    return LLMCheck(
        ok=result.ok,
        provider=result.provider,
        model=result.model,
        latency_ms=result.latency_ms,
        error_code=result.error_code,
    )
