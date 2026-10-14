"""Diagnostics probe for Clerk."""

from __future__ import annotations

from app.kernel.diagnostics import IntegrationInfo, Outcome, Verdict
from app.kernel.tenancy import TenantContext
from app.modules.identity.ports import MembershipDirectory


class IdentityProbe:
    def __init__(self, info: IntegrationInfo, directory: MembershipDirectory) -> None:
        self._info = info
        self._directory = directory

    @property
    def info(self) -> IntegrationInfo:
        return self._info

    def check(self, ctx: TenantContext) -> Verdict:
        memberships = self._directory.list_user_memberships(ctx.external_user_id)
        count = len(memberships)
        noun = "membership" if count == 1 else "memberships"
        return Verdict(
            Outcome.PASSED,
            f"Session token verified, and the Clerk Backend API returned {count} organization {noun}.",
        )
