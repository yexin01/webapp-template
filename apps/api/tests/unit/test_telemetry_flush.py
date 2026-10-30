"""OBS-06: on a serverless instance, telemetry queued by a request is sent before the instance can idle.

Sentry and PostHog send in the background (PostHog every 5 s), and a serverless platform may freeze the
instance once the invocation ends. A request that queued an error event or an analytics event hands it
to a flush after its response, waits for it at most the budget, and never changes the response
(mocked: capturing transports and a loopback PostHog stub).
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
import sentry_sdk
from fastapi import APIRouter
from fastapi.testclient import TestClient
from sentry_sdk.envelope import Envelope
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.transport import Transport
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.kernel.http import middleware
from app.kernel.http.app import build_fastapi
from app.kernel.infrastructure import observability
from app.kernel.infrastructure.observability import PostHogAnalytics, flush_telemetry, sentry_options
from app.kernel.request_context import note_unsent_telemetry
from tests.conftest import make_settings
from tests.stub_server import stub_server

DSN = "https://public@o0.ingest.example/0"
BUDGET = 0.4


class _Capture(Transport):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[dict[str, Any]] = []

    def capture_envelope(self, envelope: Envelope) -> None:
        self.events += [item.payload.json for item in envelope.items if item.type == "event"]


class Journal:
    """What happened, in order: the response's last body chunk, each flush, and the thread hops."""

    def __init__(self) -> None:
        self.entries: list[str] = []
        self.block = threading.Event()
        self.blocking = False
        self.failing = False

    def flush(self, seconds: float) -> None:
        self.entries.append("flush")
        if self.failing:
            raise RuntimeError("PostHog is down")
        if self.blocking:
            self.block.wait(10)


@pytest.fixture
def journal() -> Iterator[Journal]:
    journal = Journal()
    yield journal
    journal.block.set()


@pytest.fixture(autouse=True)
def short_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(middleware, "TELEMETRY_FLUSH_SECONDS", BUDGET)


@pytest.fixture
def sentry() -> Iterator[_Capture]:
    """Production options with a capturing transport and the logging integration only (no library stays patched)."""
    transport = _Capture()
    options = sentry_options(make_settings(error_reporting_mode="sandbox", sentry_dsn=DSN))
    sentry_sdk.init(**options, transport=transport, default_integrations=False, integrations=[LoggingIntegration()])
    yield transport
    sentry_sdk.get_client().close()
    sentry_sdk.get_global_scope().set_client(None)


def application(journal: Journal, flush: Any = None) -> ASGIApp:
    """The platform app with a few routes, wrapped to note when the last chunk of a response is sent."""
    router = APIRouter()

    @router.get("/ok")
    async def ok() -> dict[str, str]:
        return {"ok": "yes"}

    @router.get("/boom")
    async def boom() -> dict[str, str]:
        raise RuntimeError("unhandled")

    @router.get("/queues-analytics")
    async def queues_analytics() -> dict[str, str]:
        note_unsent_telemetry()
        return {"ok": "yes"}

    app = build_fastapi(
        settings=make_settings(),
        title="t",
        routers=[router],
        worker_threads=4,
        flush_telemetry=flush if flush is not None else journal.flush,
    )

    async def noting(scope: Scope, receive: Receive, send: Send) -> None:
        async def send_noted(message: Message) -> None:
            await send(message)
            if message["type"] == "http.response.body" and not message.get("more_body"):
                journal.entries.append("response sent")

        await app(scope, receive, send_noted)

    return noting


def test_a_request_that_queued_nothing_does_not_flush_or_switch_threads(
    journal: Journal, sentry: _Capture, monkeypatch: pytest.MonkeyPatch
) -> None:
    hops: list[object] = []
    real = middleware.anyio.to_thread.run_sync

    async def counting(*args: Any, **kwargs: Any) -> Any:
        hops.append(args[0])
        return await real(*args, **kwargs)

    monkeypatch.setattr(middleware.anyio.to_thread, "run_sync", counting)
    client = TestClient(application(journal))
    assert client.get("/api/v1/ok").status_code == 200
    assert client.get("/api/v1/missing").status_code == 404  # a 4xx the API chose is not reported
    assert journal.entries == ["response sent", "response sent"]
    assert hops == []


def test_an_error_request_flushes_once_after_its_500_is_sent(journal: Journal, sentry: _Capture) -> None:
    client = TestClient(application(journal), raise_server_exceptions=False)
    response = client.get("/api/v1/boom")
    assert response.status_code == 500
    assert response.json()["code"] == "INTERNAL_ERROR"
    assert journal.entries == ["response sent", "flush"]


def test_a_request_that_queued_analytics_flushes_after_its_response(journal: Journal) -> None:
    client = TestClient(application(journal))
    assert client.get("/api/v1/queues-analytics").status_code == 200
    assert journal.entries == ["response sent", "flush"]


def test_a_flush_that_does_not_return_cannot_hold_the_request_past_the_budget(
    journal: Journal, sentry: _Capture, caplog: pytest.LogCaptureFixture
) -> None:
    journal.blocking = True
    started = time.monotonic()
    with caplog.at_level(logging.WARNING), TestClient(application(journal), raise_server_exceptions=False) as client:
        first = client.get("/api/v1/boom")
        second = client.get("/api/v1/boom")  # the first flush is still sending, so this one does not start another
    elapsed = time.monotonic() - started
    assert first.status_code == second.status_code == 500
    assert elapsed < BUDGET + 1.0
    messages = [r.getMessage() for r in caplog.records]
    assert messages.count("telemetry_flush_incomplete") == 1
    assert messages.count("telemetry_flush_skipped") == 1
    assert journal.entries == ["response sent", "flush", "response sent"]


def test_a_flush_that_fails_leaves_the_response_alone(
    journal: Journal, sentry: _Capture, caplog: pytest.LogCaptureFixture
) -> None:
    journal.failing = True
    client = TestClient(application(journal), raise_server_exceptions=False)
    with caplog.at_level(logging.WARNING):
        response = client.get("/api/v1/boom")
    assert response.status_code == 500
    assert "telemetry_flush_failed" in [r.getMessage() for r in caplog.records]


def test_the_production_flush_sends_what_sentry_and_posthog_queued(
    sentry: _Capture, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = Journal()

    class Idle:
        def flush(self) -> bool:
            journal.entries.append("posthog flush")
            return True

    monkeypatch.setattr(
        observability.sentry_sdk, "flush", lambda timeout: journal.entries.append(f"sentry flush {timeout}")
    )
    app = application(journal, lambda seconds: flush_telemetry(Idle(), seconds))  # type: ignore[arg-type]
    response = TestClient(app, raise_server_exceptions=False).get("/api/v1/boom")
    assert response.status_code == 500
    assert [e["level"] for e in sentry.events] == ["error"]
    assert sentry.events[0]["tags"]["request_id"] == response.headers["x-request-id"]
    assert journal.entries == ["response sent", f"sentry flush {BUDGET}", "posthog flush"]


def test_flushing_never_raises(
    sentry: _Capture, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    class Down:
        def flush(self) -> bool:
            raise RuntimeError("PostHog is down")

    def failing(timeout: float) -> None:
        raise RuntimeError("Sentry is down")

    monkeypatch.setattr(observability.sentry_sdk, "flush", failing)
    with caplog.at_level(logging.WARNING):
        flush_telemetry(Down(), 1.0)  # type: ignore[arg-type]
    assert [r.getMessage() for r in caplog.records] == ["telemetry_flush_failed"] * 2


def test_an_analytics_event_a_request_queued_is_exported_before_the_request_returns() -> None:
    """The production flush against a loopback PostHog: PostHog's own schedule would send it 5 s later."""
    with stub_server(lambda request: (200, {"status": 1})) as posthog:
        analytics = PostHogAnalytics(api_key="phc_test_only", host=posthog.url)
        router = APIRouter()

        @router.get("/checkout")
        async def checkout() -> dict[str, str]:
            analytics.capture(uuid.uuid4(), "checkout_started", {"plan": "pro_monthly"})
            return {"ok": "yes"}

        app = build_fastapi(
            settings=make_settings(),
            title="t",
            routers=[router],
            worker_threads=4,
            flush_telemetry=lambda seconds: flush_telemetry(analytics, seconds),
        )
        try:
            started = time.monotonic()
            assert TestClient(app).get("/api/v1/checkout").status_code == 200
            batches = [r for r in posthog.requests if r.path.startswith("/batch")]
            assert [e["event"] for r in batches for e in r.json()["batch"]] == ["checkout_started"]
            assert time.monotonic() - started < 4  # not the 5 s interval
        finally:
            analytics.shutdown()
