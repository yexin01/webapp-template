"""Inbound webhooks. Authenticated by signature on the raw body, not by session (SEC-13)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

from app.http.deps import get_container
from app.http.schemas import Problem, WebhookAck

router = APIRouter(tags=["webhooks"])
ERRORS: dict[int | str, dict[str, Any]] = {400: {"model": Problem}, 503: {"model": Problem}}


@router.post("/webhooks/stripe", response_model=WebhookAck, responses=ERRORS)
async def stripe_webhook(request: Request) -> WebhookAck:
    container = get_container(request)
    payload = await request.body()  # unmodified bytes, as Stripe signed them
    status = await run_in_threadpool(container.billing.handle_webhook, payload, request.headers.get("stripe-signature"))
    return WebhookAck(received=True, status=status.value)


@router.post("/webhooks/clerk", response_model=WebhookAck, responses=ERRORS)
async def clerk_webhook(request: Request) -> WebhookAck:
    container = get_container(request)
    payload = await request.body()
    status = await run_in_threadpool(container.identity_sync.handle_webhook, payload, dict(request.headers))
    return WebhookAck(received=True, status=status.value)
