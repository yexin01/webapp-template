"""The project list says what the caller may do, from the permissions the routes enforce (ARCH-03, TEN-02).

The web app renders ``can`` and never re-derives it from a role. For a personal workspace, an organization
admin and an organization member, ``can`` equals what POST and DELETE return when the caller tries them,
and it follows a change to the permissions with nothing else edited.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from app.kernel.authorization import ADMIN_ONLY, ALL_ROLES
from app.kernel.tenancy import Role
from app.modules.identity.domain import ExternalMembership
from app.modules.projects import service as project_service
from app.modules.projects.domain import PROJECT_CREATE, PROJECT_DELETE
from tests.conftest import Harness

pytestmark = pytest.mark.db

ACME = "org_acme"
PERSONAL = "/api/v1/projects"
IN_ACME = f"/api/v1/orgs/{ACME}/projects"

# Who asks, where, and what the permissions in projects/domain.py grant there.
SHIPPED = [
    pytest.param("alice", PERSONAL, {"create": True, "delete": True}, id="personal"),
    pytest.param("alice", IN_ACME, {"create": True, "delete": True}, id="organization admin"),
    pytest.param("bob", IN_ACME, {"create": True, "delete": False}, id="organization member"),
]
# The same callers once members may delete but only admins may create.
CHANGED = [
    pytest.param("alice", PERSONAL, {"create": True, "delete": True}, id="personal"),
    pytest.param("alice", IN_ACME, {"create": True, "delete": True}, id="organization admin"),
    pytest.param("bob", IN_ACME, {"create": False, "delete": True}, id="organization member"),
]


@pytest.fixture
def people(harness: Harness) -> dict[str, Any]:
    """Alice administers Acme and Bob is a member. Each tenant holds a project of Alice's to try to delete."""
    harness.directory.set("user_alice", [ExternalMembership(ACME, Role.ADMIN)])
    harness.directory.set("user_bob", [ExternalMembership(ACME, Role.MEMBER)])
    alice, bob = harness.login("user_alice"), harness.login("user_bob")
    personal = harness.client.post(PERSONAL, json={"name": "alice's own"}, headers=alice)
    shared = harness.client.post(IN_ACME, json={"name": "acme shared"}, headers=alice)
    assert (personal.status_code, shared.status_code) == (201, 201)
    return {"alice": alice, "bob": bob, PERSONAL: personal.json()["id"], IN_ACME: shared.json()["id"]}


def trying(harness: Harness, headers: dict[str, str], path: str, target: str) -> dict[str, bool]:
    """What the routes do when the caller tries to create a project and to delete ``target``."""
    created = harness.client.post(path, json={"name": "tried"}, headers=headers).status_code
    deleted = harness.client.delete(f"{path}/{target}", headers=headers).status_code
    assert created in (201, 403), created
    assert deleted in (204, 403), deleted
    return {"create": created == 201, "delete": deleted == 204}


@pytest.mark.parametrize(("who", "path", "granted"), SHIPPED)
def test_the_list_says_what_the_caller_may_do(
    harness: Harness, people: dict[str, Any], who: str, path: str, granted: dict[str, bool]
) -> None:
    can = harness.client.get(path, headers=people[who]).json()["can"]
    assert can == granted
    assert trying(harness, people[who], path, people[path]) == can


@pytest.mark.parametrize(("who", "path", "granted"), CHANGED)
def test_the_list_follows_a_change_to_the_permissions(
    harness: Harness,
    people: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    who: str,
    path: str,
    granted: dict[str, bool],
) -> None:
    """An adopter edits the permissions and nothing else: ``can`` and the routes change together."""
    monkeypatch.setattr(project_service, "PROJECT_CREATE", replace(PROJECT_CREATE, org_roles=ADMIN_ONLY))
    monkeypatch.setattr(project_service, "PROJECT_DELETE", replace(PROJECT_DELETE, org_roles=ALL_ROLES))
    can = harness.client.get(path, headers=people[who]).json()["can"]
    assert can == granted
    assert trying(harness, people[who], path, people[path]) == can


def test_every_page_carries_the_decision(harness: Harness, people: dict[str, Any]) -> None:
    """A page reached by cursor says the same as the first, so the web app can keep the first page's."""
    for name in ("second", "third"):
        assert harness.client.post(IN_ACME, json={"name": name}, headers=people["bob"]).status_code == 201
    first = harness.client.get(f"{IN_ACME}?limit=2", headers=people["bob"]).json()
    assert first["next_cursor"]
    later = harness.client.get(f"{IN_ACME}?limit=2&cursor={first['next_cursor']}", headers=people["bob"]).json()
    assert later["items"]
    assert later["can"] == first["can"] == {"create": True, "delete": False}
