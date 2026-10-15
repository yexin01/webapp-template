"""Authentication contract between the identity module and every other module.

The identity module implements ``AuthDeps``. Other modules receive it at
composition and build their routes from it, so they never import the
identity module. Every protected route depends on ``AuthDeps.authenticate``,
directly or through a context dependency; a route-manifest test enforces this
(TEN-02).

A router that uses the request-scoped transaction lists
``Depends(auth.authenticate)`` before it, so a bad token is rejected before it
takes an admission place. FastAPI caches the result for the request, so the
token is still verified once.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

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
