"""OBS-08: what the API's production Sentry and PostHog setup puts on the wire holds none of the canaries.

``tests/telemetry_child.py`` starts the application the way production does (``bootstrap``: JSON logging, the
Sentry SDK with every integration it enables itself, PostHog, the composition root) in a process of its own,
because ``sentry_sdk.init`` patches libraries for good. It sends requests and errors that carry canary secrets,
emails, tokens, and prompt text, and Sentry and PostHog talk to a loopback stub. This test reads the bytes the
stub received, and the child's log output, and looks for the canaries in raw bodies, headers, and paths:
transactions, spans, breadcrumbs, log events, the 5xx the API chose, and PostHog batches included.

The scrubber unit tests prove the hooks work on dicts built by hand; this proves the process sends nothing
around them. Mocked: loopback only, no network access.
"""

from __future__ import annotations

import gzip
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
from sentry_sdk.envelope import Envelope

from tests.stub_server import Recorded, Stub, stub_server
from tests.telemetry_child import CANARIES, EMAIL

API_DIR = Path(__file__).resolve().parents[2]
ENVELOPE = "/api/1/envelope/"


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
def run(tmp_path_factory: pytest.TempPathFactory) -> tuple[dict[str, Any], list[Recorded], str]:
    stubs: list[Stub] = []

    def telemetry(request: Recorded) -> tuple[int, Any]:
        if request.path.startswith("/__seen"):
            marker = request.path.split("marker=", 1)[1].replace("+", " ").encode()
            seen = [r for r in list(stubs[0].requests) if r.path != request.path and marker in body_of(r)]
            return 200, {"seen": bool(seen)}
        return 200, {"status": 1}

    result = tmp_path_factory.mktemp("wire") / "result.json"
    with stub_server(telemetry) as received, stub_server(lambda request: (200, {})) as provider:
        stubs.append(received)
        # A clean environment: the settings come from the child's own arguments, never from a developer's shell.
        env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "VIRTUAL_ENV", "LANG", "TMPDIR")}
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        child = subprocess.run(  # noqa: S603 - this interpreter, running a module of this repository
            [sys.executable, "-m", "tests.telemetry_child", received.url, provider.url, str(result)],
            cwd=API_DIR,
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        assert child.returncode == 0, child.stderr[-3000:]
        wire = [r for r in received.requests if not r.path.startswith("/__seen")]
        provider_calls = [r.path for r in provider.requests]
    outcome = json.loads(result.read_text())
    outcome["provider_calls"] = provider_calls
    return outcome, wire, child.stdout + child.stderr


def test_the_run_exercised_what_it_claims_to(run: tuple[dict[str, Any], list[Recorded], str]) -> None:
    """Not vacuous: each path sent something, and the answers are the ones the routes give."""
    outcome, wire, _ = run
    assert outcome["statuses"] == {
        "me": 401,
        "unmatched": 404,
        "boom": 500,
        "unavailable": 503,
        "misuse": 500,
        "body": 500,
        "capture": 200,
    }
    assert [p.split("?")[0] for p in outcome["provider_calls"]] == ["/v1/customers"]  # the app's own call went out
    telemetry = Telemetry(wire)
    events, transactions = telemetry.items("event"), telemetry.items("transaction")
    assert len(transactions) >= 6
    errors = {e["exception"]["values"][-1]["type"] for e in events if e.get("exception")}
    assert {"RuntimeError", "DependencyUnavailableError"} <= errors
    assert any(e.get("tags", {}).get("error_code") == "DEPENDENCY_UNAVAILABLE" for e in events), "the 5xx the API chose"
    assert any("logentry" in e for e in events), "an event made from an ERROR log record"
    assert any(c.get("category") == "httplib" for e in events for c in e.get("breadcrumbs", {}).get("values", []))
    assert any(s.get("op") == "http.client" for t in transactions for s in t.get("spans", [])), "a span"
    assert [e["event"] for e in telemetry.sent] == ["checkout_started"]
    assert telemetry.sent[0]["properties"]["plan"] == "pro_monthly"
    assert telemetry.sent[0]["distinct_id"]


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
    assert outcome["arrived"] == {"boom": True, "unavailable": True, "misuse": True, "capture": True}


def test_the_checker_finds_a_canary_where_one_is_hidden() -> None:
    """The scan has teeth: every form it knows is found in every place it looks."""
    hidden = Recorded("POST", "/x", {"x-test": quote(EMAIL, safe="")}, gzip.compress(f'{{"a": "{EMAIL}"}}'.encode()))
    hidden.headers["content-encoding"] = "gzip"
    assert len(leaks([hidden])) >= 2
    assert leaks([Recorded("GET", f"/a?email={EMAIL}", {}, b"")])
    assert leaks([Recorded("POST", "/x", {}, quote(EMAIL).encode())])
    assert not leaks([Recorded("POST", "/x", {"a": "b"}, b'{"email": "[REDACTED]"}')])
