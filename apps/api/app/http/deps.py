"""Request dependencies: authentication and tenant-context resolution.

Every protected route depends on ``authenticate`` (directly or through a
context dependency). A route-manifest test enforces this (TEN-02).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Path, Request

from app.composition import Container
from app.domain.errors import AuthenticationError
from app.domain.tenancy import TenantContext, VerifiedIdentity
from app.request_context import request_id_var


def get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


ContainerDep = Annotated[Container, Depends(get_container)]


def authenticate(request: Request, container: ContainerDep) -> VerifiedIdentity:
    """Verify the bearer token. Cookies are deliberately not accepted (API-04)."""
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise AuthenticationError()
    return container.verifier.verify(token.strip())


IdentityDep = Annotated[VerifiedIdentity, Depends(authenticate)]


def personal_context(identity: IdentityDep, container: ContainerDep) -> TenantContext:
    return container.resolver.personal(identity)


def organization_context(
    identity: IdentityDep,
    container: ContainerDep,
    org_id: Annotated[str, Path(min_length=1, max_length=80, description="Clerk organization ID")],
) -> TenantContext:
    return container.resolver.organization(identity, org_id)


PersonalContext = Annotated[TenantContext, Depends(personal_context)]
OrganizationContext = Annotated[TenantContext, Depends(organization_context)]


def current_request_id() -> str | None:
    return request_id_var.get()
