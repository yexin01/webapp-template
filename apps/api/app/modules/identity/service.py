"""Identity use cases.

- ``ContextResolver`` turns a verified identity into a server-authoritative
  tenant context. Organization membership is read from the local mirror of the
  identity provider. Missing or stale mirror rows are refreshed from the
  provider before access is granted (DEC-009, TEN-07). If the provider cannot
  be reached, the request fails closed.
- ``IdentitySyncService`` applies identity webhooks. Like billing webhooks,
  they are triggers: membership events cause a re-read of the user's current
  memberships from the provider. So out-of-order delivery cannot resurrect a
  removed membership, and duplicates are harmless.
- ``MembershipReconciler`` is the scheduled job that re-verifies stale
  mirrors, so they stay fresh even for users who are not active.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime, timedelta
from uuid import UUID

from app.kernel.application.rate_limit import RateLimiter
from app.kernel.errors import NotConfiguredError, NotFoundError, RateLimitedError
from app.kernel.jobs import JobSummary
from app.kernel.ports import Clock, Transactions
from app.kernel.tenancy import ContextKind, TenantContext, VerifiedIdentity
from app.kernel.webhooks import EventStatus
from app.modules.identity.domain import MembershipRecord, is_valid_external_org_id
from app.modules.identity.ports import (
    IdentityRepository,
    IdentityWebhookEvent,
    IdentityWebhookVerifier,
    MembershipDirectory,
)

logger = logging.getLogger(__name__)

_MEMBERSHIP_EVENTS = frozenset(
    {
        "organizationMembership.created",
        "organizationMembership.updated",
        "organizationMembership.deleted",
    }
)


# How long after a full membership read a miss is answered from the mirror.
_MISS_REFRESH_COOLDOWN = timedelta(seconds=30)
# The window in which at most one miss per user calls the provider. It is
# short: a successful read already starts the cooldown above, so this window
# only spaces out retries after a failed read, and concurrent misses.
_MISS_REFRESH_CLAIM = timedelta(seconds=5)


class ContextResolver:
    def __init__(
        self,
        tx: Transactions[IdentityRepository],
        directory: MembershipDirectory,
        clock: Clock,
        max_staleness: timedelta,
        rate_limiter: RateLimiter,
    ) -> None:
        self._tx = tx
        self._directory = directory
        self._clock = clock
        self._max_staleness = max_staleness
        self._rate_limiter = rate_limiter
        self._miss_cooldown = min(_MISS_REFRESH_COOLDOWN, max_staleness)
        self._miss_claim_seconds = int(min(_MISS_REFRESH_CLAIM, max_staleness).total_seconds())

    def personal(self, identity: VerifiedIdentity) -> TenantContext:
        user_id = self._ensure_user(identity.external_user_id)
        return TenantContext(user_id, identity.external_user_id, ContextKind.PERSONAL)

    def organization(self, identity: VerifiedIdentity, external_org_id: str) -> TenantContext:
        # Malformed identifiers are indistinguishable from unknown ones (TEN-05).
        if not is_valid_external_org_id(external_org_id):
            raise NotFoundError()
        personal = self.personal(identity)

        membership = self._read_membership(personal, external_org_id)
        if membership is None and not self._may_refresh_on_miss(personal, identity, external_org_id):
            raise NotFoundError()
        if membership is None or self._is_stale(membership):
            # Never hold a request transaction open across a provider call.
            self._tx.release()
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
        # Stamped before the provider call: a slow answer must never look
        # fresher than it is, or the staleness bound would stretch.
        read_at = self._clock.now()
        memberships = self._directory.list_user_memberships(external_user_id)
        with self._tx.as_system() as t:
            t.repo.replace_user_memberships(user_id, memberships, read_at)

    def _ensure_user(self, external_user_id: str) -> UUID:
        with self._tx.as_identity(external_user_id) as t:
            user_id = t.repo.find_user_id(external_user_id)
            if user_id is None:
                t.repo.insert_user(external_user_id)
                user_id = t.repo.find_user_id(external_user_id)
        if user_id is None:  # pragma: no cover - guarded by the unique constraint
            raise RuntimeError("user could not be provisioned")
        return user_id

    def _read_membership(self, personal: TenantContext, external_org_id: str) -> MembershipRecord | None:
        with self._tx.as_tenant(personal) as t:
            return t.repo.get_my_membership(external_org_id)

    def _is_stale(self, membership: MembershipRecord) -> bool:
        return self._clock.now() - membership.verified_at > self._max_staleness

    def _may_refresh_on_miss(self, personal: TenantContext, identity: VerifiedIdentity, external_org_id: str) -> bool:
        """Whether a miss on this organization may call the provider.

        Answering misses from the mirror keeps a caller probing organization
        IDs from turning each request into a provider call. The exception is
        the organization the signed session names as active (for example, one
        the user just created): it is always re-checked. The claim only
        triggers the re-check; access still requires the refreshed mirror row.
        """
        if identity.active_org_id == external_org_id:
            return True
        if self._recently_verified(personal):
            return False  # a full read moments ago did not include this organization
        if self._miss_claim_seconds <= 0:
            return True
        # The claim is a worker transaction: end the request transaction first,
        # so this request never holds a request connection while it waits for
        # a worker connection.
        self._tx.release()
        try:
            # At most one miss-triggered refresh per user per window, across
            # instances: concurrent misses do not each call the provider.
            self._rate_limiter.hit(f"identity:miss-refresh:{personal.user_id}", 1, self._miss_claim_seconds)
        except RateLimitedError:
            return False
        return True

    def _recently_verified(self, personal: TenantContext) -> bool:
        with self._tx.as_tenant(personal) as t:
            verified_at = t.repo.get_my_memberships_verified_at()
        return verified_at is not None and self._clock.now() - verified_at < self._miss_cooldown


class IdentitySyncService:
    def __init__(
        self,
        tx: Transactions[IdentityRepository],
        verifier: IdentityWebhookVerifier | None,
        resolver: ContextResolver,
        clock: Clock,
    ) -> None:
        self._tx = tx
        self._verifier = verifier
        self._resolver = resolver
        self._clock = clock

    def handle_webhook(self, payload: bytes, headers: Mapping[str, str]) -> EventStatus:
        if self._verifier is None:
            raise NotConfiguredError()
        event = self._verifier.verify(payload, headers)

        with self._tx.as_system() as t:
            status = t.repo.claim_identity_event(event)
            if status in (EventStatus.PROCESSED, EventStatus.IGNORED):
                return status
            result, refresh = self._apply(t.repo, event, self._clock.now())

        # The provider call happens after the user row is committed and
        # outside any transaction. If it fails, the event stays "received",
        # the handler returns an error, and the sender retries.
        if refresh is not None:
            self._resolver.refresh_memberships(*refresh)

        with self._tx.as_system() as t:
            t.repo.finish_identity_event(event.event_id, result)
        return result

    @staticmethod
    def _apply(
        repo: IdentityRepository, event: IdentityWebhookEvent, now: datetime
    ) -> tuple[EventStatus, tuple[UUID, str] | None]:
        if event.event_type in _MEMBERSHIP_EVENTS:
            external_user_id = _membership_user_id(event.data)
            if external_user_id is None:
                return EventStatus.IGNORED, None
            return EventStatus.PROCESSED, (repo.ensure_user(external_user_id), external_user_id)

        if event.event_type == "organization.deleted":
            external_org_id = _str(event.data.get("id"))
            if external_org_id is None:
                return EventStatus.IGNORED, None
            repo.mark_org_deleted(external_org_id)
            return EventStatus.PROCESSED, None

        if event.event_type == "user.deleted":
            external_user_id = _str(event.data.get("id"))
            user_id = repo.find_user_id(external_user_id) if external_user_id else None
            if user_id is not None:
                # Access is revoked, and stamped like a membership read, so a
                # slower read taken before the deletion cannot bring it back.
                # Retention of the user's data is an adopter decision
                # (DEC-004, ADR-0002) and is not decided here.
                repo.remove_user_memberships(user_id, now)
            return EventStatus.PROCESSED, None

        return EventStatus.IGNORED, None


class MembershipReconciler:
    """Job ``reconcile-memberships``: re-verify the stalest mirrors, a bounded batch per run."""

    def __init__(
        self,
        tx: Transactions[IdentityRepository],
        resolver: ContextResolver,
        clock: Clock,
        max_staleness: timedelta,
        batch_size: int,
    ) -> None:
        self._tx = tx
        self._resolver = resolver
        self._clock = clock
        self._max_staleness = max_staleness
        self._batch_size = batch_size

    def run(self) -> JobSummary:
        with self._tx.as_system() as t:
            users = t.repo.list_users_with_stale_memberships(self._clock.now() - self._max_staleness, self._batch_size)
        refreshed = failed = 0
        for user_id, external_user_id in users:
            try:
                self._resolver.refresh_memberships(user_id, external_user_id)
                refreshed += 1
            except Exception:
                # One user's failure never stops the run; the rest are still re-verified.
                failed += 1
                logger.warning("membership_reconcile_failed", extra={"user_id": str(user_id)}, exc_info=True)
        return {"checked": len(users), "refreshed": refreshed, "failed": failed}


def _str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _membership_user_id(data: Mapping[str, object]) -> str | None:
    public = data.get("public_user_data")
    if isinstance(public, Mapping):
        return _str(public.get("user_id"))
    return None
