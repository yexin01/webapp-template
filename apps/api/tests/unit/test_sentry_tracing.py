"""OBS-02 / OBS-08: performance traces reach Sentry scrubbed, like error events.

Transactions (and the spans inside them) skip ``before_send``. Their names,
request data, span descriptions and span data can hold URLs with query
strings: Clerk tickets, handshake tokens, Checkout session IDs, API keys.
"""

from __future__ import annotations

import copy
import json
import time
from typing import Any

import pytest
import sentry_sdk
from fastapi.testclient import TestClient
from sentry_sdk.envelope import Envelope
from sentry_sdk.integrations.asgi import SentryAsgiMiddleware
from sentry_sdk.transport import Transport

from app.kernel.infrastructure import observability
from app.kernel.infrastructure.observability import (
    MAX_SPAN_TEXT,
    scrub_breadcrumb,
    scrub_event,
    scrub_transaction,
    sentry_options,
)
from app.kernel.infrastructure.redaction import MAX_TEXT_LENGTH, REDACTED, TRUNCATED, redact_text
from tests.conftest import make_settings

DSN = "https://public@o0.ingest.example/0"
TICKET = "CANARYTICKET42"
EMAIL = "canary.person@example.com"
TOKEN = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJDQU5BUlkifQ.c2lnbmF0dXJlQ0FOQVJZ"
SECRET = "sk_" + "live_CANARY1234567890"
CANARIES = [TICKET, EMAIL, TOKEN, SECRET, "CANARY"]
TRACE_ID = "771a43a4192642f0b136d5159a501700"


def assert_clean(value: Any) -> None:
    text = json.dumps(value, default=str)
    assert not [canary for canary in CANARIES if canary in text], text


def transaction_event() -> dict[str, Any]:
    """A transaction as sentry-sdk 2.72 builds it, with tokens wherever they can appear."""
    return {
        "type": "transaction",
        "transaction": f"GET /api/v1/invites/{EMAIL}?__clerk_ticket={TICKET}",
        "transaction_info": {"source": "url"},
        "contexts": {
            "trace": {
                "trace_id": TRACE_ID,
                "span_id": "bf25ad57950b5e81",
                "parent_span_id": "b6b1f6f6f6f6f6f6",
                "op": "http.server",
                "description": None,
                "origin": "auto.http.starlette",
                "status": "ok",
                # Received from the caller in the baggage header, and sent on in the envelope header.
                "dynamic_sampling_context": {
                    "trace_id": TRACE_ID,
                    "public_key": "public",
                    "sample_rate": "1.0",
                    "sampled": "true",
                    "transaction": f"GET /sign-up?__clerk_ticket={TICKET}",
                    "release": f"web@1 https://app.example.com/sign-up?__clerk_ticket={TICKET}",
                },
                "data": {
                    "thread.id": "8274370944",
                    "thread.name": "MainThread",
                    "http.response.status_code": 200,
                    "url": f"https://api.example.com/api/v1/x?token={TICKET}",
                },
            },
            "response": {"status_code": 200},
        },
        "tags": {"asgi.type": "http", "note": f"for {EMAIL}"},
        "extra": {"payload": {"email": EMAIL, "list": [TOKEN]}},
        "user": {"id": "user_1", "email": EMAIL, "ip_address": "203.0.113.7"},
        "request": {
            "method": "GET",
            "url": f"https://api.example.com/api/v1/invites?__clerk_ticket={TICKET}#{TICKET}",
            "query_string": f"__clerk_ticket={TICKET}",
            "fragment": TICKET,
            "headers": {"Authorization": f"Bearer {TOKEN}", "Accept": "application/json"},
            "cookies": {"__session": TOKEN},
            "env": {"REMOTE_ADDR": "203.0.113.7"},
        },
        "spans": [
            {
                # An outgoing request, as the httpx and stdlib integrations record it.
                "trace_id": TRACE_ID,
                "span_id": "b681dd5fa19ffcf8",
                "parent_span_id": "bf25ad57950b5e81",
                "same_process_as_parent": True,
                "op": "http.client",
                "description": "GET https://api.clerk.com/v1/users",
                "start_timestamp": "2026-10-15T19:01:24.750381Z",
                "timestamp": "2026-10-15T19:01:24.750408Z",
                "origin": "auto.http.httpx",
                "status": "ok",
                "tags": {"status": "ok"},
                "data": {
                    "http.method": "GET",
                    "url": "https://api.clerk.com/v1/users",
                    "http.query": f"email_address={EMAIL}&__clerk_ticket={TICKET}",
                    "http.fragment": TICKET,
                    "http.response.status_code": 200,
                    "reason": "OK",
                    "thread.id": "8274370944",
                },
            },
            {
                # A span with OpenTelemetry attribute names and a URL in its description.
                "op": "http.server",
                "description": f"POST /api/v1/webhooks/clerk?token={TICKET}",
                "data": {
                    "http.url": f"https://api.example.com/api/v1/webhooks/clerk?token={TICKET}",
                    "http.target": f"/api/v1/webhooks/clerk?token={TICKET}",
                    "url.full": f"https://api.example.com/x?__clerk_handshake={TOKEN}#{TICKET}",
                    "url.query": f"__clerk_db_jwt={TOKEN}",
                    "url.fragment": TICKET,
                    "url.path": "/x",
                    "url": f"api.example.com/hooks?ticket={TICKET}",
                    "user.email": EMAIL,
                    "user.id": "user_1",
                    "server.address": "api.example.com",
                },
            },
            {
                "op": "db",
                "description": f"SELECT id FROM users WHERE email = '{EMAIL}'",
                "data": {"db.system": "postgresql", "db.params": [EMAIL, SECRET], "token": TICKET},
            },
        ],
        "measurements": {},
        "environment": "test",
    }


def test_sentry_scrubs_transactions_and_sends_every_span_inside_one() -> None:
    options = sentry_options(make_settings(error_reporting_mode="sandbox", sentry_dsn=DSN))
    assert options["before_send_transaction"] is scrub_transaction
    # Streamed spans would skip before_send_transaction.
    assert options["trace_lifecycle"] == "static"


class TestScrubTransaction:
    def test_no_token_or_personal_data_is_left(self) -> None:
        assert_clean(scrub_transaction(transaction_event(), {}))

    def test_urls_keep_only_their_origin_and_path(self) -> None:
        scrubbed = scrub_transaction(transaction_event(), {})
        assert scrubbed["transaction"] == f"GET /api/v1/invites/{REDACTED}"
        assert scrubbed["request"] == {
            "method": "GET",
            "url": "https://api.example.com/api/v1/invites",
            "headers": {"Authorization": REDACTED, "Accept": "application/json"},
            "cookies": REDACTED,
            "env": REDACTED,
        }
        trace = scrubbed["contexts"]["trace"]
        assert trace["dynamic_sampling_context"]["transaction"] == "GET /sign-up"
        assert trace["dynamic_sampling_context"]["release"] == "web@1 https://app.example.com/sign-up"
        assert trace["data"]["url"] == "https://api.example.com/api/v1/x"

    def test_spans_drop_query_strings_fragments_and_user_fields(self) -> None:
        client, server, db = scrub_transaction(transaction_event(), {})["spans"]
        assert client["description"] == "GET https://api.clerk.com/v1/users"
        assert client["data"] == {
            "http.method": "GET",
            "url": "https://api.clerk.com/v1/users",
            "http.response.status_code": 200,
            "reason": "OK",
            "thread.id": "8274370944",
        }
        assert server["description"] == "POST /api/v1/webhooks/clerk"
        assert server["data"] == {
            "http.url": "https://api.example.com/api/v1/webhooks/clerk",
            "http.target": "/api/v1/webhooks/clerk",
            "url.full": "https://api.example.com/x",
            "url.path": "/x",
            "url": "api.example.com/hooks",
            "server.address": "api.example.com",
        }
        assert db["description"] == f"SELECT id FROM users WHERE email = '{REDACTED}'"
        assert db["data"] == {"db.system": "postgresql", "db.params": [REDACTED, REDACTED], "token": REDACTED}

    def test_everything_else_is_kept(self) -> None:
        event, scrubbed = transaction_event(), scrub_transaction(transaction_event(), {})
        assert "user" not in scrubbed
        for key in ("type", "transaction_info", "measurements", "environment"):
            assert scrubbed[key] == event[key]
        trace = scrubbed["contexts"]["trace"]
        for key in ("trace_id", "span_id", "parent_span_id", "op", "description", "origin", "status"):
            assert trace[key] == event["contexts"]["trace"][key]
        assert trace["data"]["thread.name"] == "MainThread"
        assert scrubbed["contexts"]["response"] == {"status_code": 200}
        assert scrubbed["tags"] == {"asgi.type": "http", "note": f"for {REDACTED}"}
        span = scrubbed["spans"][0]
        for key in ("trace_id", "span_id", "parent_span_id", "same_process_as_parent", "op", "origin", "status"):
            assert span[key] == event["spans"][0][key]

    def test_megabyte_values_are_scrubbed_in_well_under_100_ms(self) -> None:
        """OBS-06: the scrubber's work stays bounded whatever a span holds."""
        # Without the cap, a quadratic pattern would take minutes on these values rather than fail.
        assert redact_text("x " * MAX_TEXT_LENGTH).endswith(TRUNCATED)
        event = transaction_event()
        event["transaction"] = "GET /" + "a/?#" * 250_000
        description, url = "GET /" + "eyJ-" * 250_000, "https://h/?" + "a@" * 500_000
        event["spans"] = [{"description": description, "data": {"url": url, "note": url[8:]}} for _ in range(10)]
        started = time.thread_time()  # CPU time, which a busy machine does not inflate
        scrubbed = scrub_transaction(event, {})
        assert time.thread_time() - started < 0.1
        assert scrubbed["transaction"] == "GET /a/"
        assert scrubbed["spans"][0]["data"]["url"] == "https://h/"


class TestScrubbingCost:
    """OBS-06: before_send_transaction runs where a request's transaction ends, on the event loop.

    The SDK sends up to 1,000 spans per transaction (max_spans), with text of
    any length, and a caller can have any request traced (sentry-trace ...-1).
    """

    SPANS = 1_000

    @pytest.fixture
    def read(self, monkeypatch: pytest.MonkeyPatch) -> list[str]:
        """The text each call hands to redact_text, up to where it stops reading."""
        read: list[str] = []

        def recording(value: str, *, max_length: int = MAX_TEXT_LENGTH) -> str:
            read.append(value[:max_length])
            return redact_text(value, max_length=max_length)

        monkeypatch.setattr(observability, "redact_text", recording)
        return read

    @staticmethod
    def transaction(descriptions: list[str]) -> dict[str, Any]:
        spans = [
            {
                "trace_id": TRACE_ID,
                "span_id": f"{i:016x}",
                "parent_span_id": "bf25ad57950b5e81",
                "same_process_as_parent": True,
                "op": "db",
                "description": description,
                "start_timestamp": f"2026-10-15T19:01:24.{i:06d}Z",
                "timestamp": f"2026-10-15T19:01:25.{i:06d}Z",
                "origin": "auto.db.sqlalchemy",
                "status": "ok",
                "data": {"db.system": "postgresql", "thread.name": "MainThread", "url": f"https://h/{i}?token={i}"},
            }
            for i, description in enumerate(descriptions)
        ]
        trace = {"trace_id": TRACE_ID, "span_id": "bf25ad57950b5e81", "op": "http.server"}
        return {"type": "transaction", "transaction": "GET /x", "contexts": {"trace": trace}, "spans": spans}

    def test_a_transaction_at_the_span_limit_is_scrubbed_within_a_text_budget(self, read: list[str]) -> None:
        # Distinct queries of nearly 4 KB, each holding an address: 4 MB of text in all.
        descriptions = [f"SELECT {i} FROM t WHERE email = '{EMAIL}'" + " AND n = $1" * 360 for i in range(self.SPANS)]
        event = self.transaction(descriptions)
        copied = copy.deepcopy(event)
        started = time.thread_time()
        scrubbed = scrub_transaction(copied, {})
        seconds = time.thread_time() - started

        assert sum(map(len, read)) <= MAX_SPAN_TEXT + 64  # the transaction's name and its trace context
        assert seconds < 0.1
        assert_clean(scrubbed)
        spans = scrubbed["spans"]
        assert spans[0]["description"] == descriptions[0].replace(EMAIL, REDACTED)
        assert spans[-1]["description"] == TRUNCATED
        assert spans[-1]["data"] == {"db.system": "postgresql", "thread.name": "MainThread", "url": TRUNCATED}
        for span, original in zip(spans, event["spans"], strict=True):
            # IDs and timestamps stay whole, and text seen before the budget ran out is kept.
            for key in ("trace_id", "span_id", "parent_span_id", "start_timestamp", "timestamp", "op", "status"):
                assert span[key] == original[key]

    def test_text_repeated_across_spans_is_scrubbed_once(self, read: list[str]) -> None:
        """A query run in a loop (an N+1) keeps its description on every span, however long the loop."""
        query = f"SELECT id FROM projects WHERE owner = '{EMAIL}' AND name LIKE $1" + " OR name LIKE $1" * 240
        scrubbed = scrub_transaction(self.transaction([query] * self.SPANS), {})
        expected = query.replace(EMAIL, REDACTED)
        assert [span["description"] for span in scrubbed["spans"]] == [expected] * self.SPANS
        assert read.count(query) == 1


def test_error_events_scrub_urls_and_the_trace_context_like_transactions() -> None:
    event = transaction_event()
    for key in ("type", "spans"):
        event.pop(key)
    event["exception"] = {"values": [{"type": "ValueError", "value": f"failed for {EMAIL}"}]}
    scrubbed = scrub_event(event, {})
    assert_clean(scrubbed)
    assert scrubbed["transaction"] == f"GET /api/v1/invites/{REDACTED}"
    assert scrubbed["request"]["url"] == "https://api.example.com/api/v1/invites"
    assert scrubbed["contexts"]["trace"]["dynamic_sampling_context"]["transaction"] == "GET /sign-up"


def test_error_events_send_no_trace_context_data() -> None:
    """Like any other context's ``data``; only a transaction keeps it (its root span's), scrubbed like a span's."""
    trace = {
        "trace_id": TRACE_ID,
        "span_id": "bf25ad57950b5e81",
        "op": "http.server",
        "data": {"thread.name": "MainThread", "customer": "Jane Q. Public", "note": "https://h/p?opaque=OPAQUE"},
    }
    event = {"exception": {"values": [{"type": "ValueError", "value": "x"}]}, "contexts": {"trace": trace}}
    assert scrub_event(copy.deepcopy(event), {})["contexts"]["trace"] == {**trace, "data": REDACTED}
    transaction = {"type": "transaction", "contexts": {"trace": copy.deepcopy(trace)}, "spans": []}
    assert scrub_transaction(transaction, {})["contexts"]["trace"]["data"] == {
        "thread.name": "MainThread",
        "customer": "Jane Q. Public",
        "note": "https://h/p",
    }


def test_http_breadcrumbs_keep_no_query_string_or_fragment() -> None:
    """The httpx and stdlib integrations record each outgoing request's raw query in a breadcrumb."""
    crumb = {
        "type": "http",
        "category": "httplib",
        "data": {
            "http.method": "GET",
            "url": f"https://api.example.com/v1/items?key={TICKET}",
            "http.query": f"key={TICKET}",
            "http.fragment": TICKET,
            "http.status_code": 200,
            "reason": "OK",
        },
    }
    scrubbed = scrub_breadcrumb(crumb, {})
    assert scrubbed["data"] == {
        "http.method": "GET",
        "url": "https://api.example.com/v1/items",
        "http.status_code": 200,
        "reason": "OK",
    }


class _Capture(Transport):
    def __init__(self) -> None:
        super().__init__()
        self.envelopes: list[Envelope] = []

    def capture_envelope(self, envelope: Envelope) -> None:
        self.envelopes.append(envelope)


def _app(propagated: list[str | None]) -> Any:
    """An ASGI app that records what a request handler can put on a trace: the user, an outgoing call, an error.

    It also records the trace header an outgoing call of each request carries in ``propagated``.
    """

    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        sentry_sdk.set_user({"id": "user_1", "email": EMAIL})
        with sentry_sdk.start_span(op="http.client", name="GET https://api.clerk.com/v1/users") as span:
            span.set_data("url", "https://api.clerk.com/v1/users")
            span.set_data("http.query", f"email_address={EMAIL}")
            span.set_data("http.fragment", TICKET)
            propagated.append(sentry_sdk.get_traceparent())
        sentry_sdk.capture_exception(ValueError(f"lookup failed for {EMAIL}"))
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    return app


# A caller's trace, sent as sampled, whose sampling context names a page with a ticket in its query.
CALLER_SAMPLED = {
    "sentry-trace": f"{TRACE_ID}-b6b1f6f6f6f6f6f6-1",
    "baggage": f"sentry-trace_id={TRACE_ID},sentry-public_key=public,sentry-sampled=true,"
    f"sentry-transaction=GET%20%2Fsign-up%3F__clerk_ticket%3D{TICKET}",
}


def _send_requests(sample_rate: float, *headers: dict[str, str]) -> tuple[list[tuple[Any, str, Any]], list[str | None]]:
    """What the API sends Sentry for one request per ``headers``: each item's envelope headers, type, and payload.

    And the trace header each request's outgoing calls carry. The caller is a
    plain client, as a browser or another service is: with tracing off,
    sentry-sdk's httpx integration makes a client in this process send this
    process's trace header instead of the one it was given.
    """
    transport = _Capture()
    propagated: list[str | None] = []
    options = sentry_options(
        make_settings(error_reporting_mode="sandbox", sentry_dsn=DSN, sentry_traces_sample_rate=sample_rate)
    )
    # No integration, so no library stays patched for the rest of the run.
    sentry_sdk.init(**options, transport=transport, default_integrations=False, integrations=[])
    try:
        client = TestClient(SentryAsgiMiddleware(_app(propagated), transaction_style="url", mechanism_type="test"))
        for request_headers in headers:
            assert client.get(f"/invites/{EMAIL}?__clerk_ticket={TICKET}", headers=request_headers).text == "ok"
        sentry_sdk.flush(timeout=2)
    finally:
        sentry_sdk.get_client().close()
        sentry_sdk.get_global_scope().set_client(None)
    sent = [
        (envelope.headers, item.type, item.payload.json) for envelope in transport.envelopes for item in envelope.items
    ]
    return sent, propagated


def test_at_a_rate_of_0_no_caller_can_have_a_request_traced() -> None:
    """OBS-07: a rate of 0 alone would still trace each request whose caller sends a sampled trace.

    Each request still continues its caller's trace, so its error events link
    to it, and its outgoing calls pass it on as not sampled. A request without
    one starts a trace of its own.
    """
    sent, propagated = _send_requests(0.0, CALLER_SAMPLED, {}, {})
    assert "transaction" not in {kind for _, kind, _ in sent}
    assert_clean(sent)
    traces = [payload["contexts"]["trace"]["trace_id"] for _, kind, payload in sent if kind == "event"]
    assert traces[0] == TRACE_ID
    assert len(set(traces)) == 3
    assert len(propagated) == 3
    for trace, header in zip(traces, propagated, strict=True):
        assert header is not None
        assert header.startswith(f"{trace}-")
        assert header.endswith("-0")


@pytest.mark.parametrize(
    ("headers", "traced"),
    [
        pytest.param({}, True, id="no trace"),
        pytest.param({"sentry-trace": f"{TRACE_ID}-b6b1f6f6f6f6f6f6"}, True, id="a trace without a decision"),
        pytest.param({"sentry-trace": f"{TRACE_ID}-b6b1f6f6f6f6f6f6-0"}, False, id="a trace the caller declined"),
    ],
)
def test_the_api_samples_a_request_unless_its_caller_declined(headers: dict[str, str], traced: bool) -> None:
    """The browser sends no decision (no tracesSampleRate in apps/web/src/instrumentation-client.ts), so
    SENTRY_TRACES_SAMPLE_RATE applies to the requests pages make. A browser rate of 0 would send "-0",
    which no API rate overrides (sentry-sdk inherits the caller's decision)."""
    sent, _ = _send_requests(1.0, headers)
    assert ("transaction" in {kind for _, kind, _ in sent}) is traced


@pytest.mark.parametrize(
    ("sample_rate", "headers"),
    [
        pytest.param(0.01, CALLER_SAMPLED, id="a trace the caller sent as sampled"),
        pytest.param(1.0, {}, id="a new trace"),
    ],
)
def test_a_traced_request_reaches_sentry_scrubbed(sample_rate: float, headers: dict[str, str]) -> None:
    sent, _ = _send_requests(sample_rate, headers)
    assert {"event", "transaction"} <= {kind for _, kind, _ in sent}
    assert_clean(sent)
    [transaction] = [payload for _, kind, payload in sent if kind == "transaction"]
    assert transaction["transaction"] == f"http://testserver/invites/{REDACTED}"
    assert "user" not in transaction
    assert transaction["spans"][0]["data"]["url"] == "https://api.clerk.com/v1/users"
    assert "http.query" not in transaction["spans"][0]["data"]
