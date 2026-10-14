"""Response models shared by every module. Module-specific models live in each
module's ``routes.py``. Together they form the public API contract, and the
generated OpenAPI document is the frontend's source of truth (DEC-007)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Problem(BaseModel):
    type: str
    title: str
    status: int
    code: str
    detail: str
    request_id: str | None


class Health(BaseModel):
    status: Literal["ok"]


class Readiness(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: dict[str, Literal["ok", "unavailable"]]


class WebhookAck(BaseModel):
    received: Literal[True]
    status: str


def errors(*statuses: int) -> dict[int | str, dict[str, object]]:
    """OpenAPI ``responses`` entries for problem-details errors."""
    return {status: {"model": Problem} for status in statuses}
