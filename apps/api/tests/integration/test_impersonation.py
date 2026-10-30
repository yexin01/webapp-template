"""Sessions someone else started (Clerk impersonation, actor tokens) are refused unless enabled, and audited when allowed.

ADM-01 puts the actor in each administrative audit record, and ADM-02 requires approval for any
operator or support capability. Dashboard impersonation is one: an application that has not
decided to accept it refuses such sessions, and one that has records who acted as the user.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from app.kernel.tenancy import Role
from app.modules.identity.domain import ExternalMembership
from tests.conftest import Harness, make_settings, superuser_query

pytestmark = pytest.mark.db

ACME = "org_acme"
PROJECTS = f"/api/v1/orgs/{ACME}/projects"
STAFF = "user_clerk_staff"


def test_the_setting_is_off_by_default_and_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    assert make_settings().clerk_allow_impersonation is False
    assert make_settings(clerk_allow_impersonation=True).clerk_allow_impersonation is True
    monkeypatch.setenv("CLERK_ALLOW_IMPERSONATION", "true")
    assert make_settings().clerk_allow_impersonation is True


@pytest.fixture
def acme(harness: Harness) -> Harness:
    harness.directory.set("user_alice", [ExternalMembership(ACME, Role.ADMIN)])
    return harness


def audit(harness: Harness) -> list[tuple[Any, ...]]:
    return superuser_query(
        harness.database_url, "SELECT action, outcome, impersonator_clerk_id FROM app.audit_log ORDER BY id"
    )


@pytest.mark.parametrize("path", ["/api/v1/me", "/api/v1/projects", PROJECTS, "/api/v1/entitlement"])
def test_a_session_someone_else_started_is_refused_by_default(
    acme: Harness, path: str, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        refused = acme.client.get(path, headers=acme.login("user_alice", actor=STAFF))
    assert refused.status_code == 403
    assert refused.json()["code"] == "FORBIDDEN"
    assert STAFF not in refused.text
    assert "actor_session_refused" in [r.getMessage() for r in caplog.records]
    assert acme.client.get(path, headers=acme.login("user_alice")).status_code == 200


def test_a_refused_session_leaves_no_trace_in_the_database(acme: Harness) -> None:
    refused = acme.client.post(PROJECTS, json={"name": "as alice"}, headers=acme.login("user_alice", actor=STAFF))
    assert refused.status_code == 403
    assert superuser_query(acme.database_url, "SELECT count(*) FROM app.projects")[0][0] == 0
    assert superuser_query(acme.database_url, "SELECT count(*) FROM app.users")[0][0] == 0


def test_an_allowed_session_names_the_actor_in_the_audit_log_and_the_logs(
    make_harness: Any, caplog: pytest.LogCaptureFixture
) -> None:
    h: Harness = make_harness({"clerk_allow_impersonation": True})
    h.directory.set("user_alice", [ExternalMembership(ACME, Role.ADMIN)])
    alice, impersonated = h.login("user_alice"), h.login("user_alice", actor=STAFF)
    own = h.client.post(PROJECTS, json={"name": "mine"}, headers=alice).json()["id"]
    theirs = h.client.post(PROJECTS, json={"name": "theirs"}, headers=alice).json()["id"]

    with caplog.at_level(logging.INFO):
        assert h.client.delete(f"{PROJECTS}/{own}", headers=alice).status_code == 204
        response = h.client.delete(f"{PROJECTS}/{theirs}", headers=impersonated)
    assert response.status_code == 204

    assert audit(h) == [
        ("project:delete", "succeeded", None),
        ("project:delete", "succeeded", STAFF),
    ]
    actor_lines = [r for r in caplog.records if r.getMessage() == "actor_session"]
    assert [r.actor_external_id for r in actor_lines] == [STAFF]  # type: ignore[attr-defined]


def test_the_actor_is_recorded_when_the_action_is_denied_too(make_harness: Any) -> None:
    h: Harness = make_harness({"clerk_allow_impersonation": True})
    h.directory.set("user_bob", [ExternalMembership(ACME, Role.MEMBER)])
    h.directory.set("user_alice", [ExternalMembership(ACME, Role.ADMIN)])
    project = h.client.post(PROJECTS, json={"name": "keep"}, headers=h.login("user_alice")).json()["id"]
    denied = h.client.delete(f"{PROJECTS}/{project}", headers=h.login("user_bob", actor=STAFF))
    assert denied.status_code == 403
    assert audit(h) == [("project:delete", "denied", STAFF)]


def test_an_impersonator_does_not_inherit_the_operator_allowlist(make_harness: Any) -> None:
    h: Harness = make_harness({"clerk_allow_impersonation": True, "diagnostics_operator_ids": "user_ops"})
    base = "/api/v1/diagnostics/integrations"
    assert h.client.get(base, headers=h.login("user_ops")).status_code == 200
    assert h.client.get(base, headers=h.login("user_ops", actor=STAFF)).status_code == 404
    assert h.client.post(f"{base}/database/check", headers=h.login("user_ops", actor=STAFF)).status_code == 404
