"""Diagnostics configuration."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import NoDecode

from app.kernel.infrastructure.config import AppEnv, CoreSettings


class DiagnosticsSettings(BaseModel):
    # Unset means enabled everywhere except production.
    diagnostics_enabled: bool | None = None
    # Clerk user IDs allowed to use diagnostics. Empty: only when APP_ENV=local,
    # where any signed-in user (the developer) may use them.
    diagnostics_operator_ids: Annotated[list[str], NoDecode] = Field(default_factory=list)
    # Also caps how many of a non-operator's refused attempts are audited each minute.
    diagnostics_runs_per_user_per_minute: int = Field(default=20, ge=1, le=600)
    diagnostics_runs_global_per_day: int = Field(default=1_000, ge=1, le=100_000)

    @field_validator("diagnostics_operator_ids", mode="before")
    @classmethod
    def _split_ids(cls, value: object) -> object:
        if isinstance(value, str):
            return [p.strip() for p in value.split(",") if p.strip()]
        return value


def diagnostics_active(s: DiagnosticsSettings, core: CoreSettings) -> bool:
    """Unset means enabled everywhere except production."""
    if s.diagnostics_enabled is None:
        return core.app_env is not AppEnv.PRODUCTION
    return s.diagnostics_enabled


def open_to_any_signed_in_user(s: DiagnosticsSettings, core: CoreSettings) -> bool:
    """Only for local development with no operators listed."""
    return core.app_env is AppEnv.LOCAL and not s.diagnostics_operator_ids
