"""Diagnostics adapters against loopback stubs: no external network access."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from typing import Any

import pytest
import sentry_sdk
from sentry_sdk.envelope import Envelope
from sentry_sdk.transport import Transport

from app.kernel.errors import DependencyUnavailableError, InvalidRequestError
from app.kernel.infrastructure import observability
from app.kernel.infrastructure.observability import PostHogAnalytics, SentryErrorReporter, scrub_event
from app.modules.billing.adapters.stripe import StripeBillingProvider
from app.modules.billing.domain import PlanKey, PriceCatalog
from tests.stub_server import Recorded, stub_server

CATALOG = PriceCatalog({PlanKey.PRO_MONTHLY: "price_m", PlanKey.PRO_YEARLY: "price_y"}, "prod_pro")
FAKE_KEY = "sk_test_" + "0" * 24


def _price(price_id: str, interval: str | None = "month", product: Any = "prod_pro") -> dict[str, Any]:
    return {
        "id": price_id,
        "object": "price",
        "active": True,
        "currency": "eur",
        "livemode": False,
        "product": product,
        "recurring": {"interval": interval} if interval else None,
        "type": "recurring" if interval else "one_time",
    }


class TestStripeDescribePrice:
    def test_maps_price_fields(self) -> None:
        with stub_server(lambda req: (200, _price("price_m", "year"))) as stub:
            provider = StripeBillingProvider(secret_key=FAKE_KEY, catalog=CATALOG, api_base=stub.url)
            price = provider.describe_price("price_m")
        assert (
            price.price_id,
            price.product_id,
            price.active,
            price.recurring_interval,
            price.currency,
            price.livemode,
        ) == ("price_m", "prod_pro", True, "year", "eur", False)
        assert stub.requests[0].path == "/v1/prices/price_m"
        assert stub.requests[0].method == "GET"

    @pytest.mark.parametrize(
        ("product", "expected"),
        [({"id": "prod_x", "object": "product"}, "prod_x"), (None, None)],
        ids=["expanded", "none"],
    )
    def test_the_product_may_come_expanded_or_be_missing(self, product: Any, expected: str | None) -> None:
        with stub_server(lambda req: (200, _price("price_m", product=product))) as stub:
            price = StripeBillingProvider(secret_key=FAKE_KEY, catalog=CATALOG, api_base=stub.url).describe_price(
                "price_m"
            )
        assert price.product_id == expected

    def test_one_time_price_has_no_interval(self) -> None:
        with stub_server(lambda req: (200, _price("price_m", None))) as stub:
            price = StripeBillingProvider(secret_key=FAKE_KEY, catalog=CATALOG, api_base=stub.url).describe_price(
                "price_m"
            )
        assert price.recurring_interval is None

    @pytest.mark.parametrize(
        ("status", "error", "message"),
        [
            (401, DependencyUnavailableError, "rejected the API key"),
            (403, DependencyUnavailableError, "cannot read prices"),
            (404, InvalidRequestError, "has no price price_x"),
        ],
    )
    def test_errors_are_specific_and_never_echo_the_key(self, status: int, error: type, message: str) -> None:
        body = {"error": {"type": "invalid_request_error", "message": f"key {FAKE_KEY} problem"}}
        with stub_server(lambda req: (status, body)) as stub:
            provider = StripeBillingProvider(secret_key=FAKE_KEY, catalog=CATALOG, api_base=stub.url)
            with pytest.raises(error) as caught:
                provider.describe_price("price_x")
        assert message in caught.value.public_message
        assert FAKE_KEY not in caught.value.public_message


class TestPostHogFlush:
    def test_successful_export_sends_only_allowlisted_properties(self) -> None:
        with stub_server(lambda req: (200, {"status": 1})) as stub:
            analytics = PostHogAnalytics(api_key="phc_test_only", host=stub.url, flush_timeout_seconds=5)
            user = uuid.uuid4()
            analytics.capture(user, "integration_test_sent", {"source": "api", "email": "a@example.com"})
            assert analytics.flush() is True
            analytics.shutdown()
        batches = [r.json() for r in stub.requests if r.method == "POST"]
        events = [e for b in batches for e in b.get("batch", [])]
        sent = [e for e in events if e.get("event") == "integration_test_sent"]
        assert len(sent) == 1
        assert sent[0]["distinct_id"] == str(user)
        assert sent[0]["properties"]["source"] == "api"
        assert "email" not in sent[0]["properties"]
        assert "a@example.com" not in str(batches)

    def test_failed_export_is_reported(self) -> None:
        with stub_server(lambda req: (500, {"error": "down"})) as stub:
            analytics = PostHogAnalytics(api_key="phc_test_only", host=stub.url, flush_timeout_seconds=10)
            analytics.capture(uuid.uuid4(), "integration_test_sent", {"source": "api"})
            started = time.monotonic()
            assert analytics.flush() is False
            assert time.monotonic() - started < 15
            analytics.shutdown()

    def test_unanswered_export_is_not_reported_as_accepted(self, caplog: pytest.LogCaptureFixture) -> None:
        """A flush that runs out of time while PostHog has not answered confirms nothing."""
        arrived, answer = threading.Event(), threading.Event()

        def stalled(_request: Recorded) -> tuple[int, Any]:
            arrived.set()
            answer.wait(10)
            return 200, {"status": 1}

        with stub_server(stalled) as stub:
            analytics = PostHogAnalytics(api_key="phc_test_only", host=stub.url, flush_timeout_seconds=0.5)
            analytics.capture(uuid.uuid4(), "integration_test_sent", {"source": "api"})
            try:
                with caplog.at_level(logging.WARNING, logger=observability.__name__):
                    assert analytics.flush() is False
            finally:
                answer.set()
                analytics.shutdown()
        assert arrived.is_set()  # the stub was reached: PostHog just had not answered yet
        assert "analytics_flush_unconfirmed" in [r.getMessage() for r in caplog.records]

    def test_events_queued_after_a_confirmed_flush_do_not_fail_it(self) -> None:
        """Another request's event, captured once the flush has drained, is not this flush's work."""
        with stub_server(lambda req: (200, {"status": 1})) as stub:
            analytics = PostHogAnalytics(api_key="phc_test_only", host=stub.url, flush_timeout_seconds=5)
            analytics.capture(uuid.uuid4(), "integration_test_sent", {"source": "api"})
            drain = analytics._client.flush

            def drain_then_capture(timeout_seconds: float) -> None:
                drain(timeout_seconds=timeout_seconds)
                analytics.capture(uuid.uuid4(), "project_created", {"context": "personal"})

            analytics._client.flush = drain_then_capture
            try:
                assert analytics.flush() is True
            finally:
                del analytics._client.flush
                analytics.shutdown()

    def test_non_allowlisted_event_is_never_sent(self) -> None:
        with stub_server(lambda req: (200, {"status": 1})) as stub:
            analytics = PostHogAnalytics(api_key="phc_test_only", host=stub.url)
            analytics.capture(uuid.uuid4(), "user_email_changed", {"source": "api"})
            assert analytics.flush() is True
            analytics.shutdown()
        assert "user_email_changed" not in str([r.body for r in stub.requests])


class _CapturingTransport(Transport):
    def __init__(self) -> None:
        super().__init__()
        self.envelopes: list[Envelope] = []

    def capture_envelope(self, envelope: Envelope) -> None:
        self.envelopes.append(envelope)


class TestSentryErrorReporter:
    def test_returns_none_when_sentry_is_not_initialized(self) -> None:
        assert not sentry_sdk.is_initialized()
        assert SentryErrorReporter().send_test_event() is None

    def test_sends_scrubbed_synthetic_error(self) -> None:
        transport = _CapturingTransport()
        sentry_sdk.init(
            dsn="https://public@sentry.invalid/1",
            transport=transport,
            before_send=scrub_event,
            default_integrations=False,
        )
        try:
            event_id = SentryErrorReporter(flush_timeout_seconds=1).send_test_event()
        finally:
            sentry_sdk.get_client().close()
            sentry_sdk.get_global_scope().set_client(None)
        assert event_id
        events = [item.payload.json for env in transport.envelopes for item in env.items if item.type == "event"]
        assert len(events) == 1
        event = events[0]
        assert event["event_id"] == event_id
        assert event["exception"]["values"][0]["type"] == "IntegrationTestError"
        assert event["tags"]["integration_test"] == "true"
        assert "user" not in event
        assert not sentry_sdk.is_initialized()
