"""Identity use cases.

- ``ContextResolver`` turns a verified identity into a server-authoritative
  tenant context. Organization membership is read from the local mirror of the
  identity provider. Missing or stale mirror rows are refreshed from the
  provider before access is granted (DEC-009, TEN-07). If the provider cannot
  be reached, the request fails closed. Provider reads are bounded per
  instance, and concurrent requests of one user share a read.
- ``IdentitySyncService`` applies identity webhooks. Like billing webhooks,
  they are triggers: membership events cause a re-read of the user's current
  memberships from the provider. So out-of-order delivery cannot resurrect a
  removed membership, and duplicates are harmless.
- ``MembershipReconciler`` is the scheduled job that re-verifies stale
  mirrors, so they stay fresh even for users who are not active.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import UUID

from app.kernel.application.rate_limit import RateLimiter
from app.kernel.errors import DependencyUnavailableError, NotConfiguredError, NotFoundError, RateLimitedError
from app.kernel.jobs import TIME_BUDGET_SECONDS, JobSummary
from app.kernel.ports import Clock, Transactions
from app.kernel.tenancy import ContextKind, TenantContext, VerifiedIdentity
from app.kernel.webhooks import EventStatus
from app.modules.identity.domain import MembershipRecord, is_valid_external_org_id
from app.modules.identity.ports import (
    IdentityRepository,
    IdentityWebhookEvent,
    IdentityWebhookVerifier,
    MembershipDirectory,
    MembershipListTooLargeError,
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
# only spaces out retries after a failed read, and concurrent misses. The
# session's active organization has its own claim with the same window.
_MISS_REFRESH_CLAIM = timedelta(seconds=5)
# Reads per window for the user and the organization the session names as active:
# a page's parallel first requests on two instances each find it, and a retry after
# a failed read does too, while a token naming an organization the user has left
# costs at most this many provider calls per window.
_ACTIVE_REFRESH_CLAIMS = 2
# How long a request waits for the read another request of the same user
# started on this instance: about one directory page's timeout.
_SHARED_READ_WAIT_SECONDS = 5.0
# Requests that may always wait for one read, besides the request making it, so a
# page's parallel requests share it. More wait only while over a quarter of the read
# slots stay free, so one user's burst never takes the slots other users' reads need.
_MAX_WAITERS_PER_READ = 2
# After a read refused for listing more memberships than one read may return, that
# user's memberships are not read again on this instance for this long: such a
# read costs up to ten provider calls, and such a list rarely shrinks.
_TOO_LARGE_RETRY_AFTER = timedelta(seconds=60)
_TOO_LARGE_REMEMBERED = 1_024  # users; bounds the memory this takes
_DIRECTORY_BUSY = "Organization directory is busy. Retry shortly."
_DIRECTORY_UNAVAILABLE = "Organization directory is unavailable."


@dataclass(slots=True)
class _SharedRead:
    """A membership read that concurrent requests of one user share on this instance."""

    started_at: datetime  # no later than the read's own stamp
    done: threading.Event = field(default_factory=threading.Event)
    # Set before ``done``: the read was applied, or deliberately not made (its claim was lost).
    ok: bool = False
    waiters: int = 0  # requests waiting for it now; guarded by the resolver's _shared_reads_lock


class _ReadSlots:
    """The threads this instance may hold for membership reads: reading, or waiting for another request's read."""

    def __init__(self, size: int) -> None:
        self.size = size
        self.reserve = size // 4  # what a burst sharing one read leaves free for other users' reads
        self.in_use = 0  # guarded by _lock
        self._lock = threading.Lock()

    def take(self, keep_free: int = 0) -> bool:
        """Take a slot if at least ``keep_free`` others stay free."""
        with self._lock:
            if self.in_use + keep_free >= self.size:
                return False
            self.in_use += 1
            return True

    def give_back(self) -> None:
        with self._lock:
            if self.in_use <= 0:
                raise RuntimeError("a membership read slot was given back twice")
            self.in_use -= 1


class ContextResolver:
    def __init__(
        self,
        tx: Transactions[IdentityRepository],
        directory: MembershipDirectory,
        clock: Clock,
        max_staleness: timedelta,
        rate_limiter: RateLimiter,
        max_concurrent_reads: int,
    ) -> None:
        self._tx = tx
        self._directory = directory
        self._clock = clock
        self._max_staleness = max_staleness
        self._rate_limiter = rate_limiter
        self._miss_cooldown = min(_MISS_REFRESH_COOLDOWN, max_staleness)
        self._miss_claim_seconds = int(min(_MISS_REFRESH_CLAIM, max_staleness).total_seconds())
        # A read holds its thread until the provider answers, and so does a request
        # waiting for another request's read: a slow provider must not take them all.
        self._read_slots = _ReadSlots(max_concurrent_reads)
        self._shared_reads: dict[tuple[UUID, str | None], _SharedRead] = {}  # emptied by each read's reader
        self._shared_reads_lock = threading.Lock()
        self._too_large_until: dict[UUID, datetime] = {}  # guarded by _too_large_lock
        self._too_large_lock = threading.Lock()

    def personal(self, identity: VerifiedIdentity) -> TenantContext:
        user_id = self._ensure_user(identity.external_user_id)
        return TenantContext(user_id, identity.external_user_id, ContextKind.PERSONAL)

    def organization(self, identity: VerifiedIdentity, external_org_id: str) -> TenantContext:
        # Malformed identifiers are indistinguishable from unknown ones (TEN-05).
        if not is_valid_external_org_id(external_org_id):
            raise NotFoundError()
        personal = self.personal(identity)

        checked_at = self._clock.now()
        membership = self._read_membership(personal, external_org_id)
        if membership is not None and self._is_stale(membership, checked_at):
            # The steady state for an active user: concurrent requests share one read.
            self._shared_refresh(personal, None, None, checked_at)
            membership = self._refreshed_membership(personal, external_org_id, checked_at)
        elif membership is None and identity.active_org_id == external_org_id:
            # The organization the signed session names as active (for example, one the
            # user just created) is re-checked at once, skipping the miss cooldown. Its
            # own claim keeps a token naming an organization the user has left from
            # turning every request into a provider call. The claim only triggers the
            # re-check; access still requires the refreshed mirror row.
            claim = f"identity:active-refresh:{personal.user_id}:{external_org_id}"
            self._shared_refresh(personal, external_org_id, claim, checked_at)
            membership = self._refreshed_membership(personal, external_org_id, checked_at)
        elif membership is None:
            self._refresh_on_miss(personal)
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
        """Re-read the user's memberships from the provider and apply them.

        Raises ``DependencyUnavailableError`` (503) when the provider cannot be
        read, when this instance already has its bound of reads in flight, or
        for a minute after the user's list proved too large to read.
        """
        with self._read_slot():
            self._read_and_apply(user_id, external_user_id)

    def _read_and_apply(self, user_id: UUID, external_user_id: str) -> None:
        # Stamped before the provider call: a slow answer must never look
        # fresher than it is, or the staleness bound would stretch.
        read_at = self._clock.now()
        with self._too_large_lock:
            until = self._too_large_until.get(user_id)
        if until is not None and read_at < until:
            raise MembershipListTooLargeError()
        try:
            memberships = self._directory.list_user_memberships(external_user_id)
        except MembershipListTooLargeError:
            self._remember_too_large(user_id, read_at + _TOO_LARGE_RETRY_AFTER)
            raise
        with self._tx.as_system() as t:
            t.repo.replace_user_memberships(user_id, memberships, read_at)

    def _remember_too_large(self, user_id: UUID, until: datetime) -> None:
        with self._too_large_lock:
            if len(self._too_large_until) >= _TOO_LARGE_REMEMBERED:
                now = self._clock.now()
                for expired in [u for u, t in self._too_large_until.items() if t <= now]:
                    del self._too_large_until[expired]
            if len(self._too_large_until) < _TOO_LARGE_REMEMBERED:
                self._too_large_until[user_id] = until

    @contextmanager
    def _read_slot(self) -> Iterator[None]:
        if not self._read_slots.take():
            raise self._busy()
        try:
            yield
        finally:
            self._read_slots.give_back()

    def _busy(self) -> DependencyUnavailableError:
        logger.warning("membership_reads_exhausted", extra={"limit": self._read_slots.size})
        return DependencyUnavailableError(_DIRECTORY_BUSY)

    def _shared_refresh(
        self, personal: TenantContext, org: str | None, claim: str | None, checked_at: datetime
    ) -> None:
        """Refresh the user's memberships once for the concurrent requests of this instance that need it.

        The first request reads (unless it loses ``claim``); the others wait for
        that read instead of each calling the provider, and fail closed (503) if
        it failed or took too long. A request joins only a read that began within
        the staleness bound of its own check (``checked_at``); otherwise it starts
        a new one, which later requests join. Every request holds a read slot, and
        one that may not take one gets 503 at once. Requests for an organization
        missing from the mirror share only a read started for that organization,
        so they never rely on a read that began before the organization existed.
        """
        # Never hold a request transaction open across a provider call, or while waiting for one.
        self._tx.release()
        key = (personal.user_id, org)
        shared, reader = self._join_shared_read(key, checked_at)
        if not reader:
            try:
                finished = shared.done.wait(_SHARED_READ_WAIT_SECONDS)
            finally:
                with self._shared_reads_lock:
                    shared.waiters -= 1
                self._read_slots.give_back()
            if not (finished and shared.ok):
                raise DependencyUnavailableError(_DIRECTORY_UNAVAILABLE)
            return
        try:
            if claim is None or self._claim(claim, _ACTIVE_REFRESH_CLAIMS):
                self._read_and_apply(personal.user_id, personal.external_user_id)
            shared.ok = True
        finally:
            with self._shared_reads_lock:
                if self._shared_reads.get(key) is shared:  # not yet replaced by a newer read
                    del self._shared_reads[key]
            self._read_slots.give_back()
            shared.done.set()

    def _join_shared_read(self, key: tuple[UUID, str | None], checked_at: datetime) -> tuple[_SharedRead, bool]:
        """The read for ``key``, and True if this request makes it (False: it waits for one in flight).

        The reader takes its slot as it registers the read, so the requests that
        join it can never take the slot the read itself needs. Raises
        ``DependencyUnavailableError`` when this request may not take a slot.
        """
        with self._shared_reads_lock:
            shared = self._shared_reads.get(key)
            if shared is None or checked_at - shared.started_at > self._max_staleness:
                # None in flight, or one too old to answer this request (TEN-07).
                if not self._read_slots.take():
                    raise self._busy()
                shared = self._shared_reads[key] = _SharedRead(started_at=checked_at)
                return shared, True
            keep_free = 0 if shared.waiters < _MAX_WAITERS_PER_READ else self._read_slots.reserve
            if not self._read_slots.take(keep_free):
                raise self._busy()
            shared.waiters += 1
            return shared, False

    def _claim(self, bucket: str, limit: int) -> bool:
        """Whether this request may call the provider: at most ``limit`` per bucket and window, across instances.

        The claim is a worker transaction, so the request transaction must already
        be released: a request must never hold a request connection while it
        waits for a worker connection.
        """
        if self._miss_claim_seconds <= 0:
            return True
        try:
            self._rate_limiter.hit(bucket, limit, self._miss_claim_seconds)
        except RateLimitedError:
            return False
        return True

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

    def _refreshed_membership(
        self, personal: TenantContext, external_org_id: str, checked_at: datetime
    ) -> MembershipRecord | None:
        """The membership after a refresh; 503 if it is too old for a request that checked at ``checked_at``.

        A row this request's own read applied never is. One that another read
        applied (a request that lost its claim reads what other instances wrote)
        may have been read longer before this request than the bound allows.
        """
        membership = self._read_membership(personal, external_org_id)
        if membership is not None and self._is_stale(membership, checked_at):
            raise DependencyUnavailableError(_DIRECTORY_UNAVAILABLE)
        return membership

    def _is_stale(self, membership: MembershipRecord, at: datetime) -> bool:
        return at - membership.verified_at > self._max_staleness

    def _refresh_on_miss(self, personal: TenantContext) -> None:
        """Re-read the memberships on a miss on an organization other than the session's active one, if allowed.

        Answering misses from the mirror keeps a caller probing organization
        IDs from turning each request into a provider call.
        """
        if self._recently_verified(personal):
            return  # a full read moments ago did not include this organization
        # Never hold a request transaction open across a provider call, or the claim's worker transaction.
        self._tx.release()
        # The slot first: a request refused on a busy instance must not use up the user's claim.
        with self._read_slot():
            # At most one miss-triggered refresh per user per window, across
            # instances: concurrent misses do not each call the provider.
            if self._claim(f"identity:miss-refresh:{personal.user_id}", 1):
                self._read_and_apply(personal.user_id, personal.external_user_id)

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
    """Job ``reconcile-memberships``: re-verify stale mirrors, a bounded batch per run.

    Users with a stale membership are taken least recently tried or read first,
    so mirrors that no request keeps fresh come first, and every attempt is
    recorded whatever its outcome, so a user whose read keeps failing moves
    behind the others instead of heading every run. A run stops starting new
    reads once its time budget is spent, or once the provider is unavailable
    (every later read would fail too); the next run continues.
    """

    def __init__(
        self,
        tx: Transactions[IdentityRepository],
        resolver: ContextResolver,
        clock: Clock,
        max_staleness: timedelta,
        batch_size: int,
        *,
        time_budget_seconds: float = TIME_BUDGET_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._tx = tx
        self._resolver = resolver
        self._clock = clock
        self._max_staleness = max_staleness
        self._batch_size = batch_size
        self._time_budget_seconds = time_budget_seconds
        self._monotonic = monotonic

    def run(self) -> JobSummary:
        with self._tx.as_system() as t:
            users = t.repo.list_users_with_stale_memberships(self._clock.now() - self._max_staleness, self._batch_size)
        deadline = self._monotonic() + self._time_budget_seconds
        checked = refreshed = failed = 0
        for user_id, external_user_id in users:
            if self._monotonic() >= deadline:
                logger.warning(
                    "membership_reconcile_stopped", extra={"reason": "time_budget", "left": len(users) - checked}
                )
                break
            checked += 1
            try:
                with self._tx.as_system() as t:
                    t.repo.mark_memberships_checked(user_id, self._clock.now())
                self._resolver.refresh_memberships(user_id, external_user_id)
                refreshed += 1
            except Exception as exc:
                failed += 1
                if isinstance(exc, DependencyUnavailableError) and not isinstance(exc, MembershipListTooLargeError):
                    # The provider is down (its reads now fail fast), or every read slot is busy.
                    logger.warning(
                        "membership_reconcile_stopped",
                        extra={"reason": "unavailable", "left": len(users) - checked},
                        exc_info=True,
                    )
                    break
                # One user's failure never stops the run; the rest are still re-verified.
                logger.warning("membership_reconcile_failed", extra={"user_id": str(user_id)}, exc_info=True)
        return {"checked": checked, "refreshed": refreshed, "failed": failed}


def _str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _membership_user_id(data: Mapping[str, object]) -> str | None:
    public = data.get("public_user_data")
    if isinstance(public, Mapping):
        return _str(public.get("user_id"))
    return None
