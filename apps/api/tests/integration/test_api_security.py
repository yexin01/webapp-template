"""Negative security tests through the HTTP API (spec §7.4: NT-01 to NT-12).

Identity is injected with a token-table verifier (test code only); real token
verification is covered in tests/unit/test_clerk_verifier.py and in
test_real_verifier_end_to_end below.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.kernel.tenancy import Role
from app.modules.identity.adapters.clerk import ClerkTokenVerifier
from app.modules.identity.domain import ExternalMembership
from tests.conftest import Harness, superuser_query

pytestmark = pytest.mark.db

ACME = "org_acme"
GLOBEX = "org_globex"


@pytest.fixture
def world(harness: Harness) -> dict[str, Any]:
    h = harness
    h.directory.set("user_alice", [ExternalMembership(ACME, Role.ADMIN)])
    h.directory.set("user_bob", [ExternalMembership(ACME, Role.MEMBER), ExternalMembership(GLOBEX, Role.ADMIN)])
    h.directory.set("user_carol", [])
    alice, bob, carol = h.login("user_alice"), h.login("user_bob"), h.login("user_carol")

    alice_project = h.client.post("/api/v1/projects", json={"name": "alice private"}, headers=alice).json()
    acme_project = h.client.post(f"/api/v1/orgs/{ACME}/projects", json={"name": "acme shared"}, headers=alice).json()
    globex_project = h.client.post(f"/api/v1/orgs/{GLOBEX}/projects", json={"name": "globex"}, headers=bob).json()
    return {
        "alice": alice,
        "bob": bob,
        "carol": carol,
        "alice_project": alice_project["id"],
        "acme_project": acme_project["id"],
        "globex_project": globex_project["id"],
    }


def test_nt01_user_cannot_read_another_users_resources(harness: Harness, world: dict[str, Any]) -> None:
    listing = harness.client.get("/api/v1/projects", headers=world["bob"]).json()
    assert world["alice_project"] not in [p["id"] for p in listing["items"]]


def test_nt02_user_cannot_delete_another_users_resource(harness: Harness, world: dict[str, Any]) -> None:
    r = harness.client.delete(f"/api/v1/projects/{world['alice_project']}", headers=world["bob"])
    assert r.status_code == 404
    still = harness.client.get("/api/v1/projects", headers=world["alice"]).json()
    assert world["alice_project"] in [p["id"] for p in still["items"]]


def test_nt03_non_member_cannot_access_org(harness: Harness, world: dict[str, Any]) -> None:
    for path in (
        f"/api/v1/orgs/{ACME}/projects",
        f"/api/v1/orgs/{ACME}/projects?limit=100",
        f"/api/v1/orgs/{ACME}/entitlement",
    ):
        r = harness.client.get(path, headers=world["carol"])
        assert r.status_code == 404, path
        assert r.json()["code"] == "NOT_FOUND"
    r = harness.client.post(f"/api/v1/orgs/{ACME}/projects", json={"name": "intrusion"}, headers=world["carol"])
    assert r.status_code == 404


def test_nt04_member_of_one_org_cannot_use_another_orgs_id(harness: Harness, world: dict[str, Any]) -> None:
    r = harness.client.get(f"/api/v1/orgs/{GLOBEX}/projects", headers=world["alice"])
    assert r.status_code == 404
    # Deleting a Globex project through Acme's path must not work either.
    r = harness.client.delete(f"/api/v1/orgs/{ACME}/projects/{world['globex_project']}", headers=world["alice"])
    assert r.status_code == 404
    assert harness.client.get(f"/api/v1/orgs/{GLOBEX}/projects", headers=world["bob"]).json()["items"]


def test_nt04_personal_endpoints_never_return_org_rows(harness: Harness, world: dict[str, Any]) -> None:
    ids = [p["id"] for p in harness.client.get("/api/v1/projects", headers=world["alice"]).json()["items"]]
    assert ids == [world["alice_project"]]


def test_nt05_removed_member_loses_access_after_staleness_bound(harness: Harness, world: dict[str, Any]) -> None:
    path = f"/api/v1/orgs/{ACME}/projects"
    assert harness.client.get(path, headers=world["bob"]).status_code == 200
    harness.directory.set("user_bob", [ExternalMembership(GLOBEX, Role.ADMIN)])  # removed from Acme in Clerk
    # Within the bound the mirror is trusted; after it, the mirror is re-verified.
    harness.clock.advance(seconds=harness.container.settings.membership_max_staleness_seconds + 1)
    assert harness.client.get(path, headers=world["bob"]).status_code == 404


def test_nt05_removed_member_loses_access_immediately_via_webhook_sync(harness: Harness, world: dict[str, Any]) -> None:
    harness.directory.set("user_bob", [ExternalMembership(GLOBEX, Role.ADMIN)])
    harness.container.identity.resolver.refresh_memberships(
        superuser_query(harness.database_url, "SELECT id FROM app.users WHERE clerk_user_id='user_bob'")[0][0],
        "user_bob",
    )
    assert harness.client.get(f"/api/v1/orgs/{ACME}/projects", headers=world["bob"]).status_code == 404


def test_nt05_stale_membership_fails_closed_when_directory_down(harness: Harness, world: dict[str, Any]) -> None:
    harness.directory.available = False
    harness.clock.advance(seconds=harness.container.settings.membership_max_staleness_seconds + 1)
    r = harness.client.get(f"/api/v1/orgs/{ACME}/projects", headers=world["bob"])
    assert r.status_code == 503
    assert r.json()["code"] == "DEPENDENCY_UNAVAILABLE"


def test_nt05_an_older_directory_read_cannot_resurrect_a_removed_membership(
    harness: Harness, world: dict[str, Any]
) -> None:
    """A slow refresh that read Clerk before a removal, but applies after the
    refresh that saw the removal, changes nothing (TEN-07, DEC-009)."""
    resolver = harness.container.identity.resolver
    bob = superuser_query(harness.database_url, "SELECT id FROM app.users WHERE clerk_user_id='user_bob'")[0][0]
    directory = harness.directory
    original = type(directory).list_user_memberships
    slow_read = threading.Event()
    release_slow = threading.Event()

    def racing(self: Any, external_user_id: str) -> list[ExternalMembership]:
        seen = original(self, external_user_id)
        if threading.current_thread().name == "slow-refresh":
            slow_read.set()
            assert release_slow.wait(10)
        return seen

    directory.list_user_memberships = racing.__get__(directory)  # type: ignore[method-assign]
    slow = threading.Thread(target=lambda: resolver.refresh_memberships(bob, "user_bob"), name="slow-refresh")
    slow.start()  # reads Bob as a member of Acme, then stalls
    assert slow_read.wait(10)
    harness.clock.advance(seconds=1)
    directory.set("user_bob", [ExternalMembership(GLOBEX, Role.ADMIN)])  # removed from Acme
    resolver.refresh_memberships(bob, "user_bob")  # the newer read applies first
    release_slow.set()
    slow.join(10)
    assert harness.client.get(f"/api/v1/orgs/{ACME}/projects", headers=world["bob"]).status_code == 404


def test_probing_unknown_organizations_does_not_call_the_directory_each_time(
    harness: Harness, world: dict[str, Any]
) -> None:
    carol = world["carol"]
    assert harness.client.get("/api/v1/orgs/org_probe1/projects", headers=carol).status_code == 404
    calls = harness.directory.calls
    for n in range(2, 12):
        assert harness.client.get(f"/api/v1/orgs/org_probe{n}/projects", headers=carol).status_code == 404
    assert harness.directory.calls == calls  # answered from the fresh mirror
    # After the cooldown, a miss is re-checked, so a new membership is found.
    harness.directory.set("user_carol", [ExternalMembership("org_new", Role.MEMBER)])
    harness.clock.advance(seconds=31)
    assert harness.client.get("/api/v1/orgs/org_new/projects", headers=carol).status_code == 200


def _ids(harness: Harness) -> tuple[Any, Any]:
    org_uuid = superuser_query(harness.database_url, "SELECT id FROM app.organizations WHERE clerk_org_id=%s", (ACME,))[
        0
    ][0]
    alice_uuid = superuser_query(harness.database_url, "SELECT id FROM app.users WHERE clerk_user_id='user_alice'")[0][
        0
    ]
    return org_uuid, alice_uuid


def test_nt06_write_rechecks_role_after_context_was_resolved(harness: Harness, world: dict[str, Any]) -> None:
    """Context resolved as admin, then downgraded before the write: the write is denied."""
    from app.kernel.errors import ForbiddenError
    from app.kernel.tenancy import VerifiedIdentity

    ctx = harness.container.identity.resolver.organization(VerifiedIdentity("user_alice"), ACME)
    assert ctx.role is Role.ADMIN
    org_uuid, alice_uuid = _ids(harness)
    superuser_query(
        harness.database_url,
        "UPDATE app.organization_memberships SET role='member' WHERE org_id=%s AND user_id=%s",
        (org_uuid, alice_uuid),
    )
    import uuid as _uuid

    with pytest.raises(ForbiddenError):
        harness.container.projects.service.delete(ctx, _uuid.UUID(world["acme_project"]), None)
    count = superuser_query(
        harness.database_url, "SELECT count(*) FROM app.projects WHERE id=%s", (world["acme_project"],)
    )
    assert count[0][0] == 1


def test_nt06_concurrent_downgrade_waits_for_privileged_transaction(harness: Harness, world: dict[str, Any]) -> None:
    """While a privileged write holds the membership lock, a downgrade cannot commit."""
    from app.kernel.tenancy import VerifiedIdentity

    ctx = harness.container.identity.resolver.organization(VerifiedIdentity("user_alice"), ACME)
    org_uuid, alice_uuid = _ids(harness)
    downgrade_committed = threading.Event()

    def downgrade() -> None:
        with harness.container.kernel.database.transaction("app_worker", {}) as conn:
            conn.execute(
                "UPDATE app.organization_memberships SET role='member' WHERE org_id=%s AND user_id=%s",
                (org_uuid, alice_uuid),
            )
        downgrade_committed.set()

    with harness.container.kernel.store.as_tenant(ctx) as t:
        assert t.lock_my_role(org_uuid) is Role.ADMIN
        worker = threading.Thread(target=downgrade)
        worker.start()
        # The downgrade blocks on the row lock held by this transaction.
        assert not downgrade_committed.wait(0.5)
    worker.join(5)
    assert downgrade_committed.is_set()


def test_nt07_member_cannot_perform_admin_action_and_denial_is_audited(harness: Harness, world: dict[str, Any]) -> None:
    r = harness.client.delete(f"/api/v1/orgs/{ACME}/projects/{world['acme_project']}", headers=world["bob"])
    assert r.status_code == 403
    audit = superuser_query(harness.database_url, "SELECT action, outcome FROM app.audit_log")
    assert ("project:delete", "denied") in audit


def test_admin_delete_succeeds_and_is_audited(harness: Harness, world: dict[str, Any]) -> None:
    r = harness.client.delete(f"/api/v1/orgs/{ACME}/projects/{world['acme_project']}", headers=world["alice"])
    assert r.status_code == 204
    assert ("project:delete", "succeeded") in superuser_query(
        harness.database_url, "SELECT action, outcome FROM app.audit_log"
    )


def test_nt09_client_supplied_authority_fields_are_rejected(harness: Harness, world: dict[str, Any]) -> None:
    for body in ({"name": "x", "org_id": GLOBEX}, {"name": "x", "role": "admin"}, {"name": "x", "plan": "pro"}):
        r = harness.client.post("/api/v1/projects", json=body, headers=world["carol"])
        assert r.status_code == 422, body
        assert r.json()["code"] == "VALIDATION_FAILED"
        # Submitted values are never echoed back.
        assert GLOBEX not in r.text
        assert "admin" not in r.text
    # Headers claiming identity or plan are ignored.
    r = harness.client.get(
        "/api/v1/entitlement", headers={**world["carol"], "X-User-Id": "user_alice", "X-Plan": "pro"}
    )
    assert r.json() == {"plan": "free", "context": "personal"}


def test_nt10_redirect_and_client_claims_never_grant_pro(harness: Harness, world: dict[str, Any]) -> None:
    r = harness.client.get("/api/v1/pro/insight?session_id=cs_forged&status=active", headers=world["carol"])
    assert r.status_code == 403
    assert r.json()["code"] == "ENTITLEMENT_REQUIRED"


def test_nt11_missing_and_unknown_tokens_rejected(harness: Harness) -> None:
    for headers in ({}, {"Authorization": "Bearer nope"}, {"Authorization": "Basic abc"}, {"Cookie": "__session=abc"}):
        r = harness.client.get("/api/v1/me", headers=headers)
        assert r.status_code == 401
        assert r.json()["code"] == "UNAUTHENTICATED"
        assert r.headers["www-authenticate"] == "Bearer"


def test_nt11_revoked_token_rejected(harness: Harness) -> None:
    headers = harness.login("user_dave")
    assert harness.client.get("/api/v1/me", headers=headers).status_code == 200
    harness.verifier.revoke(headers["Authorization"].split()[1])
    assert harness.client.get("/api/v1/me", headers=headers).status_code == 401


def test_nt12_subscribers_pro_is_not_shared_with_org_members(harness: Harness, world: dict[str, Any]) -> None:
    """DEC-002 (b): Alice's Pro covers her own actions in Acme, never Bob's (full table: test_org_entitlements)."""
    from tests.integration.test_billing import make_pro

    make_pro(harness, "user_alice")
    assert harness.client.get(f"/api/v1/orgs/{ACME}/pro/insight", headers=world["alice"]).status_code == 200
    r = harness.client.get(f"/api/v1/orgs/{ACME}/pro/insight", headers=world["bob"])
    assert r.status_code == 403
    assert r.json()["code"] == "ENTITLEMENT_REQUIRED"
    assert harness.client.get(f"/api/v1/orgs/{ACME}/entitlement", headers=world["bob"]).json()["plan"] == "free"


def test_real_verifier_end_to_end(make_harness: Any) -> None:
    """The production verifier, wired into the app, accepts only valid tokens."""
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = (
        private.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    verifier = ClerkTokenVerifier(
        issuer="https://issuer.example", authorized_parties=["http://localhost:3000"], jwt_public_key=pem
    )
    h = make_harness(verifier=verifier)
    now = int(time.time())
    good = jwt.encode(
        {
            "sub": "user_real",
            "iss": "https://issuer.example",
            "azp": "http://localhost:3000",
            "iat": now,
            "exp": now + 60,
        },
        private,
        algorithm="RS256",
    )
    expired = jwt.encode(
        {
            "sub": "user_real",
            "iss": "https://issuer.example",
            "azp": "http://localhost:3000",
            "iat": now - 600,
            "exp": now - 300,
        },
        private,
        algorithm="RS256",
    )
    assert h.client.get("/api/v1/me", headers={"Authorization": f"Bearer {good}"}).status_code == 200
    assert h.client.get("/api/v1/me", headers={"Authorization": f"Bearer {expired}"}).status_code == 401
