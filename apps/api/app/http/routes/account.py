"""Current user and entitlement endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from app.http.deps import ContainerDep, OrganizationContext, PersonalContext
from app.http.schemas import Entitlement, Me

router = APIRouter(tags=["account"])


@router.get("/me", response_model=Me)
def me(ctx: PersonalContext) -> Me:
    return Me(user_id=ctx.user_id)


@router.get("/entitlement", response_model=Entitlement)
def personal_entitlement(ctx: PersonalContext, container: ContainerDep) -> Entitlement:
    return Entitlement(plan=container.billing.entitlement(ctx).plan, context="personal")


@router.get("/orgs/{org_id}/entitlement", response_model=Entitlement)
def organization_entitlement(ctx: OrganizationContext, container: ContainerDep) -> Entitlement:
    return Entitlement(plan=container.billing.entitlement(ctx).plan, context="organization")
