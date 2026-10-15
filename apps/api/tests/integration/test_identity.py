"""Identity: membership refresh concurrency, the session's active organization,
user deletion, reconciliation, and authentication before admission.

Through the real database and HTTP stack; Clerk is the in-memory directory
(and, where the token claims matter, the production verifier with a local key).
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.kernel.errors import DomainError, NotFoundError
from app.kernel.ports import AuditEntry
from app.kernel.tenancy import Role, VerifiedIdentity
from app.modules.identity.adapters import repository
from app.modules.identity.adapters.clerk import ClerkTokenVerifier, ClerkWebhookVerifier
from app.modules.identity.domain import ExternalMembership
from tests.conftest import Harness, TokenTableVerifier, superuser_query
from tests.integration.test_platform import SVIX_SECRET, clerk_send

pytestmark = pytest.mark.db

ISSUER = "https://issuer.example"
ORIGIN = "http://localhost:3000"


def _user_id(h: Harness, external_user_id: str) -> Any:
    return superuser_query(h.database_url, "SELECT id FROM app.users WHERE clerk_user_id=%s", (external_user_id,))[0][0]


def _memberships(h: Harness) -> int:
    return int(superuser_query(h.database_url, "SELECT count(*) FROM app.organization_memberships")[0][0])


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


def test_a_miss_refresh_releases_the_request_transaction(harness: Harness) -> None:
    h = harness
    headers = h.login("user_new")
    h.directory.set("user_new", [ExternalMembership("org_q", Role.MEMBER)])
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
    h, private = signed
    h.directory.set("user_mallory", [])
    claims_org = _bearer(private, "user_mallory", o={"id": "org_victim", "rol": "admin"})
    for _ in range(2):
        assert h.client.get("/api/v1/orgs/org_victim/projects", headers=claims_org).status_code == 404
    assert h.directory.calls == 2  # re-checked each time, granted never
    assert _memberships(h) == 0


def test_a_pending_session_is_signed_out(signed: tuple[Harness, Any]) -> None:
    h, private = signed
    assert h.client.get("/api/v1/me", headers=_bearer(private, "user_p", sts="pending")).status_code == 401
    assert h.client.get("/api/v1/me", headers=_bearer(private, "user_p", sts="active")).status_code == 200


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
        time.sleep(0.3)  # the refresh now holds the user row and waits for the membership
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

    def slow_exec(self: Any, query: str, params: Any = ()) -> int:
        count = original(self, query, params)
        if "INSERT INTO app.organizations" in query:
            time.sleep(0.3)  # widen the window between the two organization inserts
        return count

    monkeypatch.setattr(repository.PgIdentityRepository, "_exec", slow_exec)
    outcome: dict[str, str] = {}

    def run(external_user_id: str, user_id: Any) -> None:
        try:
            resolver.refresh_memberships(user_id, external_user_id)
            outcome[external_user_id] = "ok"
        except DomainError as exc:
            outcome[external_user_id] = repr(exc.__cause__ or exc)

    threads = [threading.Thread(target=run, args=args) for args in (("user_1", one), ("user_2", two))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert outcome == {"user_1": "ok", "user_2": "ok"}
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


def test_a_bad_token_never_waits_for_an_admission_place(make_harness: Any) -> None:
    """A request with an invalid token is rejected before it takes a place in
    the request-transaction admission gate, so it cannot queue behind (or hold
    up) authenticated work."""
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
    holder = threading.Thread(target=lambda: h.client.get("/api/v1/projects", headers=member))
    holder.start()
    try:
        assert holding.wait(10)
        codes: list[int] = []
        bad = threading.Thread(
            target=lambda: codes.append(
                h.client.get("/api/v1/projects", headers={"Authorization": "Bearer forged"}).status_code
            ),
            daemon=True,
        )
        bad.start()
        bad.join(1.5)  # well before the holder finishes
        assert codes == [401]
    finally:
        finish.set()
        holder.join(10)
