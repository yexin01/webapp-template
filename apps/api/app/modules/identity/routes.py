"""Identity HTTP surface: the ``AuthDeps`` every module builds on, ``/me``, and
the Clerk webhook."""

# No ``from __future__ import annotations`` here: FastAPI must evaluate the
# dependency aliases defined inside the router factories at definition time.

import logging
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Request
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.kernel.authorization import Access
from app.kernel.errors import AuthenticationError, ForbiddenError
from app.kernel.http.app import bearer_token
from app.kernel.http.auth import AuthDeps, requires
from app.kernel.http.schemas import WebhookAck, errors
from app.kernel.request_context import bind_tenant
from app.kernel.tenancy import TenantContext, VerifiedIdentity
from app.modules.identity.ports import IdentityVerifier
from app.modules.identity.service import ContextResolver, IdentitySyncService

logger = logging.getLogger(__name__)


class Me(BaseModel):
    user_id: UUID


def _bound(ctx: TenantContext) -> TenantContext:
    """Puts the internal user and organization IDs, not the identity provider's, on this request's logs (OBS-04)."""
    bind_tenant(ctx.user_id, ctx.org_id)
    return ctx


def build_auth(
    verifier: IdentityVerifier, resolver: ContextResolver, *, allow_actor_sessions: bool = False
) -> AuthDeps:
    def authenticate(request: Request) -> VerifiedIdentity:
        """Verify the bearer token. Cookies are deliberately not accepted (API-04).

        A session someone else started (an impersonation) has a valid token, so refusing it is a
        decision about the caller, not about the token: 403, not 401 (the web would send the user
        to sign in again, into a session that is still active).
        """
        token = bearer_token(request.headers.get("authorization", ""))
        if token is None:
            raise AuthenticationError()
        identity = verifier.verify(token)
        if identity.actor_external_id is not None:
            if not allow_actor_sessions:
                logger.warning("actor_session_refused")
                raise ForbiddenError("Sessions started by someone else are not enabled for this application.")
            # The audit log names the actor too; this line ties the request ID to it (ADM-01).
            logger.info("actor_session", extra={"actor_external_id": identity.actor_external_id})
        return identity

    Identity = Annotated[VerifiedIdentity, Depends(authenticate)]

    def personal(identity: Identity) -> TenantContext:
        return _bound(resolver.personal(identity))

    def organization(
        identity: Identity,
        org_id: Annotated[str, Path(min_length=1, max_length=80, description="Clerk organization ID")],
    ) -> TenantContext:
        return _bound(resolver.organization(identity, org_id))

    return AuthDeps(authenticate=authenticate, personal=personal, organization=organization)


def build_account_router(auth: AuthDeps, request_transaction: Any) -> APIRouter:
    # Authenticate first: a bad token is rejected before it takes an admission place.
    router = APIRouter(
        tags=["account"],
        dependencies=[Depends(auth.authenticate), Depends(request_transaction, scope="function")],
        responses=errors(401, 403, 503),
    )
    Personal = Annotated[TenantContext, Depends(auth.personal)]

    @router.get("/me", response_model=Me, dependencies=[requires(Access.OWN_ACCOUNT)])
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
