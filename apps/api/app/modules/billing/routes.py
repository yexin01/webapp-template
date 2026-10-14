"""Billing HTTP surface: status and entitlement reads, checkout and the customer
portal, a Pro-gated example, and the Stripe webhook."""

# No ``from __future__ import annotations`` here: FastAPI must evaluate the
# dependency aliases defined inside the router factories at definition time.

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.kernel.http.auth import AuthDeps
from app.kernel.http.schemas import StrictInput, WebhookAck, errors
from app.kernel.ports import Clock
from app.kernel.tenancy import TenantContext
from app.modules.billing.domain import BillingState, Plan, PlanKey
from app.modules.billing.service import BillingService

ERRORS = errors(401, 403, 409, 429, 503)


class BillingStatus(BaseModel):
    plan: Plan
    state: BillingState
    plan_key: PlanKey | None
    current_period_end: datetime | None
    cancels_at: datetime | None
    available_plans: list[Literal[PlanKey.PRO_MONTHLY, PlanKey.PRO_YEARLY]]


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


def build_read_router(billing: BillingService, clock: Clock, auth: AuthDeps, request_transaction: Any) -> APIRouter:
    """Database-only reads: one request-scoped transaction each."""
    router = APIRouter(dependencies=[Depends(request_transaction, scope="function")])
    Personal = Annotated[TenantContext, Depends(auth.personal)]
    Organization = Annotated[TenantContext, Depends(auth.organization)]

    @router.get("/entitlement", response_model=Entitlement, tags=["account"])
    def personal_entitlement(ctx: Personal) -> Entitlement:
        return Entitlement(plan=billing.entitlement(ctx).plan, context="personal")

    @router.get("/orgs/{org_id}/entitlement", response_model=Entitlement, tags=["account"])
    def organization_entitlement(ctx: Organization) -> Entitlement:
        return Entitlement(plan=billing.entitlement(ctx).plan, context="organization")

    @router.get("/billing/status", response_model=BillingStatus, responses=ERRORS, tags=["billing"])
    def billing_status(ctx: Personal) -> BillingStatus:
        summary = billing.summary(ctx)
        return BillingStatus(
            plan=summary.plan,
            state=summary.state,
            plan_key=summary.plan_key,
            current_period_end=summary.current_period_end,
            cancels_at=summary.cancels_at,
            available_plans=[k for k in (PlanKey.PRO_MONTHLY, PlanKey.PRO_YEARLY) if billing.has_price(k)],
        )

    @router.get("/pro/insight", response_model=ProInsight, responses=ERRORS, tags=["billing"])
    def pro_insight(ctx: Personal) -> ProInsight:
        billing.require_pro(ctx)
        return ProInsight(message="This content is only available on the Pro plan.", generated_at=clock.now())

    @router.get("/orgs/{org_id}/pro/insight", response_model=ProInsight, responses=ERRORS, tags=["billing"])
    def org_pro_insight(ctx: Organization) -> ProInsight:
        # The caller's own subscription decides, as in the personal context (DEC-002, ADR-0002).
        billing.require_pro(ctx)
        return ProInsight(message="This content is only available on the Pro plan.", generated_at=clock.now())

    return router


def build_action_router(billing: BillingService, auth: AuthDeps) -> APIRouter:
    """Checkout and portal call Stripe, so they use short explicit transactions."""
    router = APIRouter(tags=["billing"])
    Personal = Annotated[TenantContext, Depends(auth.personal)]

    @router.post("/billing/checkout", response_model=Redirect, responses=ERRORS)
    def start_checkout(
        body: CheckoutRequest,
        ctx: Personal,
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=64)],
    ) -> Redirect:
        return Redirect(url=billing.start_checkout(ctx, PlanKey(body.plan), idempotency_key))

    @router.post("/billing/portal", response_model=Redirect, responses=ERRORS)
    def open_portal(ctx: Personal) -> Redirect:
        return Redirect(url=billing.open_portal(ctx))

    return router


def build_webhook_router(billing: BillingService) -> APIRouter:
    """Authenticated by signature on the raw body, not by session (SEC-13)."""
    router = APIRouter(tags=["webhooks"])

    @router.post("/webhooks/stripe", response_model=WebhookAck, responses=errors(400, 503))
    async def stripe_webhook(request: Request) -> WebhookAck:
        payload = await request.body()  # unmodified bytes, as Stripe signed them
        status = await run_in_threadpool(billing.handle_webhook, payload, request.headers.get("stripe-signature"))
        return WebhookAck(received=True, status=status.value)

    return router
