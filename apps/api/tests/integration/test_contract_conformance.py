"""Every operation of the OpenAPI document answers as the document says (DEC-007, API-02).

Each documented operation is requested with representative input, signed in, signed out, and invalid, and
every response must match packages/contracts/openapi.json: a declared status, the declared content type
(errors are application/problem+json), a body that validates against the declared schema. The same check
runs on every response of the whole test session (tests/conftest.py), so a status or error code that a
test provokes anywhere and the document does not declare also fails the run.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx
import pytest
from svix.webhooks import Webhook

from app.kernel.tenancy import Role
from app.modules.identity.adapters.clerk import ClerkWebhookVerifier
from app.modules.identity.domain import ExternalMembership
from tests.conftest import Harness
from tests.integration.test_billing import event_payload, make_pro, sign
from tests.integration.test_platform import SVIX_SECRET
from tests.openapi_contract import RECORDS, Contract, Record

pytestmark = pytest.mark.db

CONTRACT = Contract()
ORG = "org_acme"
OTHER_ORG = "org_other"
UNKNOWN = "00000000-0000-4000-8000-000000000000"
OPERATORS = {"diagnostics_operator_ids": "user_a"}


class Sweep:
    """Sends requests through the harness, checks each response against the document, and remembers which
    operations were requested and which statuses came back."""

    def __init__(self, harness: Harness) -> None:
        self.harness = harness
        self.answered: dict[tuple[str, str], set[int]] = {}

    def __call__(self, method: str, path: str, expect: int, **kwargs: Any) -> httpx.Response:
        response = self.harness.client.request(method, path, **kwargs)
        record = Record(
            method,
            urlparse(str(response.request.url)).path,
            response.status_code,
            response.headers.get("content-type", ""),
            response.content,
        )
        assert CONTRACT.conformance(record) == [], (method, path, response.status_code, response.text[:300])
        assert response.status_code == expect, (method, path, response.status_code, response.text[:300])
        operation = CONTRACT.operation(method, record.path)
        assert operation is not None, f"{method} {path} is not an operation of the document"
        self.answered.setdefault((operation.method, operation.template), set()).add(response.status_code)
        return response


@pytest.fixture
def sweep(make_harness: Any) -> Sweep:
    h: Harness = make_harness(OPERATORS, identity_webhook_verifier=ClerkWebhookVerifier(SVIX_SECRET))
    h.directory.set("user_a", [ExternalMembership(ORG, Role.ADMIN)])
    h.directory.set("user_b", [ExternalMembership(OTHER_ORG, Role.MEMBER)])
    return Sweep(h)


def test_every_operation_answers_as_the_document_says_when_signed_in(sweep: Sweep) -> None:
    h = sweep.harness
    a = h.login("user_a")
    b = h.login("user_b")
    api = "/api/v1"

    sweep("GET", f"{api}/health", 200)
    sweep("GET", f"{api}/ready", 200)
    sweep("GET", f"{api}/me", 200, headers=a)
    sweep("GET", f"{api}/entitlement", 200, headers=a)
    sweep("GET", f"{api}/orgs/{ORG}/entitlement", 200, headers=a)

    personal = sweep("POST", f"{api}/projects", 201, json={"name": "one"}, headers=a).json()["id"]
    sweep("GET", f"{api}/projects?limit=1", 200, headers=a)
    sweep("DELETE", f"{api}/projects/{personal}", 204, headers=a)
    shared = sweep("POST", f"{api}/orgs/{ORG}/projects", 201, json={"name": "two"}, headers=a).json()["id"]
    sweep("GET", f"{api}/orgs/{ORG}/projects", 200, headers=a)
    sweep("DELETE", f"{api}/orgs/{ORG}/projects/{shared}", 204, headers=a)

    sweep("GET", f"{api}/billing/status", 200, headers=a)
    sweep("POST", f"{api}/billing/portal", 409, headers=a)  # no billing account yet
    sweep(
        "POST",
        f"{api}/billing/checkout",
        200,
        json={"plan": "pro_monthly"},
        headers={**a, "Idempotency-Key": uuid.uuid4().hex},
    )
    sweep("POST", f"{api}/billing/portal", 200, headers=a)
    sweep("POST", f"{api}/billing/sync", 200, headers=a)
    sweep("GET", f"{api}/pro/insight", 403, headers=a)
    sweep("GET", f"{api}/orgs/{ORG}/pro/insight", 403, headers=a)
    customer = make_pro(h, "user_a")
    sweep("GET", f"{api}/pro/insight", 200, headers=a)
    sweep("GET", f"{api}/orgs/{ORG}/pro/insight", 200, headers=a)

    sweep("POST", f"{api}/llm/check", 200, headers=a)
    sweep("GET", f"{api}/diagnostics/access", 200, headers=a)
    sweep("GET", f"{api}/diagnostics/integrations", 200, headers=a)
    sweep("POST", f"{api}/diagnostics/integrations/database/check", 200, headers=a)

    event = event_payload(customer)
    sweep(
        "POST",
        f"{api}/webhooks/stripe",
        200,
        content=event,
        headers={"Stripe-Signature": sign(event), "Content-Type": "application/json"},
    )
    sweep(
        "POST",
        f"{api}/webhooks/stripe",
        400,
        content=b"{}",
        headers={"Stripe-Signature": "t=1,v1=bad", "Content-Type": "application/json"},
    )
    organization = json.dumps({"type": "organization.deleted", "data": {"id": "org_gone", "deleted": True}}).encode()
    signed = _clerk_signed(organization)
    sweep("POST", f"{api}/webhooks/clerk", 200, content=organization, headers=signed)
    sweep("POST", f"{api}/webhooks/clerk", 400, content=organization, headers={**signed, "svix-signature": "v1,AAAA"})

    # Errors a client must be able to type: 404 for an organization the caller is not in, 422, 400, 403, 409.
    sweep("GET", f"{api}/orgs/{OTHER_ORG}/projects", 404, headers=a)
    sweep("GET", f"{api}/orgs/{OTHER_ORG}/entitlement", 404, headers=a)
    sweep("GET", f"{api}/orgs/{OTHER_ORG}/pro/insight", 404, headers=a)
    sweep("POST", f"{api}/orgs/{OTHER_ORG}/projects", 404, json={"name": "x"}, headers=a)
    sweep("DELETE", f"{api}/orgs/{OTHER_ORG}/projects/{UNKNOWN}", 404, headers=a)
    sweep("DELETE", f"{api}/projects/{UNKNOWN}", 404, headers=a)
    sweep("GET", f"{api}/diagnostics/integrations", 404, headers=b)
    sweep("POST", f"{api}/diagnostics/integrations/database/check", 404, headers=b)
    sweep("GET", f"{api}/projects?cursor=!!!", 400, headers=a)
    sweep("POST", f"{api}/projects", 422, json={}, headers=a)
    sweep("GET", f"{api}/projects?limit=0", 422, headers=a)
    sweep("POST", f"{api}/billing/checkout", 422, json={"plan": "pro_monthly"}, headers=a)  # no Idempotency-Key
    sweep("POST", f"{api}/diagnostics/integrations/nope/check", 422, headers=a)

    covered = {key for key, statuses in sweep.answered.items() if any(200 <= s < 300 for s in statuses)}
    documented = {(op.method, op.template) for op in CONTRACT.operations}
    assert documented - covered == set(), "operations of the document this test never saw succeed"


def _clerk_signed(payload: bytes) -> dict[str, str]:
    message_id = f"msg_{time.time_ns()}"
    now = int(time.time())
    signature = Webhook(SVIX_SECRET).sign(message_id, datetime.fromtimestamp(now, UTC), payload.decode())
    return {
        "svix-id": message_id,
        "svix-timestamp": str(now),
        "svix-signature": signature,
        "content-type": "application/json",
    }


def test_every_operation_that_needs_a_session_answers_401_as_a_problem(sweep: Sweep) -> None:
    open_routes = {"/api/v1/health", "/api/v1/ready", "/api/v1/webhooks/stripe", "/api/v1/webhooks/clerk"}
    for operation in CONTRACT.operations:
        if operation.template in open_routes:
            continue
        path = operation.template.replace("{org_id}", ORG).replace("{project_id}", UNKNOWN).replace("{key}", "database")
        response = sweep(operation.method, path, 401, json={} if operation.method == "POST" else None)
        assert response.headers["content-type"] == "application/problem+json"
        assert response.json()["code"] == "UNAUTHENTICATED"
    seen = {key for key, statuses in sweep.answered.items() if 401 in statuses}
    assert {t for _, t in seen} == {op.template for op in CONTRACT.operations} - open_routes


def test_an_unavailable_identity_provider_answers_503_as_a_problem(sweep: Sweep) -> None:
    h = sweep.harness
    h.directory.set("user_c", [ExternalMembership("org_c", Role.MEMBER)])
    h.directory.available = False
    response = sweep("GET", "/api/v1/orgs/org_c/projects", 503, headers=h.login("user_c"))
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == "DEPENDENCY_UNAVAILABLE"


def test_the_clerk_webhook_answers_503_as_a_problem_until_it_has_a_signing_secret(make_harness: Any) -> None:
    h: Harness = make_harness()
    response = Sweep(h)(
        "POST", "/api/v1/webhooks/clerk", 503, content=b"{}", headers={"Content-Type": "application/json"}
    )
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == "NOT_CONFIGURED"


def test_the_harness_records_every_response_for_the_check_at_the_end_of_the_session(harness: Harness) -> None:
    before = len(RECORDS)
    response = harness.client.get("/api/v1/me", headers=harness.login("user_a"))
    assert [(r.method, r.path, r.status, r.content_type) for r in RECORDS[before:]] == [
        ("GET", "/api/v1/me", 200, "application/json")
    ]
    assert RECORDS[-1].body == response.content
