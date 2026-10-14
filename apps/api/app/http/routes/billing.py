"""Individual billing: status, checkout, customer portal, and a Pro-gated example."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Header

from app.domain.entitlements import PlanKey
from app.http.deps import ContainerDep, OrganizationContext, PersonalContext
from app.http.schemas import BillingStatus, CheckoutRequest, Problem, ProInsight, Redirect

router = APIRouter(tags=["billing"])
ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": Problem},
    403: {"model": Problem},
    409: {"model": Problem},
    429: {"model": Problem},
    503: {"model": Problem},
}


@router.get("/billing/status", response_model=BillingStatus, responses=ERRORS)
def billing_status(ctx: PersonalContext, container: ContainerDep) -> BillingStatus:
    summary = container.billing.summary(ctx)
    return BillingStatus(
        plan=summary.plan,
        state=summary.state,
        plan_key=summary.plan_key,
        current_period_end=summary.current_period_end,
        cancels_at=summary.cancels_at,
        available_plans=[k for k in (PlanKey.PRO_MONTHLY, PlanKey.PRO_YEARLY) if container.billing.has_price(k)],
    )


@router.post("/billing/checkout", response_model=Redirect, responses=ERRORS)
def start_checkout(
    body: CheckoutRequest,
    ctx: PersonalContext,
    container: ContainerDep,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=64)],
) -> Redirect:
    return Redirect(url=container.billing.start_checkout(ctx, PlanKey(body.plan), idempotency_key))


@router.post("/billing/portal", response_model=Redirect, responses=ERRORS)
def open_portal(ctx: PersonalContext, container: ContainerDep) -> Redirect:
    return Redirect(url=container.billing.open_portal(ctx))


@router.get("/pro/insight", response_model=ProInsight, responses=ERRORS)
def pro_insight(ctx: PersonalContext, container: ContainerDep) -> ProInsight:
    container.billing.require_pro(ctx)
    return ProInsight(message="This content is only available on the Pro plan.", generated_at=container.clock.now())


@router.get("/orgs/{org_id}/pro/insight", response_model=ProInsight, responses=ERRORS)
def org_pro_insight(ctx: OrganizationContext, container: ContainerDep) -> ProInsight:
    # The caller's own subscription decides, as in the personal context (DEC-002, ADR-0002).
    container.billing.require_pro(ctx)
    return ProInsight(message="This content is only available on the Pro plan.", generated_at=container.clock.now())
