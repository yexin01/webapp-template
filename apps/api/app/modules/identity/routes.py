"""Identity HTTP surface: the ``AuthDeps`` every module builds on, ``/me``, and
the Clerk webhook."""

# No ``from __future__ import annotations`` here: FastAPI must evaluate the
# dependency aliases defined inside the router factories at definition time.

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Request
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.kernel.errors import AuthenticationError
from app.kernel.http.app import bearer_token
from app.kernel.http.auth import AuthDeps
from app.kernel.http.schemas import WebhookAck, errors
from app.kernel.tenancy import TenantContext, VerifiedIdentity
from app.modules.identity.ports import IdentityVerifier
from app.modules.identity.service import ContextResolver, IdentitySyncService


class Me(BaseModel):
    user_id: UUID


def build_auth(verifier: IdentityVerifier, resolver: ContextResolver) -> AuthDeps:
    def authenticate(request: Request) -> VerifiedIdentity:
        """Verify the bearer token. Cookies are deliberately not accepted (API-04)."""
        token = bearer_token(request.headers.get("authorization", ""))
        if token is None:
            raise AuthenticationError()
        return verifier.verify(token)

    Identity = Annotated[VerifiedIdentity, Depends(authenticate)]

    def personal(identity: Identity) -> TenantContext:
        return resolver.personal(identity)

    def organization(
        identity: Identity,
        org_id: Annotated[str, Path(min_length=1, max_length=80, description="Clerk organization ID")],
    ) -> TenantContext:
        return resolver.organization(identity, org_id)

    return AuthDeps(authenticate=authenticate, personal=personal, organization=organization)


def build_account_router(auth: AuthDeps, request_transaction: Any) -> APIRouter:
    router = APIRouter(tags=["account"], dependencies=[Depends(request_transaction, scope="function")])
    Personal = Annotated[TenantContext, Depends(auth.personal)]

    @router.get("/me", response_model=Me)
    def me(ctx: Personal) -> Me:
        return Me(user_id=ctx.user_id)

    return router


def build_webhook_router(sync: IdentitySyncService) -> APIRouter:
    """Authenticated by signature on the raw body, not by session (SEC-13)."""
    router = APIRouter(tags=["webhooks"])

    @router.post("/webhooks/clerk", response_model=WebhookAck, responses=errors(400, 503))
    async def clerk_webhook(request: Request) -> WebhookAck:
        payload = await request.body()
        status = await run_in_threadpool(sync.handle_webhook, payload, dict(request.headers))
        return WebhookAck(received=True, status=status.value)

    return router
