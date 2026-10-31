"""Project writes re-check the caller's membership in the writing transaction (TEN-05, TEN-07, NT-06, NT-07).

The organization context is resolved before the write, so a removal or a
downgrade can commit in between. These tests commit one right after
resolution, through the real request path, and check the answer and the audit.
"""

from __future__ import annotations

import threading
from dataclasses import replace
from typing import Any
from uuid import UUID

import pytest

from app.kernel.authorization import ALL_ROLES
from app.kernel.errors import ForbiddenError, NotFoundError
from app.kernel.tenancy import Role, VerifiedIdentity
from app.modules.identity.domain import ExternalMembership
from app.modules.projects import service as project_service
from app.modules.projects.adapters import PgProjectRepository
from app.modules.projects.domain import PROJECT_DELETE
from tests.conftest import Harness, superuser_query
from tests.lock_waits import wait_for_lock_wait
from tests.membership_races import DOWNGRADE, REMOVE, change_after_resolution

pytestmark = pytest.mark.db

ACME = "org_acme"
PROJECTS = f"/api/v1/orgs/{ACME}/projects"


@pytest.fixture
def acme(harness: Harness) -> dict[str, Any]:
    """Alice administers Acme and Bob is a member; both are mirrored, and Acme has one project."""
    harness.directory.set("user_alice", [ExternalMembership(ACME, Role.ADMIN)])
    harness.directory.set("user_bob", [ExternalMembership(ACME, Role.MEMBER)])
    alice, bob = harness.login("user_alice"), harness.login("user_bob")
    created = harness.client.post(PROJECTS, json={"name": "acme shared"}, headers=alice)
    assert created.status_code == 201
    assert harness.client.get(PROJECTS, headers=bob).status_code == 200
    return {"alice": alice, "bob": bob, "project": created.json()["id"]}


def audit(harness: Harness) -> list[tuple[Any, ...]]:
    return superuser_query(harness.database_url, "SELECT action, target_id, outcome FROM app.audit_log ORDER BY id")


def project_count(harness: Harness, column: str, value: str) -> int:
    rows = superuser_query(harness.database_url, f"SELECT count(*) FROM app.projects WHERE {column} = %s", (value,))
    return int(rows[0][0])


def test_create_by_a_member_removed_after_resolution_is_not_found(
    harness: Harness, acme: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not a 500 from the row-level security check: a removed member gets 404, and nothing is written."""
    change_after_resolution(harness, monkeypatch, REMOVE)
    r = harness.client.post(PROJECTS, json={"name": "after removal"}, headers=acme["bob"])
    assert r.status_code == 404, r.text
    assert r.json()["code"] == "NOT_FOUND"
    assert project_count(harness, "name", "after removal") == 0
    assert superuser_query(harness.database_url, "SELECT count(*) FROM app.outbox") == [(1,)]  # the fixture's create


def test_create_outside_a_request_scope_rechecks_the_membership(harness: Harness, acme: dict[str, Any]) -> None:
    ctx = harness.container.identity.resolver.organization(VerifiedIdentity("user_bob"), ACME)
    superuser_query(harness.database_url, REMOVE, (ctx.org_id, ctx.user_id))
    with pytest.raises(NotFoundError):
        harness.container.projects.service.create(ctx, "after removal")
    assert project_count(harness, "name", "after removal") == 0


def test_writes_in_an_organization_deleted_after_resolution_are_not_found(
    harness: Harness, acme: dict[str, Any]
) -> None:
    """organization.deleted can land between resolution and the write while a membership row remains (a refresh that
    raced the webhook): the locked re-check counts a deleted organization as no membership (TEN-05)."""
    ctx = harness.container.identity.resolver.organization(VerifiedIdentity("user_alice"), ACME)
    superuser_query(
        harness.database_url, "UPDATE app.organizations SET deleted_at = now() WHERE id = %s", (ctx.org_id,)
    )
    service = harness.container.projects.service
    with pytest.raises(NotFoundError):
        service.create(ctx, "after deletion")
    assert project_count(harness, "name", "after deletion") == 0
    with pytest.raises(NotFoundError):
        service.delete(ctx, UUID(acme["project"]), "req-deleted-org")
    assert project_count(harness, "id", acme["project"]) == 1
    assert audit(harness) == [("project:delete", acme["project"], "denied")]


def test_delete_refused_by_the_role_recheck_is_audited(
    harness: Harness, acme: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """An admin downgraded after resolution: 403, the project stays, and the denial is audited (ADM-01, NT-07)."""
    change_after_resolution(harness, monkeypatch, DOWNGRADE)
    r = harness.client.delete(f"{PROJECTS}/{acme['project']}", headers=acme["alice"])
    assert r.status_code == 403, r.text
    assert r.json()["code"] == "FORBIDDEN"
    assert audit(harness) == [("project:delete", acme["project"], "denied")]
    assert project_count(harness, "id", acme["project"]) == 1


def test_delete_by_an_admin_removed_after_resolution_is_not_found_and_audited(
    harness: Harness, acme: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A removed admin is a non-member: 404 as for any other (TEN-05), and the refused attempt is audited."""
    change_after_resolution(harness, monkeypatch, REMOVE)
    r = harness.client.delete(f"{PROJECTS}/{acme['project']}", headers=acme["alice"])
    assert r.status_code == 404, r.text
    assert r.json()["code"] == "NOT_FOUND"
    assert audit(harness) == [("project:delete", acme["project"], "denied")]
    assert project_count(harness, "id", acme["project"]) == 1


def test_the_role_recheck_applies_the_declared_permission(
    harness: Harness, acme: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Granting delete to members in PROJECT_DELETE is enough: the locked re-check applies that same permission."""
    monkeypatch.setattr(project_service, "PROJECT_DELETE", replace(PROJECT_DELETE, org_roles=ALL_ROLES))
    r = harness.client.delete(f"{PROJECTS}/{acme['project']}", headers=acme["bob"])
    assert r.status_code == 204, r.text
    assert audit(harness) == [("project:delete", acme["project"], "succeeded")]
    assert project_count(harness, "id", acme["project"]) == 0


def test_role_recheck_denial_is_audited_outside_a_request_scope(harness: Harness, acme: dict[str, Any]) -> None:
    ctx = harness.container.identity.resolver.organization(VerifiedIdentity("user_alice"), ACME)
    assert ctx.role is Role.ADMIN
    superuser_query(harness.database_url, DOWNGRADE, (ctx.org_id, ctx.user_id))
    with pytest.raises(ForbiddenError):
        harness.container.projects.service.delete(ctx, UUID(acme["project"]), "req-recheck")
    assert superuser_query(harness.database_url, "SELECT action, outcome, request_id FROM app.audit_log") == [
        ("project:delete", "denied", "req-recheck")
    ]
    assert project_count(harness, "id", acme["project"]) == 1


@pytest.mark.parametrize("operation", ["create", "delete"])
def test_a_downgrade_waits_for_the_write_that_holds_the_membership_lock(
    harness: Harness, acme: dict[str, Any], monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    """TEN-07, NT-06: the service locks the caller's membership until its transaction ends, so a downgrade that
    arrives in the middle of a create or a delete waits for it instead of committing first."""
    ctx = harness.container.identity.resolver.organization(VerifiedIdentity("user_alice"), ACME)
    committed = threading.Event()

    def downgrade() -> None:
        with harness.container.kernel.database.transaction("app_worker", {}) as conn:
            conn.execute(DOWNGRADE, (ctx.org_id, ctx.user_id))
        committed.set()

    waiter = threading.Thread(target=downgrade)
    write = getattr(PgProjectRepository, operation)
    waited: list[bool] = []

    def write_while_a_downgrade_arrives(repository: PgProjectRepository, *args: Any) -> Any:
        waiter.start()
        wait_for_lock_wait(harness.database_url)  # the downgrade is queued behind the lock this transaction holds
        waited.append(not committed.is_set())
        return write(repository, *args)

    monkeypatch.setattr(PgProjectRepository, operation, write_while_a_downgrade_arrives)
    service = harness.container.projects.service
    if operation == "create":
        service.create(ctx, "during a downgrade")
    else:
        service.delete(ctx, UUID(acme["project"]), None)
    waiter.join(5)
    assert waited == [True]
    assert committed.is_set()
    assert superuser_query(
        harness.database_url, "SELECT role FROM app.organization_memberships WHERE user_id = %s", (ctx.user_id,)
    ) == [("member",)]
