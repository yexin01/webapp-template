"""Authentication contract between the identity module and every other module.

The identity module implements ``AuthDeps``. Other modules receive it at
composition and build their routes from it, so they never import the
identity module. Every protected route depends on ``AuthDeps.authenticate``,
directly or through a context dependency, and declares what authorizes it with
``requires``; a route-manifest test enforces both (TEN-02).

A router that uses the request-scoped transaction lists
``Depends(auth.authenticate)`` before it, so a bad token is rejected before it
takes an admission place. FastAPI caches the result for the request, so the
token is still verified once.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

from fastapi import Depends

from app.kernel.authorization import Access, Permission, recording_evaluations
from app.kernel.tenancy import TenantContext, VerifiedIdentity


@dataclass(frozen=True, slots=True)
class AuthDeps:
    """FastAPI dependency callables.

    - ``authenticate`` verifies the bearer token.
    - ``personal`` resolves the caller's personal context.
    - ``organization`` resolves the organization named by the ``{org_id}``
      path parameter, after verifying membership.
    """

    authenticate: Callable[..., VerifiedIdentity]
    personal: Callable[..., TenantContext]
    organization: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class Requires:
    """The dependency behind ``requires``. The route manifest finds a route's declaration by this type."""

    requirement: Permission | Access

    async def __call__(self) -> AsyncIterator[None]:
        with recording_evaluations() as evaluated:
            yield
        # Reached only when the route completed: a refusal or any other error skips it.
        if isinstance(self.requirement, Permission) and self.requirement.name not in evaluated:
            raise RuntimeError(f"A route declares {self.requirement.name} but nothing evaluated it. Call authorize().")


def requires(requirement: Permission | Access) -> Any:
    """Declare what authorizes a route (TEN-02): ``dependencies=[requires(PROJECT_READ)]``.

    Every protected route declares exactly one. The use case still decides, inside its
    transaction, and writes the audit entry of a denial (NT-07, ADM-01). For a
    ``Permission``, a request that completes without the use case evaluating it fails (a
    500, rolled back), so a forgotten ``authorize`` is found by the first test of the route.
    """
    return Depends(Requires(requirement), scope="function")
