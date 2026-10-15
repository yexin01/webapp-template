"""A membership change that commits between context resolution and the write (TEN-05, TEN-07, NT-06).

A request resolves its organization context before it writes, so a removal or
a downgrade can commit in between. These helpers commit one right after
resolution, through the real request path, with no timing involved.
"""

from __future__ import annotations

import pytest

from app.kernel.tenancy import TenantContext, VerifiedIdentity
from tests.conftest import Harness, superuser_query

REMOVE = "DELETE FROM app.organization_memberships WHERE org_id = %s AND user_id = %s"
DOWNGRADE = "UPDATE app.organization_memberships SET role = 'member' WHERE org_id = %s AND user_id = %s"


def change_after_resolution(harness: Harness, monkeypatch: pytest.MonkeyPatch, sql: str) -> None:
    """Commit ``sql`` on the caller's membership right after each organization context is resolved."""
    resolver = harness.container.identity.resolver
    resolve = resolver.organization

    def resolve_then_change(identity: VerifiedIdentity, external_org_id: str) -> TenantContext:
        ctx = resolve(identity, external_org_id)
        superuser_query(harness.database_url, sql, (ctx.org_id, ctx.user_id))
        return ctx

    monkeypatch.setattr(resolver, "organization", resolve_then_change)
