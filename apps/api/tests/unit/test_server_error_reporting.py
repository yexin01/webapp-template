"""OBS-02, OBS-05 to OBS-07: a 5xx the API chose reaches Sentry, scrubbed, once a minute per cause.

A dependency or configuration failure is a ``DomainError`` mapped to 503. Sentry's logging
integration turns only ERROR records into events and its Starlette hook captures only exceptions
with a ``status_code``, so these failures (a revoked Stripe key, a Clerk outage) used to leave no
event at all (mocked: a capturing transport, a loopback Stripe stub).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest
import sentry_sdk
from fastapi import APIRouter
from fastapi.testclient import TestClient
from sentry_sdk.envelope import Envelope
from sentry_sdk.integrations.asgi import SentryAsgiMiddleware
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.transport import Transport

from app.kernel.errors import (
    ConflictError,
    DependencyUnavailableError,
    DomainError,
    NotConfiguredError,
    ProviderRejectedError,
)
from app.kernel.http.app import build_fastapi
from app.kernel.infrastructure import observability
from app.kernel.infrastructure.observability import ErrorThrottle, sentry_options
from app.modules.billing.adapters.stripe import StripeBillingProvider
from app.modules.billing.domain import PlanKey, PriceCatalog
from tests.conftest import make_settings
from tests.stub_server import stub_server

DSN = "https://public@o0.ingest.example/0"
STRIPE_KEY = "sk_" + "live_CANARY1234567890"
TOKEN = "CANARYBEARER0987654321"
QUERY_SECRET = "CANARYQUERY42"
CANARIES = [STRIPE_KEY, TOKEN, QUERY_SECRET]
CATALOG = PriceCatalog({PlanKey.PRO_MONTHLY: "price_m", PlanKey.PRO_YEARLY: "price_y"})


class _Capture(Transport):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[dict[str, Any]] = []

    def capture_envelope(self, envelope: Envelope) -> None:
        self.events += [item.payload.json for item in envelope.items if item.type == "event"]


@dataclass
class Reports:
    transport: _Capture
    now: list[float]

    @property
    def events(self) -> list[dict[str, Any]]:
        sentry_sdk.flush(timeout=2)
        return self.transport.events


@pytest.fixture
def reports(monkeypatch: pytest.MonkeyPatch) -> Iterator[Reports]:
    transport = _Capture()
    now = [1_000.0]
    monkeypatch.setattr(observability, "_server_errors", ErrorThrottle(60.0, 256, lambda: now[0]))
    options = sentry_options(make_settings(error_reporting_mode="sandbox", sentry_dsn=DSN))
    # The logging integration only, so no other library stays patched for the rest of the run.
    sentry_sdk.init(**options, transport=transport, default_integrations=False, integrations=[LoggingIntegration()])
    try:
        yield Reports(transport, now)
    finally:
        sentry_sdk.get_client().close()
        sentry_sdk.get_global_scope().set_client(None)


CAUSES: dict[str, type[Exception]] = {"pool": TimeoutError, "other": ConnectionError}


class RejectedKeyError(DomainError):
    code = "REJECTED_KEY"
    status = 503


def build_client(provider: StripeBillingProvider | None = None) -> TestClient:
    router = APIRouter()

    @router.get("/checkout")
    def checkout() -> None:
        assert provider is not None
        provider.create_customer(uuid.uuid4())

    @router.get("/not-configured")
    def not_configured() -> None:
        raise NotConfiguredError()

    @router.get("/provider-rejected")
    def provider_rejected() -> None:
        raise ProviderRejectedError("Billing is not configured correctly for this Stripe account.")

    @router.get("/conflict")
    def conflict() -> None:
        raise ConflictError()

    @router.get("/module-503")
    def module_503() -> None:
        raise RejectedKeyError()

    @router.get("/cause/{kind}")
    def cause(kind: str) -> None:
        raise DependencyUnavailableError("Billing is temporarily unavailable.") from CAUSES[kind]("down")

    @router.get("/bare")
    def bare() -> None:
        raise DomainError()

    @router.get("/bug")
    def bug() -> None:
        raise RuntimeError("bug")

    app = build_fastapi(settings=make_settings(), title="t", routers=[router], worker_threads=4)
    return TestClient(SentryAsgiMiddleware(app), raise_server_exceptions=False)


def stripe_rejects_the_key(_request: Any) -> tuple[int, dict[str, Any]]:
    message = f"Invalid API Key provided: {STRIPE_KEY}"
    return 401, {"error": {"type": "invalid_request_error", "message": message}}


def test_a_rejected_stripe_key_reaches_sentry_as_one_scrubbed_event_with_the_request_id(reports: Reports) -> None:
    with stub_server(stripe_rejects_the_key) as stub:
        provider = StripeBillingProvider(secret_key=STRIPE_KEY, catalog=CATALOG, api_base=stub.url)
        response = build_client(provider).get(
            f"/api/v1/checkout?token={QUERY_SECRET}", headers={"Authorization": f"Bearer {TOKEN}"}
        )
    assert response.status_code == 503
    assert response.json()["code"] == "NOT_CONFIGURED"
    [event] = reports.events
    assert event["level"] == "error"  # Stripe refuses the key: an operator has to act (an unset secret is a warning)
    assert event["tags"]["error_code"] == "NOT_CONFIGURED"
    assert event["tags"]["request_id"] == response.headers["x-request-id"]
    chain = [(v["module"], v["type"]) for v in event["exception"]["values"]]
    assert any(module.startswith("stripe") and kind == "AuthenticationError" for module, kind in chain), chain
    assert event["request"]["url"].endswith("/api/v1/checkout")
    assert not [canary for canary in CANARIES if canary in json.dumps(event)]


def test_a_repeat_of_the_same_cause_is_sent_once_a_minute(reports: Reports) -> None:
    client = build_client()
    assert client.get("/api/v1/cause/pool").status_code == 503
    assert client.get("/api/v1/cause/pool").status_code == 503
    assert len(reports.events) == 1
    # Another cause is another issue, so it is not held back by the first.
    assert client.get("/api/v1/cause/other").status_code == 503
    assert client.get("/api/v1/not-configured").status_code == 503
    assert len(reports.events) == 3
    reports.now[0] += 61
    assert client.get("/api/v1/cause/pool").status_code == 503
    assert len(reports.events) == 4


def test_not_configured_is_a_warning(reports: Reports) -> None:
    """The public Clerk webhook answers it while its signing secret is unset: worth seeing, not paging."""
    assert build_client().get("/api/v1/not-configured").status_code == 503
    [event] = reports.events
    assert event["level"] == "warning"
    assert event["tags"]["error_code"] == "NOT_CONFIGURED"


def test_a_provider_that_rejects_what_is_configured_is_the_same_answer_reported_as_an_error(reports: Reports) -> None:
    """A secret that is unset waits to be set (a warning); a key the provider refuses needs an operator (an error)."""
    response = build_client().get("/api/v1/provider-rejected")
    assert (response.status_code, response.json()["code"]) == (503, "NOT_CONFIGURED")
    [event] = reports.events
    assert event["level"] == "error"
    assert event["tags"]["error_code"] == "NOT_CONFIGURED"


def test_a_module_error_with_status_503_is_reported_under_its_own_code(reports: Reports) -> None:
    assert build_client().get("/api/v1/module-503").status_code == 503
    [event] = reports.events
    assert event["tags"]["error_code"] == "REJECTED_KEY"


def test_a_bare_domain_error_is_a_500_that_reaches_sentry(reports: Reports) -> None:
    assert build_client().get("/api/v1/bare").status_code == 500
    [event] = reports.events
    assert event["level"] == "error"
    assert event["tags"]["error_code"] == "INTERNAL_ERROR"


def test_an_unexpected_exception_still_reaches_sentry_once(reports: Reports) -> None:
    assert build_client().get("/api/v1/bug").status_code == 500
    [event] = reports.events
    assert event["exception"]["values"][-1]["type"] == "RuntimeError"


def test_a_client_error_is_not_reported(reports: Reports) -> None:
    assert build_client().get("/api/v1/conflict").status_code == 409
    assert reports.events == []


def test_reporting_does_nothing_without_sentry() -> None:
    assert not sentry_sdk.is_initialized()
    client = build_client()
    assert client.get("/api/v1/cause/pool").status_code == 503
    assert client.get("/api/v1/bare").status_code == 500


def test_a_failing_report_never_changes_the_response(reports: Reports, monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("sentry is broken")

    monkeypatch.setattr(sentry_sdk, "capture_exception", explode)
    response = build_client().get("/api/v1/cause/pool")
    assert response.status_code == 503
    assert response.json()["code"] == "DEPENDENCY_UNAVAILABLE"


def test_the_throttle_is_bounded() -> None:
    now = [0.0]
    throttle = ErrorThrottle(60.0, 3, lambda: now[0])
    assert [throttle.allow(("a", "b", str(i))) for i in range(5)] == [True] * 5
    assert len(throttle) <= 3
    assert throttle.allow(("a", "b", "4")) is False
