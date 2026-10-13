"""Resolve a verified identity into a server-authoritative tenant context.

Organization membership is read from the local mirror of the identity
provider. Missing or stale mirror rows are refreshed from the provider before
access is granted (DEC-009, TEN-07). If the provider cannot be reached, the
request fails closed.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from app.domain.errors import NotFoundError
from app.domain.tenancy import ContextKind, TenantContext, VerifiedIdentity, is_valid_external_org_id
from app.ports import Clock, MembershipDirectory, MembershipRecord, UnitOfWork


class ContextResolver:
    def __init__(
        self,
        uow: UnitOfWork,
        directory: MembershipDirectory,
        clock: Clock,
        max_staleness: timedelta,
    ) -> None:
        self._uow = uow
        self._directory = directory
        self._clock = clock
        self._max_staleness = max_staleness

    def personal(self, identity: VerifiedIdentity) -> TenantContext:
        user_id = self._ensure_user(identity.external_user_id)
        return TenantContext(user_id, identity.external_user_id, ContextKind.PERSONAL)

    def organization(self, identity: VerifiedIdentity, external_org_id: str) -> TenantContext:
        # Malformed identifiers are indistinguishable from unknown ones (TEN-05).
        if not is_valid_external_org_id(external_org_id):
            raise NotFoundError()
        personal = self.personal(identity)

        membership = self._read_membership(personal, external_org_id)
        if membership is None or self._is_stale(membership):
            self.refresh_memberships(personal.user_id, identity.external_user_id)
            membership = self._read_membership(personal, external_org_id)
        if membership is None:
            raise NotFoundError()

        return TenantContext(
            user_id=personal.user_id,
            external_user_id=identity.external_user_id,
            kind=ContextKind.ORGANIZATION,
            org_id=membership.org_id,
            external_org_id=membership.external_org_id,
            role=membership.role,
        )

    def refresh_memberships(self, user_id: UUID, external_user_id: str) -> None:
        memberships = self._directory.list_user_memberships(external_user_id)
        with self._uow.as_system() as repo:
            repo.replace_user_memberships(user_id, memberships, self._clock.now())

    def _ensure_user(self, external_user_id: str) -> UUID:
        with self._uow.as_identity(external_user_id) as repo:
            user_id = repo.find_user_id(external_user_id)
            if user_id is None:
                repo.insert_user(external_user_id)
                user_id = repo.find_user_id(external_user_id)
        if user_id is None:  # pragma: no cover - guarded by the unique constraint
            raise RuntimeError("user could not be provisioned")
        return user_id

    def _read_membership(self, personal: TenantContext, external_org_id: str) -> MembershipRecord | None:
        with self._uow.as_tenant(personal) as repo:
            return repo.get_my_membership(external_org_id)

    def _is_stale(self, membership: MembershipRecord) -> bool:
        return self._clock.now() - membership.verified_at > self._max_staleness
