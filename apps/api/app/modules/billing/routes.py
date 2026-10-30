"""Billing HTTP surface: status and entitlement reads, checkout and the customer
portal, a Pro-gated example, and the Stripe webhook."""

# No ``from __future__ import annotations`` here: FastAPI must evaluate the
# dependency aliases defined inside the router factories at definition time.

from datetime import datetime
from typing import Annotated, Any, Literal

import anyio
from anyio.lowlevel import RunVar
from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.kernel.errors import DependencyUnavailableError
from app.kernel.http.auth import AuthDeps
from app.kernel.http.schemas import StrictInput, WebhookAck, errors
from app.kernel.ports import Clock
from app.kernel.tenancy import TenantContext
from app.modules.billing.domain import BillingState, BillingSummary, CheckoutBlock, Plan, PlanKey
from app.modules.billing.service import BillingService

ERRORS = errors(401, 403, 409, 429, 503)


class BillingStatus(BaseModel):
    plan: Plan
    state: BillingState
    plan_key: PlanKey | None
    current_period_end: datetime | None
    cancels_at: datetime | None
    available_plans: list[Literal[PlanKey.PRO_MONTHLY, PlanKey.PRO_YEARLY]]
    # Why a new checkout is refused (DEC-013), or null when starting one is allowed. The UI offers
    # Upgrade only when this is null, and plan management otherwise.
    checkout_blocked: CheckoutBlock | None


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


def _status(billing: BillingService, summary: BillingSummary) -> BillingStatus:
    return BillingStatus(
        plan=summary.plan,
        state=summary.state,
        plan_key=summary.plan_key,
        current_period_end=summary.current_period_end,
        cancels_at=summary.cancels_at,
        checkout_blocked=summary.checkout_block,
        available_plans=[k for k in (PlanKey.PRO_MONTHLY, PlanKey.PRO_YEARLY) if billing.has_price(k)],
    )


def build_read_router(billing: BillingService, clock: Clock, auth: AuthDeps, request_transaction: Any) -> APIRouter:
    """Database-only reads: one request-scoped transaction each."""
    # The token is verified before the request takes an admission place (KDB-2).
    router = APIRouter(dependencies=[Depends(auth.authenticate), Depends(request_transaction, scope="function")])
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
        return _status(billing, billing.summary(ctx))

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

    @router.post("/billing/sync", response_model=BillingStatus, responses=ERRORS)
    def sync_billing(ctx: Personal) -> BillingStatus:
        """Re-read the caller's own subscriptions from Stripe, then return the status. Takes no input (BILL-02)."""
        return _status(billing, billing.sync_from_stripe(ctx))

    return router


def build_webhook_router(billing: BillingService) -> APIRouter:
    """Authenticated by signature on the raw body, not by session (SEC-13)."""
    router = APIRouter(tags=["webhooks"])
    # One admission gate per event loop: a burst of deliveries waits here, holding no
    # threadpool thread, for one of the synchronization slots (BILL-10).
    gates: RunVar[anyio.Semaphore] = RunVar(f"billing_webhook_gate_{id(billing)}")

    def gate() -> anyio.Semaphore:
        try:
            return gates.get()
        except LookupError:
            created = anyio.Semaphore(billing.sync_slots)
            gates.set(created)
            return created

    @router.post("/webhooks/stripe", response_model=WebhookAck, responses=errors(400, 503))
    async def stripe_webhook(request: Request) -> WebhookAck:
        payload = await request.body()  # unmodified bytes, as Stripe signed them
        # Verified first, so forged requests get 400 at once and never queue for a slot.
        event = await run_in_threadpool(billing.verify_webhook, payload, request.headers.get("stripe-signature"))
        if not billing.requires_sync(event):
            status = await run_in_threadpool(billing.process_webhook, event)
            return WebhookAck(received=True, status=status.value)
        admission = gate()
        admitted = False
        with anyio.move_on_after(billing.sync_wait_seconds):
            await admission.acquire()
            admitted = True
        if not admitted:
            raise DependencyUnavailableError("Billing synchronization is busy. Retry shortly.")
        try:
            status = await run_in_threadpool(billing.process_webhook, event)
        finally:
            admission.release()
        return WebhookAck(received=True, status=status.value)

    return router
