"""OBS-04, OBS-05: the logs and error reports of a request carry its ID and the internal user and tenant IDs.

The IDs are the database's own UUIDs, never the identity provider's (``user_…``, ``org_…``), an email, or
a name. They are known only once the request authenticates, so the logs of a request refused earlier carry none.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import sentry_sdk
from sentry_sdk.envelope import Envelope
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.transport import Transport

from app.kernel.infrastructure.observability import JsonFormatter, sentry_options
from app.kernel.tenancy import Role
from app.modules.identity.domain import ExternalMembership
from app.modules.projects.service import ProjectService
from tests.conftest import Harness, make_settings, superuser_query

pytestmark = pytest.mark.db

ACME, GLOBEX = "org_acme", "org_globex"
ALICE, BOB = "user_alice", "user_bob"
# Built at run time: Sentry sends the source lines around a frame, so a literal would be sent with the stack.
EMAIL = "alice" + "@example.com"
CLERK_IDS = [ACME, GLOBEX, ALICE, BOB]


@pytest.fixture
def lines() -> Iterator[list[dict[str, Any]]]:
    """The JSON log lines, formatted as they are written: while the request's context is still current."""
    written: list[dict[str, Any]] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            if record.name.startswith("app"):  # the application's lines, not the test client's
                written.append(json.loads(JsonFormatter().format(record)))

    handler, root = Capture(), logging.getLogger()
    level = root.level
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    yield written
    root.removeHandler(handler)
    root.setLevel(level)


@pytest.fixture
def world(harness: Harness) -> Harness:
    harness.directory.set(ALICE, [ExternalMembership(ACME, Role.ADMIN)])
    harness.directory.set(BOB, [ExternalMembership(GLOBEX, Role.MEMBER)])
    return harness


def access(lines: list[dict[str, Any]], request_id: str) -> dict[str, Any]:
    [line] = [x for x in lines if x["logger"] == "app.access" and x.get("request_id") == request_id]
    return line


def internal_user(h: Harness, clerk_user: str) -> str:
    [(user,)] = superuser_query(
        h.database_url, "SELECT id::text FROM app.users WHERE clerk_user_id = %s", (clerk_user,)
    )
    return user


def internal_ids(h: Harness, clerk_user: str, clerk_org: str) -> tuple[str, str]:
    [(user,)] = superuser_query(
        h.database_url, "SELECT id::text FROM app.users WHERE clerk_user_id = %s", (clerk_user,)
    )
    [(org,)] = superuser_query(
        h.database_url, "SELECT id::text FROM app.organizations WHERE clerk_org_id = %s", (clerk_org,)
    )
    return user, org


def test_an_organization_request_logs_the_internal_user_and_organization_ids(
    world: Harness, lines: list[dict[str, Any]]
) -> None:
    response = world.client.get(
        f"/api/v1/orgs/{ACME}/projects", headers={**world.login(ALICE), "X-Request-ID": "request-org-0001"}
    )
    assert response.status_code == 200
    user, org = internal_ids(world, ALICE, ACME)
    line = access(lines, "request-org-0001")
    assert (line["user_id"], line["org_id"]) == (user, org)
    assert line["status"] == 200
    # Every line the request wrote, not only the access log, and never an identity provider's ID.
    mine = [x for x in lines if x.get("request_id") == "request-org-0001"]
    assert all(x["user_id"] == user and x["org_id"] == org for x in mine)
    assert not [c for c in CLERK_IDS if c in json.dumps(lines)]


def test_a_personal_request_logs_the_user_only(world: Harness, lines: list[dict[str, Any]]) -> None:
    assert (
        world.client.get("/api/v1/me", headers={**world.login(ALICE), "X-Request-ID": "request-me-0001"}).status_code
        == 200
    )
    user = internal_user(world, ALICE)
    line = access(lines, "request-me-0001")
    assert line["user_id"] == user
    assert "org_id" not in line


@pytest.mark.parametrize(
    ("path", "token", "status"),
    [
        pytest.param("/api/v1/me", None, 401, id="no token"),
        pytest.param("/api/v1/me", "not-a-token", 401, id="an invalid token"),
        pytest.param(f"/api/v1/orgs/{GLOBEX}/projects", ALICE, 404, id="an organization the user is not in"),
        pytest.param("/api/v1/health", None, 200, id="a route without authentication"),
    ],
)
def test_a_request_that_never_got_a_tenant_context_logs_no_ids(
    world: Harness, lines: list[dict[str, Any]], path: str, token: str | None, status: int
) -> None:
    headers = world.login(token) if token == ALICE else {"Authorization": f"Bearer {token}"} if token else {}
    response = world.client.get(path, headers={**headers, "X-Request-ID": "request-anon-01"})
    assert response.status_code == status
    line = access(lines, "request-anon-01")
    assert "user_id" not in line
    assert "org_id" not in line


def test_requests_in_flight_together_do_not_get_each_others_ids(world: Harness, lines: list[dict[str, Any]]) -> None:
    # Each user's first request creates their rows; concurrent first requests are the identity module's concern.
    for path, user in [(f"/api/v1/orgs/{ACME}/projects", ALICE), (f"/api/v1/orgs/{GLOBEX}/projects", BOB)]:
        assert world.client.get(path, headers=world.login(user)).status_code == 200

    async def both() -> list[httpx.Response]:
        transport = httpx.ASGITransport(app=world.client.app)  # type: ignore[arg-type]
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            sent = [
                (f"/api/v1/orgs/{ACME}/projects", world.login(ALICE), "request-alice-1"),
                (f"/api/v1/orgs/{GLOBEX}/projects", world.login(BOB), "request-bob-0001"),
            ] * 3
            return await asyncio.gather(
                *(
                    client.get(path, headers={**auth, "X-Request-ID": f"{rid}{i}"})
                    for i, (path, auth, rid) in enumerate(sent)
                )
            )

    assert {r.status_code for r in asyncio.run(both())} == {200}
    alice, bob = internal_ids(world, ALICE, ACME), internal_ids(world, BOB, GLOBEX)
    for i in range(6):
        who = alice if i % 2 == 0 else bob
        rid = f"request-alice-1{i}" if i % 2 == 0 else f"request-bob-0001{i}"
        line = access(lines, rid)
        assert (line["user_id"], line["org_id"]) == who


class _Capture(Transport):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[dict[str, Any]] = []

    def capture_envelope(self, envelope: Envelope) -> None:
        self.events += [item.payload.json for item in envelope.items if item.type == "event"]


def test_an_error_report_carries_the_request_id_and_the_internal_ids_and_no_user(
    world: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError(f"the database said no for {EMAIL}")

    monkeypatch.setattr(ProjectService, "list", failing)
    transport = _Capture()
    options = sentry_options(
        make_settings(error_reporting_mode="sandbox", sentry_dsn="https://public@o0.ingest.example/0")
    )
    sentry_sdk.init(**options, transport=transport, default_integrations=False, integrations=[LoggingIntegration()])
    try:
        response = world.client.get(
            f"/api/v1/orgs/{ACME}/projects", headers={**world.login(ALICE), "X-Request-ID": "request-error-1"}
        )
        sentry_sdk.flush(timeout=2)
    finally:
        sentry_sdk.get_client().close()
        sentry_sdk.get_global_scope().set_client(None)
    assert response.status_code == 500
    user, org = internal_ids(world, ALICE, ACME)
    [event] = transport.events
    assert event["tags"] == {"request_id": "request-error-1", "user_id": user, "org_id": org}
    assert "user" not in event
    assert not [c for c in [*CLERK_IDS, EMAIL] if c in json.dumps(event)]
