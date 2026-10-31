"""OBS-08: what the API's production Sentry and PostHog setup puts on the wire holds none of the canaries.

``tests/telemetry_child.py`` starts the application the way production does (``bootstrap``: JSON logging, the
Sentry SDK with every integration it enables itself, PostHog, the composition root) in a process of its own,
because ``sentry_sdk.init`` patches libraries for good. It sends requests and errors that carry canary secrets,
emails, tokens, and prompt text, and Sentry and PostHog talk to a loopback stub. This test reads the bytes the
stub received, and the child's log output, and looks for the canaries in raw bodies, headers, and paths:
transactions, spans, breadcrumbs, log events, the 5xx the API chose, and PostHog batches included.

The scrubber unit tests prove the hooks work on dicts built by hand; this proves the process sends nothing
around them. A signed-in user's request runs through the production Clerk verifier and directory against a stub
provider, in a database of its own: the Clerk IDs travel in the request path and in the path of the call to the
provider, which must receive no trace header. Mocked: loopback only, no network access; the database is local.
"""

from __future__ import annotations

import gzip
import json
import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse, urlunparse

import pytest
from sentry_sdk.envelope import Envelope

from app.kernel.infrastructure import migrations
from tests.conftest import superuser_query
from tests.stub_server import Recorded, Stub, stub_server
from tests.telemetry_child import CANARIES, CLERK_ORG, CLERK_USER, EMAIL, STRIPE_CUSTOMER

pytestmark = pytest.mark.db

API_DIR = Path(__file__).resolve().parents[2]
ENVELOPE = "/api/1/envelope/"
# What Sentry puts on an outgoing request to continue a trace; a provider has no use for any of them.
TRACE_HEADERS = {"sentry-trace", "baggage", "traceparent"}


def body_of(request: Recorded) -> bytes:
    return gzip.decompress(request.body) if request.headers.get("content-encoding") == "gzip" else request.body


def forms(canary: str) -> set[bytes]:
    """How a canary can appear in bytes: as written, URL-encoded, and JSON-escaped."""
    return {
        canary.encode(),
        quote(canary).encode(),
        quote(canary, safe="").encode(),
        json.dumps(canary)[1:-1].encode(),
        canary.replace(" ", "+").encode(),
    }


def leaks(wire: list[Recorded]) -> list[str]:
    found = []
    for request in wire:
        headers = "\n".join(f"{k}: {v}" for k, v in request.headers.items()).encode()
        for where, data in (("body", body_of(request)), ("headers", headers), ("path", request.path.encode())):
            found += [
                f"{request.path} {where}: {form.decode()}"
                for canary in CANARIES
                for form in forms(canary)
                if form in data
            ]
    return found


class Telemetry:
    """What Sentry and PostHog received, decoded."""

    def __init__(self, wire: list[Recorded]) -> None:
        self.envelopes = [Envelope.deserialize(body_of(r)) for r in wire if r.path == ENVELOPE]
        self.posthog = [r.json() for r in wire if r.path.startswith("/batch")]

    def items(self, kind: str) -> list[dict[str, Any]]:
        return [i.payload.json for e in self.envelopes for i in e.items if i.type == kind]

    @property
    def sent(self) -> list[dict[str, Any]]:
        return [event for batch in self.posthog for event in batch["batch"]]


@pytest.fixture(scope="module")
def wire_database(database_url: str) -> Iterator[str]:
    """A migrated database of its own, where the child creates the user and organization of its session."""
    name = f"app_wire_{uuid.uuid4().hex[:10]}"
    superuser_query(database_url, f'CREATE DATABASE "{name}"')
    url = urlunparse(urlparse(database_url)._replace(path=f"/{name}"))
    migrations.migrate(url)
    yield url
    superuser_query(
        database_url,
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()",
        (name,),
    )
    superuser_query(database_url, f'DROP DATABASE IF EXISTS "{name}"')


def clerk_answer(request: Recorded) -> tuple[int, Any]:
    """The provider: the memberships of the signed-in user, and an empty answer to anything else."""
    if request.path.split("?")[0].endswith("/organization_memberships"):
        return 200, {"data": [{"organization": {"id": CLERK_ORG}, "role": "org:admin"}]}
    return 200, {}


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory, wire_database: str) -> tuple[dict[str, Any], list[Recorded], str]:
    stubs: list[Stub] = []

    def telemetry(request: Recorded) -> tuple[int, Any]:
        if request.path.startswith("/__seen"):
            marker = request.path.split("marker=", 1)[1].replace("+", " ").encode()
            seen = [r for r in list(stubs[0].requests) if r.path != request.path and marker in body_of(r)]
            return 200, {"seen": bool(seen)}
        return 200, {"status": 1}

    result = tmp_path_factory.mktemp("wire") / "result.json"
    with stub_server(telemetry) as received, stub_server(clerk_answer) as provider:
        stubs.append(received)
        # A clean environment: the settings come from the child's own arguments, never from a developer's shell.
        env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "VIRTUAL_ENV", "LANG", "TMPDIR")}
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        child = subprocess.run(  # noqa: S603 - this interpreter, running a module of this repository
            [sys.executable, "-m", "tests.telemetry_child", received.url, provider.url, wire_database, str(result)],
            cwd=API_DIR,
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        assert child.returncode == 0, child.stderr[-3000:]
        wire = [r for r in received.requests if not r.path.startswith("/__seen")]
        provider_requests = list(provider.requests)
    outcome = json.loads(result.read_text())
    outcome["provider_calls"] = [r.path for r in provider_requests]
    outcome["provider_headers"] = [set(r.headers) for r in provider_requests]
    return outcome, wire, child.stdout + child.stderr


def test_the_run_exercised_what_it_claims_to(run: tuple[dict[str, Any], list[Recorded], str]) -> None:
    """Not vacuous: each path sent something, and the answers are the ones the routes give."""
    outcome, wire, _ = run
    assert outcome["statuses"] == {
        "me": 401,
        "unmatched": 404,
        "organization": 200,
        "unmatched_organization": 404,
        "organization_boom": 500,
        "boom": 500,
        "unavailable": 503,
        "misuse": 500,
        "body": 500,
        "capture": 200,
    }
    # The app's own calls went out: the signed-in user's memberships, with their Clerk ID in the path, and the rest.
    assert [p.split("?")[0] for p in outcome["provider_calls"]] == [
        f"/v1/users/{CLERK_USER}/organization_memberships",
        "/v1/customers",
        f"/v1/customers/{STRIPE_CUSTOMER}",
    ]
    telemetry = Telemetry(wire)
    events, transactions = telemetry.items("event"), telemetry.items("transaction")
    assert len(transactions) >= 6
    errors = {e["exception"]["values"][-1]["type"] for e in events if e.get("exception")}
    assert {"RuntimeError", "DependencyUnavailableError"} <= errors
    assert any(e.get("tags", {}).get("error_code") == "DEPENDENCY_UNAVAILABLE" for e in events), "the 5xx the API chose"
    assert any("logentry" in e for e in events), "an event made from an ERROR log record"
    assert any(c.get("category") == "httplib" for e in events for c in e.get("breadcrumbs", {}).get("values", []))
    assert any(s.get("op") == "http.client" for t in transactions for s in t.get("spans", [])), "a span"
    # The queued event and the one sent at once, which PostHog received in whichever order.
    sent = {e["event"]: e for e in telemetry.sent}
    assert sorted(sent) == ["billing_portal_opened", "checkout_started"]
    assert sent["checkout_started"]["properties"]["plan"] == "pro_monthly"
    assert all(e["distinct_id"] for e in sent.values())
    assert "email" not in sent["billing_portal_opened"]["properties"]


def test_ids_in_paths_are_replaced_not_dropped(run: tuple[dict[str, Any], list[Recorded], str]) -> None:
    """The calls to the providers and the routes are still told apart in Sentry, without the IDs in their paths."""
    _, wire, _ = run
    telemetry = Telemetry(wire)
    events, transactions = telemetry.items("event"), telemetry.items("transaction")
    calls = {s["data"]["url"] for t in transactions for s in t.get("spans", []) if s.get("op") == "http.client"}
    assert {u.partition("/v1/")[2] for u in calls} >= {
        "users/{id}/organization_memberships",
        "customers/{id}",
    }
    crumbs = {c.get("data", {}).get("url") for e in events for c in e.get("breadcrumbs", {}).get("values", [])}
    assert any(u and u.endswith("/v1/customers/{id}") for u in crumbs)
    assert "http://testserver/api/v1/orgs/{org_id}/projects" in {t["request"]["url"] for t in transactions}
    assert "http://testserver/api/v1/_canary/orgs/{org_id}/boom" in {e["request"]["url"] for e in events}
    unmatched = {t["transaction"] for t in transactions if t["transaction_info"] == {"source": "url"}}
    assert "http://testserver/api/v1/orgs/{id}/nothing" in unmatched, "no route matched: the path stays"


def test_no_trace_header_reaches_a_provider(run: tuple[dict[str, Any], list[Recorded], str]) -> None:
    """A provider gets no sentry-trace or baggage header (the release, environment, trace ID and route names).

    Clerk and the Stripe shaped path are httpx calls; PostHog is a ``requests`` one, sent from the request here
    (the child reaches the stub by another name than the Sentry DSN's, whose host sentry-sdk never adds a header to).
    """
    outcome, wire, _ = run
    assert len(outcome["provider_headers"]) == 3
    assert [headers & TRACE_HEADERS for headers in outcome["provider_headers"]] == [set()] * 3
    posthog = [r for r in wire if r.path.startswith("/batch")]
    assert posthog, "PostHog received a batch"
    assert [set(r.headers) & TRACE_HEADERS for r in posthog] == [set()] * len(posthog)


def test_no_canary_reaches_sentry_or_posthog(run: tuple[dict[str, Any], list[Recorded], str]) -> None:
    _, wire, _ = run
    assert wire, "nothing was sent"
    assert leaks(wire) == []


def test_no_canary_reaches_the_log_output(run: tuple[dict[str, Any], list[Recorded], str]) -> None:
    _, _, output = run
    assert output.strip(), "nothing was logged"
    assert [c for c in CANARIES if any(form.decode() in output for form in forms(c))] == []


def test_the_events_carry_the_request_id_and_no_user(
    run: tuple[dict[str, Any], list[Recorded], str],
) -> None:
    _, wire, _ = run
    events = Telemetry(wire).items("event")
    assert all(e.get("tags", {}).get("request_id") for e in events)
    assert not [e for e in events if "user" in e]


def test_events_were_at_sentry_and_posthog_when_the_request_returned(
    run: tuple[dict[str, Any], list[Recorded], str],
) -> None:
    """OBS-06 on a serverless instance: the request flushed what it queued before it answered the next call."""
    outcome, _, _ = run
    assert outcome["arrived"] == {
        "organization_boom": True,
        "boom": True,
        "unavailable": True,
        "misuse": True,
        "capture": True,
    }


def test_the_checker_finds_a_canary_where_one_is_hidden() -> None:
    """The scan has teeth: every form it knows is found in every place it looks."""
    hidden = Recorded("POST", "/x", {"x-test": quote(EMAIL, safe="")}, gzip.compress(f'{{"a": "{EMAIL}"}}'.encode()))
    hidden.headers["content-encoding"] = "gzip"
    assert len(leaks([hidden])) >= 2
    assert leaks([Recorded("GET", f"/a?email={EMAIL}", {}, b"")])
    assert leaks([Recorded("POST", "/x", {}, quote(EMAIL).encode())])
    assert not leaks([Recorded("POST", "/x", {"a": "b"}, b'{"email": "[REDACTED]"}')])
