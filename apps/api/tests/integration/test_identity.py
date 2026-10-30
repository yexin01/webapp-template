"""Identity: membership refresh concurrency, the session's active organization,
user deletion, reconciliation, and authentication before admission.

Through the real database and HTTP stack; Clerk is the in-memory directory
(and, where the token claims matter, the production verifier with a local key).
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections.abc import Callable, Collection
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from svix.webhooks import Webhook

from app.kernel.errors import DependencyUnavailableError, DomainError, NotFoundError, RateLimitedError
from app.kernel.ports import AuditEntry
from app.kernel.tenancy import Role, VerifiedIdentity
from app.modules.identity import service as identity_service
from app.modules.identity.adapters import clerk, repository
from app.modules.identity.adapters.clerk import ClerkMembershipDirectory, ClerkTokenVerifier, ClerkWebhookVerifier
from app.modules.identity.domain import ExternalMembership
from tests.conftest import Harness, TokenTableVerifier, superuser_query
from tests.integration.test_platform import SVIX_SECRET, clerk_send
from tests.lock_waits import wait_for_lock_wait

pytestmark = pytest.mark.db

ISSUER = "https://issuer.example"
ORIGIN = "http://localhost:3000"


def _user_id(h: Harness, external_user_id: str) -> Any:
    return superuser_query(h.database_url, "SELECT id FROM app.users WHERE clerk_user_id=%s", (external_user_id,))[0][0]


def _memberships(h: Harness) -> int:
    return int(superuser_query(h.database_url, "SELECT count(*) FROM app.organization_memberships")[0][0])


def _wait_until(condition: Callable[[], bool], seconds: float = 5) -> bool:
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.01)
    return condition()


def _busy_read_slots(h: Harness) -> int:
    """Requests reading memberships from Clerk, or waiting for another request's read, on this instance."""
    return h.container.identity.resolver._read_slots.in_use


class BlockingDirectory:
    """Wraps the in-memory directory: each read waits until ``finish`` is set, and the reads in flight are counted.

    With ``users``, only those users' reads wait (and are counted); the others are answered at once.
    """

    def __init__(self, h: Harness, fail: bool = False, users: Collection[str] | None = None) -> None:
        self.finish = threading.Event()
        self.reads = 0
        self.inside = 0
        self.peak = 0
        lock = threading.Lock()
        directory = h.directory
        original = type(directory).list_user_memberships

        def read(external_user_id: str) -> list[ExternalMembership]:
            if users is not None and external_user_id not in users:
                return original(directory, external_user_id)
            with lock:
                self.reads += 1
                self.inside += 1
                self.peak = max(self.peak, self.inside)
            try:
                assert self.finish.wait(10)  # a Clerk round trip that outlasts the other requests
                if fail:
                    raise DependencyUnavailableError("Organization directory is unavailable.")
                return original(directory, external_user_id)
            finally:
                with lock:
                    self.inside -= 1

        directory.list_user_memberships = read  # type: ignore[method-assign]


def _resolve(h: Harness, identity: VerifiedIdentity, org: str, outcomes: list[str]) -> None:
    try:
        h.container.identity.resolver.organization(identity, org)
        outcomes.append("found")
    except DomainError as exc:
        outcomes.append(exc.code)


def _signed_clerk_event(event_type: str, data: dict[str, Any], msg_id: str) -> tuple[bytes, dict[str, str]]:
    payload = json.dumps({"type": event_type, "data": data, "object": "event"}).encode()
    ts = int(time.time())
    signature = Webhook(SVIX_SECRET).sign(msg_id, datetime.fromtimestamp(ts, UTC), payload.decode())
    headers = {"svix-id": msg_id, "svix-timestamp": str(ts), "svix-signature": signature}
    return payload, {**headers, "content-type": "application/json"}


def _event_status(h: Harness, msg_id: str) -> str:
    return str(
        superuser_query(h.database_url, "SELECT status FROM app.identity_events WHERE event_id=%s", (msg_id,))[0][0]
    )


# -- Misses: at most one provider call per user per window (DEC-009) -------------------


def test_concurrent_misses_call_the_provider_once(harness: Harness) -> None:
    h = harness
    resolver = h.container.identity.resolver
    carol = VerifiedIdentity("user_carol")
    resolver.personal(carol)  # provisioned, never verified
    in_provider = threading.Event()
    finish = threading.Event()
    original = type(h.directory).list_user_memberships

    def slow(self: Any, external_user_id: str) -> list[ExternalMembership]:
        in_provider.set()
        assert finish.wait(10)  # a Clerk round trip that outlasts the other requests
        return original(self, external_user_id)

    h.directory.list_user_memberships = slow.__get__(h.directory)  # type: ignore[method-assign]
    outcomes: list[str] = []

    def probe(n: int) -> None:
        try:
            resolver.organization(carol, f"org_probe{n}")
            outcomes.append("found")
        except NotFoundError:
            outcomes.append("404")

    threads = [threading.Thread(target=probe, args=(n,)) for n in range(6)]
    for thread in threads:
        thread.start()
    assert in_provider.wait(10)
    deadline = time.monotonic() + 5
    while len(outcomes) < 5 and time.monotonic() < deadline:
        time.sleep(0.01)
    finish.set()
    for thread in threads:
        thread.join(10)
    assert outcomes == ["404"] * 6
    assert h.directory.calls == 1


@pytest.mark.parametrize("membership", ["missing", "stale"])
def test_a_refresh_releases_the_request_transaction(harness: Harness, membership: str) -> None:
    h = harness
    headers = h.login("user_new")
    h.directory.set("user_new", [ExternalMembership("org_q", Role.MEMBER)])
    if membership == "stale":
        assert h.client.get("/api/v1/orgs/org_q/projects", headers=headers).status_code == 200
        h.clock.advance(seconds=h.container.settings.membership_max_staleness_seconds + 1)
    seen: list[bool] = []
    original = h.directory.list_user_memberships

    def spy(external_user_id: str) -> list[ExternalMembership]:
        seen.append(h.container.kernel.database.in_request_transaction())
        return original(external_user_id)

    h.directory.list_user_memberships = spy  # type: ignore[method-assign]
    assert h.client.get("/api/v1/orgs/org_q/projects", headers=headers).status_code == 200
    assert seen == [False]


def test_the_miss_claim_is_taken_outside_the_request_transaction(harness: Harness) -> None:
    """The claim needs a worker connection; waiting for one while holding a request connection
    and an admission place would let a busy worker pool stall request-scoped routes."""
    h = harness
    headers = h.login("user_claim")
    h.directory.set("user_claim", [ExternalMembership("org_r", Role.MEMBER)])
    limiter = h.container.kernel.rate_limiter
    seen: list[bool] = []
    original = limiter.hit

    def spy(bucket: str, limit: int, window_seconds: int) -> None:
        if bucket.startswith("identity:"):
            seen.append(h.container.kernel.database.in_request_transaction())
        original(bucket, limit, window_seconds)

    limiter.hit = spy  # type: ignore[method-assign]
    assert h.client.get("/api/v1/orgs/org_r/projects", headers=headers).status_code == 200
    assert seen == [False]


def test_a_miss_refused_while_read_slots_are_busy_keeps_the_users_claim(make_harness: Any) -> None:
    """The read slot is taken before the claim: otherwise a miss refused on a busy instance would use
    up the user's claim, and the next miss within the window would get 404 without asking Clerk."""
    h: Harness = make_harness({"clerk_max_concurrent_reads": 3})
    holders = [_stale_member(h, f"user_h{n}") for n in range(3)]
    h.directory.set("user_m", [ExternalMembership("org_m", Role.MEMBER)])
    h.clock.advance(seconds=h.container.settings.membership_max_staleness_seconds + 1)
    clerk_api = BlockingDirectory(h, users={"user_h0", "user_h1", "user_h2"})
    outcomes: list[str] = []
    threads = [threading.Thread(target=_resolve, args=(h, holder, "org_d", outcomes)) for holder in holders]
    member = VerifiedIdentity("user_m")
    mine: list[str] = []
    try:
        for thread in threads:
            thread.start()
        assert _wait_until(lambda: clerk_api.inside == 3)  # every read slot is taken
        _resolve(h, member, "org_m", mine)
        assert mine == ["DEPENDENCY_UNAVAILABLE"]
    finally:
        clerk_api.finish.set()
        for thread in threads:
            thread.join(10)
    _resolve(h, member, "org_m", mine)  # the same claim window
    assert mine == ["DEPENDENCY_UNAVAILABLE", "found"]


def test_a_failed_miss_refresh_is_retried_within_seconds(harness: Harness) -> None:
    """Only a successful read answers misses from the mirror for the cooldown. After a failed
    read (a Clerk blip), the next miss a few seconds later asks Clerk again."""
    h = harness
    headers = h.login("user_blip")
    h.directory.set("user_blip", [ExternalMembership("org_b", Role.MEMBER)])
    h.directory.available = False
    assert h.client.get("/api/v1/orgs/org_b/projects", headers=headers).status_code == 503
    h.directory.available = True
    h.clock.advance(seconds=6)
    assert h.client.get("/api/v1/orgs/org_b/projects", headers=headers).status_code == 200


# -- Stale memberships: one read per user per instance, and a bound per instance --------


def _stale_member(h: Harness, external_user_id: str, org: str = "org_d") -> VerifiedIdentity:
    identity = VerifiedIdentity(external_user_id)
    h.directory.set(external_user_id, [ExternalMembership(org, Role.MEMBER)])
    h.container.identity.resolver.organization(identity, org)
    return identity


def test_concurrent_stale_requests_share_one_read(harness: Harness) -> None:
    """Re-verifying a stale membership is the steady state; a page's parallel requests make one Clerk call."""
    h = harness
    dave = _stale_member(h, "user_dave")
    h.clock.advance(seconds=h.container.settings.membership_max_staleness_seconds + 1)
    calls = h.directory.calls
    clerk_api = BlockingDirectory(h)
    outcomes: list[str] = []
    threads = [threading.Thread(target=_resolve, args=(h, dave, "org_d", outcomes)) for _ in range(3)]
    try:
        for thread in threads:
            thread.start()
        assert _wait_until(lambda: _busy_read_slots(h) == 3)  # one reads, the others wait for that read
    finally:
        clerk_api.finish.set()
        for thread in threads:
            thread.join(10)
    assert outcomes == ["found"] * 3
    assert h.directory.calls - calls == 1


def test_a_failed_shared_read_fails_every_waiting_request_closed(harness: Harness) -> None:
    """Requests that waited for a read that failed are never answered from the stale row (TEN-07)."""
    h = harness
    dave = _stale_member(h, "user_dave")
    h.clock.advance(seconds=h.container.settings.membership_max_staleness_seconds + 1)
    clerk_api = BlockingDirectory(h, fail=True)
    outcomes: list[str] = []
    threads = [threading.Thread(target=_resolve, args=(h, dave, "org_d", outcomes)) for _ in range(3)]
    try:
        for thread in threads:
            thread.start()
        assert _wait_until(lambda: _busy_read_slots(h) == 3)
    finally:
        clerk_api.finish.set()
        for thread in threads:
            thread.join(10)
    assert outcomes == ["DEPENDENCY_UNAVAILABLE"] * 3
    assert clerk_api.reads == 1


def test_a_burst_shares_one_read_while_read_slots_are_free(harness: Harness) -> None:
    """A page with many parallel organization requests at a staleness boundary, with read slots to
    spare: every request waits for the one read, none gets 503."""
    h = harness
    dave = _stale_member(h, "user_dave")
    h.clock.advance(seconds=h.container.settings.membership_max_staleness_seconds + 1)
    calls = h.directory.calls
    clerk_api = BlockingDirectory(h)
    outcomes: list[str] = []
    threads = [threading.Thread(target=_resolve, args=(h, dave, "org_d", outcomes)) for _ in range(6)]
    try:
        for thread in threads:
            thread.start()
        assert _wait_until(lambda: _busy_read_slots(h) + len(outcomes) == 6)
        assert outcomes == []  # one reads, five wait for that read
    finally:
        clerk_api.finish.set()
        for thread in threads:
            thread.join(10)
    assert outcomes == ["found"] * 6
    assert h.directory.calls - calls == 1


def test_one_users_burst_never_takes_the_read_slots_other_users_need(make_harness: Any) -> None:
    """Two requests may always wait for a read, besides the one making it; more only while over a
    quarter of the read slots stay free, and the rest of a burst gets 503 at once. So while Clerk
    is slow, other users' re-verifications still find a slot."""
    h: Harness = make_harness({"clerk_max_concurrent_reads": 8})  # two slots stay free
    alice = _stale_member(h, "user_alice")
    bob = _stale_member(h, "user_bob")
    h.clock.advance(seconds=h.container.settings.membership_max_staleness_seconds + 1)
    clerk_api = BlockingDirectory(h, users={"user_alice"})
    outcomes: list[str] = []
    threads = [threading.Thread(target=_resolve, args=(h, alice, "org_d", outcomes)) for _ in range(8)]
    try:
        for thread in threads:
            thread.start()
        assert _wait_until(lambda: _busy_read_slots(h) + len(outcomes) == 8)
        assert outcomes == ["DEPENDENCY_UNAVAILABLE"] * 2  # refused at once
        assert _busy_read_slots(h) == 6  # alice's read, and five requests waiting for it
        bobs: list[str] = []
        _resolve(h, bob, "org_d", bobs)
        assert bobs == ["found"]
    finally:
        clerk_api.finish.set()
        for thread in threads:
            thread.join(10)
    assert sorted(outcomes) == ["DEPENDENCY_UNAVAILABLE"] * 2 + ["found"] * 6
    assert clerk_api.reads == 1


def test_a_request_that_gave_up_waiting_no_longer_counts_as_waiting(
    make_harness: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Otherwise, once two requests had given up on a slow read, the user's next request would count
    as a third waiter and be refused whenever slots run short, although nobody is waiting."""
    h: Harness = make_harness({"clerk_max_concurrent_reads": 4})  # one slot stays free past two waiters
    dave = _stale_member(h, "user_dave")
    erin, frank = _stale_member(h, "user_erin"), _stale_member(h, "user_frank")
    h.clock.advance(seconds=h.container.settings.membership_max_staleness_seconds + 1)
    clerk_api = BlockingDirectory(h)
    outcomes: list[str] = []
    threads = [threading.Thread(target=_resolve, args=(h, dave, "org_d", outcomes))]
    try:
        threads[0].start()
        assert _wait_until(lambda: clerk_api.inside == 1)  # dave's read is in flight
        monkeypatch.setattr(identity_service, "_SHARED_READ_WAIT_SECONDS", 0.05)
        gave_up = [threading.Thread(target=_resolve, args=(h, dave, "org_d", outcomes)) for _ in range(2)]
        for thread in gave_up:
            thread.start()
        for thread in gave_up:
            thread.join(10)
        assert outcomes == ["DEPENDENCY_UNAVAILABLE"] * 2  # they waited, then gave up
        monkeypatch.setattr(identity_service, "_SHARED_READ_WAIT_SECONDS", 10.0)
        threads += [threading.Thread(target=_resolve, args=(h, user, "org_d", outcomes)) for user in (erin, frank)]
        for thread in threads[1:]:
            thread.start()
        assert _wait_until(lambda: clerk_api.inside == 3)  # one read slot left
        threads.append(threading.Thread(target=_resolve, args=(h, dave, "org_d", outcomes)))
        threads[-1].start()
        assert _wait_until(lambda: _busy_read_slots(h) == 4 or len(outcomes) > 2)
        assert outcomes == ["DEPENDENCY_UNAVAILABLE"] * 2  # the new request waits for dave's read
    finally:
        clerk_api.finish.set()
        for thread in threads:
            thread.join(10)
    assert sorted(outcomes) == ["DEPENDENCY_UNAVAILABLE"] * 2 + ["found"] * 4
    assert clerk_api.reads == 3


def test_a_read_is_shared_only_once_its_reader_holds_a_read_slot(harness: Harness) -> None:
    """Otherwise requests joining the read could take the slot the read needs: the reader would be
    refused, and every request of the burst with it (503), although the bound had room for the read."""
    h = harness
    dave = _stale_member(h, "user_dave")
    h.clock.advance(seconds=h.container.settings.membership_max_staleness_seconds + 1)
    resolver = h.container.identity.resolver
    slots = resolver._read_slots
    taking, go = threading.Event(), threading.Event()
    take = slots.take

    def descheduled(keep_free: int = 0) -> bool:  # the reader is preempted just before it takes its slot
        taking.set()
        assert go.wait(10)
        return take(keep_free)

    slots.take = descheduled  # type: ignore[method-assign]
    outcome: list[str] = []
    reader = threading.Thread(target=_resolve, args=(h, dave, "org_d", outcome))
    reader.start()
    try:
        assert taking.wait(10)
        assert resolver._shared_reads == {}  # no read another request could join yet
    finally:
        slots.take = take  # type: ignore[method-assign]
        go.set()
        reader.join(10)
    assert outcome == ["found"]


def test_a_request_never_relies_on_a_read_that_began_too_long_before_it(make_harness: Any) -> None:
    """With a short staleness bound and a slow Clerk, the read in flight for the user may have begun
    longer before a request than the bound allows: that request reads Clerk itself (TEN-07)."""
    h: Harness = make_harness({"membership_max_staleness_seconds": 2})
    dave = _stale_member(h, "user_dave")
    h.clock.advance(seconds=3)
    calls = h.directory.calls
    original = type(h.directory).list_user_memberships
    started, finish = threading.Event(), threading.Event()

    def slow(self: Any, external_user_id: str) -> list[ExternalMembership]:
        seen = original(self, external_user_id)  # what Clerk listed when the read began
        started.set()
        assert finish.wait(10)
        return seen

    h.directory.list_user_memberships = slow.__get__(h.directory)  # type: ignore[method-assign]
    first: list[str] = []
    later: list[str] = []
    threads = [threading.Thread(target=_resolve, args=(h, dave, "org_d", first))]
    threads[0].start()
    try:
        assert started.wait(10)  # this read began while dave was a member
        h.directory.set("user_dave", [])  # removed in Clerk
        h.clock.advance(seconds=3)  # the read in flight is now older than the bound
        threads.append(threading.Thread(target=_resolve, args=(h, dave, "org_d", later)))
        threads[1].start()
        assert _wait_until(lambda: _busy_read_slots(h) == 2)
    finally:
        finish.set()
        for thread in threads:
            thread.join(10)
    assert later == ["NOT_FOUND"]
    assert first in (["found"], ["NOT_FOUND"])  # either: its read began before the removal, and the two race
    assert h.directory.calls - calls == 2
    assert _busy_read_slots(h) == 0


def test_a_row_applied_by_another_read_answers_a_request_only_within_the_bound(make_harness: Any) -> None:
    """A request that did not read Clerk itself (its claim was lost: other instances are reading) is
    answered from the mirror only with a row verified within the staleness bound of its own check."""
    h: Harness = make_harness({"membership_max_staleness_seconds": 10})
    alice = _stale_member(h, "user_alice", "org_a")
    both = [ExternalMembership("org_a", Role.MEMBER), ExternalMembership("org_b", Role.ADMIN)]
    h.directory.set("user_alice", both)  # org_b was just created and made active
    h.clock.advance(seconds=11)
    original = type(h.directory).list_user_memberships
    started, finish = threading.Event(), threading.Event()

    def slow_stale_read(self: Any, external_user_id: str) -> list[ExternalMembership]:
        seen = original(self, external_user_id)
        if threading.current_thread().name == "stale-read":
            started.set()
            assert finish.wait(10)
        return seen

    h.directory.list_user_memberships = slow_stale_read.__get__(h.directory)  # type: ignore[method-assign]
    stale_outcome: list[str] = []
    stale = threading.Thread(target=_resolve, args=(h, alice, "org_a", stale_outcome), name="stale-read")
    stale.start()
    try:
        assert started.wait(10)  # a read listing org_b began ...
        h.clock.advance(seconds=11)  # ... longer before the request below than the bound
        limiter = h.container.kernel.rate_limiter
        original_hit = limiter.hit

        def lost_once_that_read_is_applied(bucket: str, limit: int, window_seconds: int) -> None:
            if bucket.startswith("identity:active-refresh:"):
                finish.set()
                stale.join(10)
                raise RateLimitedError(retry_after_seconds=1)
            original_hit(bucket, limit, window_seconds)

        limiter.hit = lost_once_that_read_is_applied  # type: ignore[method-assign]
        outcome: list[str] = []
        _resolve(h, VerifiedIdentity("user_alice", active_org_id="org_b"), "org_b", outcome)
    finally:
        finish.set()
        stale.join(10)
    assert stale_outcome == ["found"]
    assert outcome == ["DEPENDENCY_UNAVAILABLE"]  # the org_b row is 11 s older than this request's check


def test_membership_reads_in_flight_are_bounded_per_instance(make_harness: Any) -> None:
    """Each read holds a request thread until Clerk answers. Past the bound, a request that needs a
    read fails closed at once (503), as during a Clerk outage, instead of taking another thread."""
    h: Harness = make_harness({"clerk_max_concurrent_reads": 3})
    users = [_stale_member(h, f"user_{n}", "org_s") for n in range(6)]
    h.clock.advance(seconds=h.container.settings.membership_max_staleness_seconds + 1)
    calls = h.directory.calls
    clerk_api = BlockingDirectory(h)
    outcomes: list[str] = []
    threads = [threading.Thread(target=_resolve, args=(h, user, "org_s", outcomes)) for user in users]
    try:
        for thread in threads:
            thread.start()
        assert _wait_until(lambda: len(outcomes) == 3)  # refused at once, while three reads are in flight
        assert outcomes == ["DEPENDENCY_UNAVAILABLE"] * 3
        assert clerk_api.inside == 3
    finally:
        clerk_api.finish.set()
        for thread in threads:
            thread.join(10)
    assert sorted(outcomes) == ["DEPENDENCY_UNAVAILABLE"] * 3 + ["found"] * 3
    assert clerk_api.peak == 3
    assert h.directory.calls - calls == 3


def test_requests_waiting_for_a_read_count_against_the_bound(make_harness: Any) -> None:
    """A request waiting for another request's read holds a thread too, so one user's burst stays bounded."""
    h: Harness = make_harness({"clerk_max_concurrent_reads": 3})
    dave = _stale_member(h, "user_dave")
    h.clock.advance(seconds=h.container.settings.membership_max_staleness_seconds + 1)
    clerk_api = BlockingDirectory(h)
    outcomes: list[str] = []
    threads = [threading.Thread(target=_resolve, args=(h, dave, "org_d", outcomes)) for _ in range(5)]
    try:
        for thread in threads:
            thread.start()
        assert _wait_until(lambda: len(outcomes) == 2)  # one reads, two wait for it, two are refused
        assert outcomes == ["DEPENDENCY_UNAVAILABLE"] * 2
    finally:
        clerk_api.finish.set()
        for thread in threads:
            thread.join(10)
    assert outcomes == ["DEPENDENCY_UNAVAILABLE"] * 2 + ["found"] * 3
    assert clerk_api.reads == 1


def test_a_slow_directory_never_takes_every_request_thread(make_harness: Any) -> None:
    """A burst of membership webhooks while Clerk is slow: deliveries past the bound get 503 at once
    (Svix retries them), so the rest of the instance keeps its threads: a route whose dependencies
    run on them (/me) still answers at once."""
    from fastapi.testclient import TestClient

    from app.bootstrap import create_app

    h: Harness = make_harness(identity_webhook_verifier=ClerkWebhookVerifier(SVIX_SECRET))
    bound = h.container.settings.clerk_max_concurrent_reads
    clerk_api = BlockingDirectory(h)
    deliveries = 45  # more than the 40 request threads
    codes: list[int] = []
    member = h.login("user_probe")
    with TestClient(create_app(h.container), raise_server_exceptions=False) as client:  # one event loop

        def deliver(n: int) -> None:
            data = {"organization": {"id": "org_bulk"}, "public_user_data": {"user_id": f"user_bulk{n}"}}
            payload, headers = _signed_clerk_event("organizationMembership.updated", data, f"msg_bulk{n}")
            codes.append(client.post("/api/v1/webhooks/clerk", content=payload, headers=headers).status_code)

        threads = [threading.Thread(target=deliver, args=(n,)) for n in range(deliveries)]
        try:
            for thread in threads:
                thread.start()
            assert _wait_until(lambda: len(codes) == deliveries - bound, seconds=10)
            for path, headers in (("/api/v1/me", member), ("/api/v1/health", {})):
                started = time.monotonic()
                assert client.get(path, headers=headers).status_code == 200
                assert time.monotonic() - started < 1.0, path
        finally:
            clerk_api.finish.set()
            for thread in threads:
                thread.join(10)
    assert sorted(codes) == [200] * bound + [503] * (deliveries - bound)
    assert clerk_api.peak == bound


def test_a_webhook_refused_while_reads_are_busy_is_retried(make_harness: Any) -> None:
    """The refused delivery stays unprocessed, so Svix's retry of the same message re-reads Clerk."""
    h: Harness = make_harness(
        {"clerk_max_concurrent_reads": 3}, identity_webhook_verifier=ClerkWebhookVerifier(SVIX_SECRET)
    )
    resolver = h.container.identity.resolver
    holders: list[threading.Thread] = []
    for user in ("user_h1", "user_h2", "user_h3"):
        user_id = resolver.personal(VerifiedIdentity(user)).user_id
        holders.append(threading.Thread(target=resolver.refresh_memberships, args=(user_id, user)))
    clerk_api = BlockingDirectory(h)
    for holder in holders:
        holder.start()
    try:
        assert _wait_until(lambda: clerk_api.inside == 3)  # every read slot is taken
        h.directory.set("user_w", [ExternalMembership("org_w", Role.MEMBER)])
        data = {"organization": {"id": "org_w"}, "public_user_data": {"user_id": "user_w"}}
        assert clerk_send(h, "organizationMembership.created", data, msg_id="msg_busy").status_code == 503
        assert _event_status(h, "msg_busy") == "received"
    finally:
        clerk_api.finish.set()
        for holder in holders:
            holder.join(10)
    assert clerk_send(h, "organizationMembership.created", data, msg_id="msg_busy").json()["status"] == "processed"
    assert _event_status(h, "msg_busy") == "processed"
    assert h.client.get("/api/v1/orgs/org_w/projects", headers=h.login("user_w")).status_code == 200


# -- The session's active organization (Clerk session token claims) ----------------


@pytest.fixture
def signed(make_harness: Any) -> tuple[Harness, Any]:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = (
        private.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    verifier = ClerkTokenVerifier(issuer=ISSUER, authorized_parties=[ORIGIN], jwt_public_key=pem)
    return make_harness(verifier=verifier), private


def _bearer(private: Any, sub: str, **claims: Any) -> dict[str, str]:
    now = int(time.time())
    body = {"sub": sub, "iss": ISSUER, "azp": ORIGIN, "iat": now, "exp": now + 60, "v": 2, **claims}
    return {"Authorization": f"Bearer {jwt.encode(body, private, algorithm='RS256')}"}


def test_a_new_active_organization_is_found_at_once(signed: tuple[Harness, Any]) -> None:
    """Create an organization in Clerk, switch to it, load it: no 404 for the cooldown."""
    h, private = signed
    h.directory.set("user_alice", [ExternalMembership("org_a", Role.ADMIN)])
    on_a = _bearer(private, "user_alice", o={"id": "org_a", "rol": "admin"})
    assert h.client.get("/api/v1/orgs/org_a/projects", headers=on_a).status_code == 200  # a full read
    h.clock.advance(seconds=5)
    h.directory.set("user_alice", [ExternalMembership("org_a", Role.ADMIN), ExternalMembership("org_b", Role.ADMIN)])

    # Another organization's miss inside the cooldown is still answered from the mirror ...
    assert h.client.get("/api/v1/orgs/org_b/projects", headers=on_a).status_code == 404
    # ... but the organization the signed session names as active is re-checked.
    on_b = _bearer(private, "user_alice", o={"id": "org_b", "rol": "admin"})
    assert h.client.get("/api/v1/orgs/org_b/projects", headers=on_b).status_code == 200
    assert h.client.get("/api/v1/orgs/org_b/entitlement", headers=on_b).status_code == 200


def test_an_active_organization_claim_never_grants_access_by_itself(signed: tuple[Harness, Any]) -> None:
    """A token naming an organization the user is not a member of (one they just left, say) is
    re-checked with Clerk at most twice per user and organization per 5-s window, across instances,
    however many requests it sends; access is never granted."""
    h, private = signed
    h.directory.set("user_mallory", [])
    claims_org = _bearer(private, "user_mallory", o={"id": "org_victim", "rol": "admin"})
    for _ in range(5):
        assert h.client.get("/api/v1/orgs/org_victim/projects", headers=claims_org).status_code == 404
    assert h.directory.calls == 2
    h.clock.advance(seconds=5)  # the next window
    for _ in range(5):
        assert h.client.get("/api/v1/orgs/org_victim/projects", headers=claims_org).status_code == 404
    assert h.directory.calls == 4
    assert _memberships(h) == 0


def test_a_failed_read_for_a_new_active_organization_is_retried_within_the_window(
    signed: tuple[Harness, Any],
) -> None:
    h, private = signed
    h.directory.set("user_alice", [ExternalMembership("org_b", Role.ADMIN)])
    on_b = _bearer(private, "user_alice", o={"id": "org_b", "rol": "admin"})
    h.directory.available = False  # a Clerk blip
    assert h.client.get("/api/v1/orgs/org_b/projects", headers=on_b).status_code == 503
    h.directory.available = True
    assert h.client.get("/api/v1/orgs/org_b/projects", headers=on_b).status_code == 200


def test_parallel_first_requests_for_a_new_active_organization_on_two_instances(make_harness: Any) -> None:
    """The workspace loads an organization's entitlement and projects together right after the switch,
    and the two requests may land on two instances: each reads Clerk, and neither gets 404."""
    a: Harness = make_harness()
    b: Harness = make_harness()  # another instance on the same database, so the same claims
    for h in (a, b):  # both instances read the same Clerk
        h.directory.set("user_alice", [ExternalMembership("org_new", Role.ADMIN)])
    alice = VerifiedIdentity("user_alice", active_org_id="org_new")
    clerk_api = BlockingDirectory(a)
    on_a: list[str] = []
    entitlement = threading.Thread(target=_resolve, args=(a, alice, "org_new", on_a))
    entitlement.start()
    try:
        assert _wait_until(lambda: clerk_api.inside == 1)  # instance A's read is in flight
        on_b: list[str] = []
        _resolve(b, alice, "org_new", on_b)
        assert on_b == ["found"]
    finally:
        clerk_api.finish.set()
        entitlement.join(10)
    assert on_a == ["found"]


def test_concurrent_requests_for_a_left_active_organization_make_one_read(harness: Harness) -> None:
    h = harness
    mallory = VerifiedIdentity("user_mallory", active_org_id="org_left")
    h.directory.set("user_mallory", [])
    clerk_api = BlockingDirectory(h)
    outcomes: list[str] = []
    threads = [threading.Thread(target=_resolve, args=(h, mallory, "org_left", outcomes)) for _ in range(3)]
    try:
        for thread in threads:
            thread.start()
        assert _wait_until(lambda: clerk_api.inside == 1)
        assert _wait_until(lambda: len(outcomes) + _busy_read_slots(h) == 3)
    finally:
        clerk_api.finish.set()
        for thread in threads:
            thread.join(10)
    assert outcomes == ["NOT_FOUND"] * 3
    assert h.directory.calls == 1


def test_parallel_first_requests_for_a_new_active_organization_share_its_read(harness: Harness) -> None:
    """The workspace loads an organization's entitlement and projects together right after the switch:
    on one instance both wait for the same read instead of one of them getting 404."""
    h = harness
    alice = VerifiedIdentity("user_alice", active_org_id="org_new")
    h.directory.set("user_alice", [ExternalMembership("org_new", Role.ADMIN)])
    clerk_api = BlockingDirectory(h)
    outcomes: list[str] = []
    threads = [threading.Thread(target=_resolve, args=(h, alice, "org_new", outcomes)) for _ in range(3)]
    try:
        for thread in threads:
            thread.start()
        assert _wait_until(lambda: _busy_read_slots(h) == 3)
    finally:
        clerk_api.finish.set()
        for thread in threads:
            thread.join(10)
    assert outcomes == ["found"] * 3
    assert h.directory.calls == 1


def test_a_new_active_organization_never_relies_on_a_read_that_began_before_it(harness: Harness) -> None:
    """A read already in flight for the user (here, re-verifying a stale membership) may have
    started before the organization the session just switched to existed in Clerk."""
    h = harness
    alice = _stale_member(h, "user_alice", "org_a")
    h.clock.advance(seconds=h.container.settings.membership_max_staleness_seconds + 1)
    started, finish = threading.Event(), threading.Event()
    original = type(h.directory).list_user_memberships

    def answered_early(self: Any, external_user_id: str) -> list[ExternalMembership]:
        seen = original(self, external_user_id)  # what Clerk listed when the read began
        if threading.current_thread().name == "stale-read":
            started.set()
            assert finish.wait(10)
        return seen

    h.directory.list_user_memberships = answered_early.__get__(h.directory)  # type: ignore[method-assign]
    stale_outcome: list[str] = []
    stale = threading.Thread(target=_resolve, args=(h, alice, "org_a", stale_outcome), name="stale-read")
    stale.start()
    try:
        assert started.wait(10)
        new = [ExternalMembership("org_a", Role.MEMBER), ExternalMembership("org_b", Role.ADMIN)]
        h.directory.set("user_alice", new)  # created in Clerk and made active
        outcome: list[str] = []
        _resolve(h, VerifiedIdentity("user_alice", active_org_id="org_b"), "org_b", outcome)
        assert outcome == ["found"]
    finally:
        finish.set()
        stale.join(10)
    assert stale_outcome == ["found"]


def test_the_active_organization_claim_is_taken_outside_the_request_transaction(signed: tuple[Harness, Any]) -> None:
    h, private = signed
    h.directory.set("user_alice", [ExternalMembership("org_a", Role.ADMIN)])
    limiter = h.container.kernel.rate_limiter
    seen: list[tuple[str, bool]] = []
    original = limiter.hit

    def spy(bucket: str, limit: int, window_seconds: int) -> None:
        seen.append((bucket.split(":")[1], h.container.kernel.database.in_request_transaction()))
        original(bucket, limit, window_seconds)

    limiter.hit = spy  # type: ignore[method-assign]
    on_a = _bearer(private, "user_alice", o={"id": "org_a", "rol": "admin"})
    assert h.client.get("/api/v1/orgs/org_a/projects", headers=on_a).status_code == 200
    assert seen == [("active-refresh", False)]


def test_a_pending_session_is_signed_out(signed: tuple[Harness, Any]) -> None:
    h, private = signed
    assert h.client.get("/api/v1/me", headers=_bearer(private, "user_p", sts="pending")).status_code == 401
    assert h.client.get("/api/v1/me", headers=_bearer(private, "user_p", sts="active")).status_code == 200


# -- Membership webhooks --------------------------------------------------------------


def test_a_membership_webhook_is_retried_after_a_directory_outage(make_harness: Any) -> None:
    """The event stays "received" until the Clerk read succeeds, so Svix's retry of the same
    message re-reads Clerk instead of being acknowledged as a duplicate: a removal still takes
    effect at once, not after the staleness bound."""
    h: Harness = make_harness(identity_webhook_verifier=ClerkWebhookVerifier(SVIX_SECRET))
    headers = h.login("user_r")
    h.directory.set("user_r", [ExternalMembership("org_retry", Role.MEMBER)])
    assert h.client.get("/api/v1/orgs/org_retry/projects", headers=headers).status_code == 200
    h.directory.set("user_r", [])  # removed in Clerk ...
    h.directory.available = False  # ... while the Backend API is down
    data = {"organization": {"id": "org_retry"}, "public_user_data": {"user_id": "user_r"}}
    assert clerk_send(h, "organizationMembership.deleted", data, msg_id="msg_retry").status_code == 503
    assert _event_status(h, "msg_retry") == "received"

    h.directory.available = True
    assert clerk_send(h, "organizationMembership.deleted", data, msg_id="msg_retry").json()["status"] == "processed"
    assert h.client.get("/api/v1/orgs/org_retry/projects", headers=headers).status_code == 404


def test_a_signing_secret_pasted_with_whitespace_still_verifies_deliveries(make_harness: Any) -> None:
    """The configured secret is trimmed before it reaches svix, which would read a leading space
    into the key and reject every delivery."""
    h: Harness = make_harness({"clerk_webhook_signing_secret": f" {SVIX_SECRET}\n"})
    h.directory.set("user_ws", [ExternalMembership("org_ws", Role.MEMBER)])
    data = {"organization": {"id": "org_ws"}, "public_user_data": {"user_id": "user_ws"}}
    assert clerk_send(h, "organizationMembership.created", data).json()["status"] == "processed"
    assert h.client.get("/api/v1/orgs/org_ws/projects", headers=h.login("user_ws")).status_code == 200


# -- user.deleted -------------------------------------------------------------------


def test_an_older_read_cannot_resurrect_a_deleted_users_memberships(make_harness: Any) -> None:
    h: Harness = make_harness(identity_webhook_verifier=ClerkWebhookVerifier(SVIX_SECRET))
    bob = h.login("user_bob")
    h.directory.set("user_bob", [ExternalMembership("org_acme", Role.ADMIN)])
    assert h.client.get("/api/v1/orgs/org_acme/projects", headers=bob).status_code == 200
    resolver = h.container.identity.resolver
    original = type(h.directory).list_user_memberships
    read, go = threading.Event(), threading.Event()

    def racing(self: Any, external_user_id: str) -> list[ExternalMembership]:
        seen = original(self, external_user_id)
        if threading.current_thread().name == "slow-refresh":
            read.set()
            assert go.wait(10)
        return seen

    h.directory.list_user_memberships = racing.__get__(h.directory)  # type: ignore[method-assign]
    bob_id = _user_id(h, "user_bob")
    slow = threading.Thread(target=lambda: resolver.refresh_memberships(bob_id, "user_bob"), name="slow-refresh")
    slow.start()
    assert read.wait(10)  # Clerk answered before the deletion

    h.clock.advance(seconds=1)
    h.directory.set("user_bob", [])  # deleted in Clerk
    assert clerk_send(h, "user.deleted", {"id": "user_bob", "deleted": True}).json()["status"] == "processed"
    assert _memberships(h) == 0
    go.set()
    slow.join(10)
    assert _memberships(h) == 0
    assert h.client.get("/api/v1/orgs/org_acme/projects", headers=bob).status_code == 404


# -- Reconciliation -----------------------------------------------------------------


def test_one_users_failure_does_not_stop_reconciliation(harness: Harness) -> None:
    h = harness
    for user in ("user_ok", "user_bad"):
        h.directory.set(user, [ExternalMembership("org_r", Role.MEMBER)])
        h.container.identity.resolver.organization(VerifiedIdentity(user), "org_r")
    h.clock.advance(seconds=h.container.settings.membership_max_staleness_seconds + 1)
    original = type(h.directory).list_user_memberships

    def flaky(self: Any, external_user_id: str) -> list[ExternalMembership]:
        if external_user_id == "user_bad":
            raise RuntimeError("an unexpected answer")
        return original(self, external_user_id)

    h.directory.list_user_memberships = flaky.__get__(h.directory)  # type: ignore[method-assign]
    assert h.container.identity.reconciler.run() == {"checked": 2, "refreshed": 1, "failed": 1}


def test_reconciliation_stops_starting_reads_when_its_time_budget_is_spent(harness: Harness) -> None:
    """Like the other jobs, a run stays well inside the platform's function time limit; the next run continues."""
    h = harness
    for user in ("user_1", "user_2", "user_3"):
        _stale_member(h, user, "org_r")
    h.clock.advance(seconds=h.container.settings.membership_max_staleness_seconds + 1)
    reconciler = h.container.identity.reconciler
    elapsed = [0.0]
    reconciler._monotonic = lambda: elapsed[0]
    original = type(h.directory).list_user_memberships

    def slow(self: Any, external_user_id: str) -> list[ExternalMembership]:
        elapsed[0] += 40  # a degraded Clerk
        return original(self, external_user_id)

    h.directory.list_user_memberships = slow.__get__(h.directory)  # type: ignore[method-assign]
    # Reads start at 0 s and 40 s; at 80 s the 60-s budget is spent.
    assert reconciler.run() == {"checked": 2, "refreshed": 2, "failed": 0}
    assert reconciler.run() == {"checked": 1, "refreshed": 1, "failed": 0}  # the user left over


class ClerkBackend:
    """Clerk's membership endpoint in process, behind the production adapter (with its outage breaker)."""

    def __init__(self) -> None:
        self.memberships: dict[str, list[str]] = {}
        self.failing: set[str] = set()  # users whose reads always answer 500
        self.too_many: set[str] = set()  # users with more memberships than one read may return
        self.time_out_next = False
        self.requested: list[str] = []
        self.monotonic = 1_000.0  # the adapter's breaker clock

    def handle(self, request: httpx.Request) -> httpx.Response:
        user = request.url.path.split("/")[3]  # /v1/users/<user>/organization_memberships
        self.requested.append(user)
        if self.time_out_next:
            self.time_out_next = False
            raise httpx.ConnectTimeout("timed out")
        if user in self.failing:
            return httpx.Response(500)
        orgs = self.memberships.get(user, [])
        if user in self.too_many:
            offset, limit = int(request.url.params["offset"]), int(request.url.params["limit"])
            orgs = [f"org_{n}" for n in range(offset, offset + limit)]  # every page is full
        data = [{"role": "org:member", "organization": {"id": org}} for org in orgs]
        return httpx.Response(200, json={"data": data})

    def harness(self, make_harness: Callable[..., Harness]) -> Harness:
        directory = ClerkMembershipDirectory(
            secret_key="sk_test_only_x",
            api_url="https://api.clerk.invalid",
            clock=lambda: self.monotonic,
            transport=httpx.MockTransport(self.handle),
        )
        return make_harness(directory=directory)

    def mirror(self, h: Harness, external_user_id: str) -> None:
        self.memberships[external_user_id] = ["org_r"]
        h.container.identity.resolver.organization(VerifiedIdentity(external_user_id), "org_r")


def test_reconciliation_stops_at_a_directory_outage_and_resumes_after_it(make_harness: Any) -> None:
    """After a failed read the adapter fails fast for a few seconds, so the rest of the run
    would only record failures: the run stops, and the next one re-verifies everyone."""
    clerk_api = ClerkBackend()
    h = clerk_api.harness(make_harness)
    users = ["user_a", "user_b", "user_c", "user_d"]
    for user in users:
        clerk_api.mirror(h, user)
    h.clock.advance(seconds=h.container.settings.membership_max_staleness_seconds + 1)
    reconciler = h.container.identity.reconciler

    clerk_api.time_out_next = True
    assert reconciler.run() == {"checked": 1, "refreshed": 0, "failed": 1}
    assert len(clerk_api.requested) == len(users) + 1
    clerk_api.monotonic += 5  # the adapter's cooldown is over
    assert reconciler.run() == {"checked": 4, "refreshed": 4, "failed": 0}


def _verified_now(h: Harness) -> list[tuple[Any, ...]]:
    """The users whose memberships were read at the harness clock's current time."""
    return superuser_query(
        h.database_url,
        "SELECT clerk_user_id FROM app.users WHERE memberships_verified_at = %s ORDER BY clerk_user_id",
        (h.clock.now(),),
    )


def test_reconciliation_takes_the_least_recently_tried_or_read_users_first(make_harness: Any) -> None:
    """The job is for mirrors nobody keeps fresh: a user never tried by the job (a new organization
    member, say) does not jump ahead of one whose mirror was read or tried longer ago."""
    h: Harness = make_harness({"membership_reconcile_batch_size": 1})
    _stale_member(h, "user_old", "org_r")
    h.clock.advance(days=2)
    _stale_member(h, "user_new", "org_r")  # read once, never tried by the job
    superuser_query(  # the job tried user_old a day after its read
        h.database_url,
        "UPDATE app.users SET memberships_checked_at = %s WHERE clerk_user_id = 'user_old'",
        (h.clock.now() - timedelta(days=1),),
    )
    h.clock.advance(days=1)
    assert h.container.identity.reconciler.run() == {"checked": 1, "refreshed": 1, "failed": 0}
    assert _verified_now(h) == [("user_old",)]


def test_a_user_whose_read_keeps_failing_does_not_starve_the_others(make_harness: Any) -> None:
    """Every attempt is recorded, so the user with the oldest mirror, whose read always fails (and stops
    the run, as the adapter then fails fast), moves behind the others instead of heading every run."""
    clerk_api = ClerkBackend()
    h = clerk_api.harness(make_harness)
    clerk_api.mirror(h, "user_bad")
    h.clock.advance(seconds=1)
    for user in ("user_a", "user_b", "user_c"):
        clerk_api.mirror(h, user)
    h.clock.advance(seconds=h.container.settings.membership_max_staleness_seconds + 1)
    clerk_api.failing.add("user_bad")
    reconciler = h.container.identity.reconciler

    assert reconciler.run() == {"checked": 1, "refreshed": 0, "failed": 1}  # the oldest mirror first
    clerk_api.monotonic += 5  # the adapter's cooldown is over
    assert reconciler.run() == {"checked": 4, "refreshed": 3, "failed": 1}  # the others first this time
    assert _verified_now(h) == [("user_a",), ("user_b",), ("user_c",)]


def test_a_user_with_too_many_memberships_does_not_stop_reconciliation(make_harness: Any) -> None:
    """That read fails for one user and applies nothing; Clerk itself is fine, so the run goes on."""
    clerk_api = ClerkBackend()
    h = clerk_api.harness(make_harness)
    clerk_api.mirror(h, "user_big")  # the oldest mirror, so user_big comes first
    h.clock.advance(seconds=1)
    for user in ("user_a", "user_b"):
        clerk_api.mirror(h, user)
    h.clock.advance(seconds=h.container.settings.membership_max_staleness_seconds + 1)
    clerk_api.too_many.add("user_big")
    before = len(clerk_api.requested)
    assert h.container.identity.reconciler.run() == {"checked": 3, "refreshed": 2, "failed": 1}
    pages = clerk._MEMBERSHIP_MAX_PAGES
    assert clerk_api.requested[before : before + pages] == ["user_big"] * pages  # read first, then the others
    kept = superuser_query(
        h.database_url,
        "SELECT o.clerk_org_id FROM app.organization_memberships m JOIN app.users u ON u.id = m.user_id "
        "JOIN app.organizations o ON o.id = m.org_id WHERE u.clerk_user_id = 'user_big'",
    )
    assert kept == [("org_r",)]  # the partial list was not applied


def test_a_list_too_large_to_read_is_not_read_again_at_once(make_harness: Any) -> None:
    """Such a read costs ten Clerk calls, and such a list rarely shrinks: until a cooldown ends, the
    user's requests on the instance fail closed (503) without reading Clerk. Other users are unaffected."""
    clerk_api = ClerkBackend()
    h = clerk_api.harness(make_harness)
    for user in ("user_big", "user_a"):
        clerk_api.mirror(h, user)
    h.clock.advance(seconds=h.container.settings.membership_max_staleness_seconds + 1)
    clerk_api.too_many.add("user_big")
    big, other = h.login("user_big"), h.login("user_a")
    pages = clerk._MEMBERSHIP_MAX_PAGES

    for _ in range(3):
        assert h.client.get("/api/v1/orgs/org_r/projects", headers=big).status_code == 503
    assert clerk_api.requested.count("user_big") == 1 + pages  # its first read, then one refused read
    assert h.client.get("/api/v1/orgs/org_r/projects", headers=other).status_code == 200

    h.clock.advance(seconds=identity_service._TOO_LARGE_RETRY_AFTER.total_seconds())
    assert h.client.get("/api/v1/orgs/org_r/projects", headers=big).status_code == 503
    assert clerk_api.requested.count("user_big") == 1 + 2 * pages


def test_users_with_lists_too_large_to_read_are_remembered_within_a_bound(harness: Harness) -> None:
    resolver = harness.container.identity.resolver
    bound = identity_service._TOO_LARGE_REMEMBERED
    cooldown = identity_service._TOO_LARGE_RETRY_AFTER
    for _ in range(bound + 10):
        resolver._remember_too_large(uuid.uuid4(), harness.clock.now() + cooldown)
    assert len(resolver._too_large_until) == bound
    harness.clock.advance(seconds=cooldown.total_seconds())  # every cooldown is over
    resolver._remember_too_large(uuid.uuid4(), harness.clock.now() + cooldown)
    assert len(resolver._too_large_until) == 1


# -- Lock ordering --------------------------------------------------------------------


def test_a_refresh_does_not_deadlock_with_a_privileged_write(harness: Harness) -> None:
    """NT-06 with the real refresh path. The privileged write holds its membership
    lock and then writes an audit row that references the user; the refresh
    holds the user row and waits for that membership. Both must commit."""
    h = harness
    h.directory.set("user_alice", [ExternalMembership("org_acme", Role.ADMIN)])
    ctx = h.container.identity.resolver.organization(VerifiedIdentity("user_alice"), "org_acme")
    assert ctx.org_id is not None
    h.clock.advance(seconds=1)
    h.directory.set("user_alice", [ExternalMembership("org_acme", Role.MEMBER)])  # downgraded in Clerk
    outcome: dict[str, str] = {}

    def refresh() -> None:
        try:
            h.container.identity.resolver.refresh_memberships(ctx.user_id, "user_alice")
            outcome["refresh"] = "ok"
        except DomainError as exc:
            outcome["refresh"] = repr(exc.__cause__ or exc)

    with h.container.kernel.store.as_tenant(ctx) as t:
        assert t.lock_my_role(ctx.org_id) is Role.ADMIN
        worker = threading.Thread(target=refresh)
        worker.start()
        wait_for_lock_wait(h.database_url)  # the refresh now holds the user row and waits for the membership
        t.audit(AuditEntry(ctx.user_id, ctx.org_id, "project:delete", "project", str(uuid.uuid4()), "succeeded", None))
    outcome["write"] = "committed"
    worker.join(10)
    assert outcome == {"write": "committed", "refresh": "ok"}
    assert superuser_query(h.database_url, "SELECT role FROM app.organization_memberships") == [("member",)]


def test_refreshes_creating_the_same_organizations_do_not_deadlock(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = harness
    resolver = h.container.identity.resolver
    one = resolver.personal(VerifiedIdentity("user_1")).user_id
    two = resolver.personal(VerifiedIdentity("user_2")).user_id
    # Clerk lists the same two new organizations in opposite orders.
    h.directory.set("user_1", [ExternalMembership("org_a", Role.MEMBER), ExternalMembership("org_b", Role.MEMBER)])
    h.directory.set("user_2", [ExternalMembership("org_b", Role.MEMBER), ExternalMembership("org_a", Role.MEMBER)])
    original = repository.PgIdentityRepository._exec
    inserted = {"user_1": threading.Event(), "user_2": threading.Event()}

    def exec_then_hold(self: Any, query: str, params: Any = ()) -> int:
        count = original(self, query, params)
        me = threading.current_thread().name
        if "INSERT INTO app.organizations" in query and me in inserted and not inserted[me].is_set():
            inserted[me].set()
            other = inserted["user_2" if me == "user_1" else "user_1"]
            # Keep the first new organization uncommitted until the other refresh has one too, or waits on it.
            wait_for_lock_wait(h.database_url, or_until=other)
        return count

    monkeypatch.setattr(repository.PgIdentityRepository, "_exec", exec_then_hold)
    outcome: dict[str, str] = {}

    def run(external_user_id: str, user_id: Any) -> None:
        try:
            resolver.refresh_memberships(user_id, external_user_id)
            outcome[external_user_id] = "ok"
        except (DomainError, pytest.fail.Exception) as exc:  # a deadlock, or a wait that timed out
            outcome[external_user_id] = repr(exc.__cause__ or exc)

    threads = [
        threading.Thread(target=run, args=(name, user), name=name) for name, user in (("user_1", one), ("user_2", two))
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert outcome == {"user_1": "ok", "user_2": "ok"}
    # Both refreshes held their first insert, so the race above was exercised.
    assert {name: event.is_set() for name, event in inserted.items()} == {"user_1": True, "user_2": True}
    assert _memberships(h) == 4


# -- Authentication before admission ------------------------------------------------------


class CountingVerifier(TokenTableVerifier):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def verify(self, token: str) -> VerifiedIdentity:
        self.calls += 1
        return super().verify(token)


@pytest.mark.parametrize("path", ["/api/v1/me", "/api/v1/projects", "/api/v1/orgs/org_c/projects"])
def test_a_token_is_verified_once_per_request(make_harness: Any, path: str) -> None:
    verifier = CountingVerifier()
    h = make_harness(verifier=verifier)
    h.directory.set("user_c", [ExternalMembership("org_c", Role.MEMBER)])
    headers = {"Authorization": f"Bearer {verifier.issue('user_c')}"}
    assert h.client.get(path, headers=headers).status_code == 200
    assert verifier.calls == 1


@pytest.mark.parametrize("path", ["/api/v1/projects", "/api/v1/me", "/api/v1/billing/status"])
def test_a_bad_token_never_waits_for_an_admission_place(make_harness: Any, path: str) -> None:
    """A request with an invalid token is rejected before it takes a place in
    the request-transaction admission gate, so it cannot queue behind (or hold
    up) authenticated work. One path per request-scoped router."""
    from fastapi.testclient import TestClient

    from app.bootstrap import create_app

    h = make_harness({"database_pool_max_size": 1})
    member = h.login("user_holder")
    holding = threading.Event()
    finish = threading.Event()
    service = h.container.projects.service
    original = service.list

    def blocking_list(*args: Any) -> Any:
        holding.set()
        assert finish.wait(10)  # holds the only admission place
        return original(*args)

    service.list = blocking_list
    # The gate belongs to the event loop. The harness client runs every request on
    # a loop of its own, where nothing ever contends for a place; production runs one.
    with TestClient(create_app(h.container), raise_server_exceptions=False) as client:
        holder = threading.Thread(target=lambda: client.get("/api/v1/projects", headers=member))
        holder.start()
        try:
            assert holding.wait(10)
            codes: list[int] = []
            bad = threading.Thread(
                target=lambda: codes.append(client.get(path, headers={"Authorization": "Bearer forged"}).status_code),
                daemon=True,
            )
            bad.start()
            bad.join(5)  # the holder keeps the only place until ``finish`` is set
            assert codes == [401]
        finally:
            finish.set()
            holder.join(10)
