"""Liveness and readiness (API-06). Public; reveal no configuration."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.domain.errors import DependencyUnavailableError
from app.http.deps import ContainerDep
from app.http.schemas import Health, Readiness

router = APIRouter(tags=["health"])


@router.get("/health", response_model=Health)
def health() -> Health:
    return Health(status="ok")


@router.get("/ready", response_model=Readiness, responses={503: {"model": Readiness}})
def ready(container: ContainerDep) -> JSONResponse:
    try:
        with container.uow.as_system() as repo:
            repo.ping()
        body = Readiness(status="ready", checks={"database": "ok"})
        return JSONResponse(body.model_dump(), status_code=200)
    except DependencyUnavailableError:
        body = Readiness(status="not_ready", checks={"database": "unavailable"})
        return JSONResponse(body.model_dump(), status_code=503)
