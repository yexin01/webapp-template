"""Public API contract. The generated OpenAPI document is the source of truth
for the frontend's TypeScript types (DEC-007)."""

from __future__ import annotations

import base64
import binascii
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.entitlements import BillingState, Plan, PlanKey
from app.domain.errors import InvalidRequestError
from app.ports import PageCursor, ProjectRecord


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


class Me(BaseModel):
    user_id: UUID


class Project(BaseModel):
    id: UUID
    name: str
    organization_scoped: bool
    created_at: datetime

    @classmethod
    def from_record(cls, record: ProjectRecord) -> Project:
        return cls(
            id=record.id,
            name=record.name,
            organization_scoped=record.org_id is not None,
            created_at=record.created_at,
        )


class ProjectPage(BaseModel):
    items: list[Project]
    next_cursor: str | None


class ProjectCreate(StrictInput):
    name: str = Field(min_length=1, max_length=120)


class BillingStatus(BaseModel):
    plan: Plan
    state: BillingState
    plan_key: PlanKey | None
    current_period_end: datetime | None
    cancels_at: datetime | None
    available_plans: list[PlanKey]


class CheckoutRequest(StrictInput):
    plan: Literal[PlanKey.PRO_MONTHLY, PlanKey.PRO_YEARLY]


class Redirect(BaseModel):
    url: str


class Entitlement(BaseModel):
    plan: Plan
    context: Literal["personal", "organization"]


class ProInsight(BaseModel):
    message: str
    generated_at: datetime


class LLMCheck(BaseModel):
    ok: bool
    provider: str
    model: str
    latency_ms: int
    error_code: str | None


class WebhookAck(BaseModel):
    received: Literal[True]
    status: str


def encode_cursor(record: ProjectRecord) -> str:
    raw = f"{record.created_at.isoformat()}|{record.id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(value: str | None) -> PageCursor | None:
    if not value:
        return None
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode()
        created_at, _, ident = raw.partition("|")
        return PageCursor(created_at=datetime.fromisoformat(created_at), id=UUID(ident))
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise InvalidRequestError("The pagination cursor is invalid.") from exc
