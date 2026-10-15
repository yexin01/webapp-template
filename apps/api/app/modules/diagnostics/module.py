"""Wiring for the diagnostics module."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.kernel.diagnostics import IntegrationProbe
from app.kernel.http.auth import AuthDeps
from app.kernel.infrastructure.config import CoreSettings
from app.kernel.wiring import Contribution, Kernel
from app.modules.diagnostics.domain import OperatorPolicy
from app.modules.diagnostics.routes import build_router
from app.modules.diagnostics.service import DiagnosticsService
from app.modules.diagnostics.settings import (
    DiagnosticsSettings,
    diagnostics_active,
    open_to_any_signed_in_user,
)


@dataclass
class DiagnosticsModule:
    service: DiagnosticsService
    contribution: Contribution


def build_diagnostics(
    core: CoreSettings,
    settings: DiagnosticsSettings,
    kernel: Kernel,
    auth: AuthDeps,
    probes: Sequence[IntegrationProbe],
) -> DiagnosticsModule:
    service = DiagnosticsService(
        enabled=diagnostics_active(settings, core),
        operators=OperatorPolicy(
            frozenset(settings.diagnostics_operator_ids),
            allow_any_signed_in_user=open_to_any_signed_in_user(settings, core),
        ),
        probes=probes,
        rate_limiter=kernel.rate_limiter,
        transactions=kernel.store,
        runs_per_user_per_minute=settings.diagnostics_runs_per_user_per_minute,
        runs_global_per_day=settings.diagnostics_runs_global_per_day,
    )
    router = build_router(service, core.app_env.value, auth)
    return DiagnosticsModule(service, Contribution(routers=[router]))
