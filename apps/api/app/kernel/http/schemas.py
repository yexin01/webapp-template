"""Response models shared by every module. Module-specific models live in each
module's ``routes.py``. Together they form the public API contract, and the
generated OpenAPI document is the frontend's source of truth (DEC-007)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, model_validator


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @model_validator(mode="before")
    @classmethod
    def _no_nul_characters(cls, data: Any) -> Any:
        # PostgreSQL text cannot hold U+0000; refuse it here as invalid input (422), not as a 500 at write time.
        if _contains_nul(data):
            raise ValueError("Text must not contain NUL characters.")
        return data


def _contains_nul(value: Any) -> bool:
    # Iterative, so deeply nested input cannot exhaust the stack (a 500 instead of a 422).
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            if "\x00" in item:
                return True
        elif isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, list | tuple):
            pending.extend(item)
    return False


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
