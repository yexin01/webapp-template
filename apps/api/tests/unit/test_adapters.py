"""Adapter behavior: Stripe mapping, webhook signatures, LLM retries, redaction."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import sentry_sdk
from sentry_sdk.envelope import Envelope
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.transport import Transport

from app.kernel.errors import InvalidRequestError, WebhookSignatureError
from app.kernel.infrastructure.observability import scrub_event, sentry_options
from app.kernel.infrastructure.redaction import REDACTED, redact, redact_text
from app.modules.billing.adapters.stripe import StripeWebhookVerifier, to_snapshot
from app.modules.billing.domain import PlanKey, PriceCatalog, SubscriptionStatus
from app.modules.llm import adapters as llm_adapters
from app.modules.llm.adapters import AnthropicProvider, OpenAIProvider
from tests.conftest import make_settings

SECRET = "whsec_test_only_not_a_real_secret_000000"
PRODUCT = "prod_pro"
CATALOG = PriceCatalog({PlanKey.PRO_MONTHLY: "price_m", PlanKey.PRO_YEARLY: "price_y"}, PRODUCT)


def sign(payload: bytes, secret: str = SECRET, timestamp: int | None = None) -> str:
    ts = timestamp if timestamp is not None else int(time.time())
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


def event(event_type: str = "customer.subscription.updated", customer: str = "cus_1") -> bytes:
    return json.dumps(
        {
            "id": "evt_1",
            "object": "event",
            "type": event_type,
            "created": 1_780_000_000,
            "data": {"object": {"id": "sub_1", "object": "subscription", "customer": customer}},
        }
    ).encode()


THIN = json.dumps(
    {
        "id": "evt_thin",
        "object": "v2.core.event",
        "type": "v1.customer.subscription.updated",
        "created": "2026-10-30T10:00:00.000Z",
        "related_object": {"id": "sub_1", "type": "subscription", "url": "/v1/subscriptions/sub_1"},
    }
).encode()


class TestStripeWebhookVerifier:
    def test_valid_signature(self) -> None:
        payload = event()
        parsed = StripeWebhookVerifier(SECRET).verify(payload, sign(payload))
        assert parsed.event_id == "evt_1"
        assert parsed.customer_id == "cus_1"

    def test_customer_event_uses_object_id(self) -> None:
        payload = json.dumps(
            {
                "id": "evt_2",
                "object": "event",
                "type": "customer.updated",
                "created": 1,
                "data": {"object": {"id": "cus_9", "object": "customer"}},
            }
        ).encode()
        assert StripeWebhookVerifier(SECRET).verify(payload, sign(payload)).customer_id == "cus_9"

    @pytest.mark.parametrize(
        "header_factory",
        [
            lambda p: None,
            lambda p: "",
            lambda p: sign(p, secret="whsec_wrong_secret_000000000000"),
            lambda p: sign(p, timestamp=int(time.time()) - 3600),  # replay outside tolerance
            lambda p: sign(p + b" "),  # body altered after signing
            lambda p: "t=1,v1=deadbeef",
        ],
    )
    def test_invalid_signatures(self, header_factory: Any) -> None:
        payload = event()
        with pytest.raises(WebhookSignatureError):
            StripeWebhookVerifier(SECRET).verify(payload, header_factory(payload))

    def test_a_signed_thin_payload_is_named_not_called_forged(self, caplog: pytest.LogCaptureFixture) -> None:
        """A destination created with the Thin payload format delivers v2 notifications this API cannot read."""
        payload = THIN
        with caplog.at_level(logging.WARNING), pytest.raises(InvalidRequestError, match="Snapshot") as caught:
            StripeWebhookVerifier(SECRET).verify(payload, sign(payload))
        assert not isinstance(caught.value, WebhookSignatureError)
        [record] = [r for r in caplog.records if r.name == "app.modules.billing.adapters.stripe"]
        assert (record.levelno, record.getMessage()) == (logging.ERROR, "stripe_webhook_unsupported_payload")
        assert (record.object, record.event_type) == ("v2.core.event", "v1.customer.subscription.updated")  # type: ignore[attr-defined]
        assert "evt_thin" not in caplog.text  # never the body

    @pytest.mark.parametrize(
        "header_factory",
        [
            lambda p: None,
            lambda p: sign(p, secret="whsec_wrong_secret_000000000000"),
            lambda p: sign(p, timestamp=int(time.time()) - 3600),
            lambda p: sign(p + b" "),
        ],
    )
    def test_a_thin_payload_without_a_valid_signature_is_still_a_forgery(
        self, header_factory: Any, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING), pytest.raises(WebhookSignatureError):
            StripeWebhookVerifier(SECRET).verify(THIN, header_factory(THIN))
        assert [r.levelno for r in caplog.records] == [logging.WARNING]  # anyone can send these, so never an error

    @pytest.mark.parametrize("body", [b"not json", b"[1]", b'"event"', b"{}", b'{"object": "list"}'])
    def test_a_signed_body_that_is_not_an_event_is_unsupported(self, body: bytes) -> None:
        with pytest.raises(InvalidRequestError, match="Snapshot"):
            StripeWebhookVerifier(SECRET).verify(body, sign(body))


class TestSubscriptionMapping:
    def raw(self, **kw: Any) -> dict[str, Any]:
        base: dict[str, Any] = {
            "id": "sub_1",
            "customer": "cus_1",
            "status": "active",
            "cancel_at_period_end": False,
            "cancel_at": None,
            "ended_at": None,
            "items": {"data": [{"price": {"id": "price_y"}, "current_period_end": 1_790_000_000}]},
        }
        base.update(kw)
        return base

    def test_period_end_from_item(self) -> None:
        snap = to_snapshot(self.raw(), CATALOG)
        assert snap.plan_key is PlanKey.PRO_YEARLY
        assert snap.current_period_end is not None
        assert int(snap.current_period_end.timestamp()) == 1_790_000_000

    def test_unknown_price_is_unknown_plan(self) -> None:
        raw = self.raw(items={"data": [{"price": {"id": "price_other"}, "current_period_end": 1}]})
        assert to_snapshot(raw, CATALOG).plan_key is PlanKey.UNKNOWN

    def test_unknown_status_is_never_entitled(self) -> None:
        assert to_snapshot(self.raw(status="something_new"), CATALOG).status is SubscriptionStatus.INCOMPLETE

    @staticmethod
    def priced(
        price_id: str, product: str | dict[str, Any] | None = PRODUCT, interval: str = "month", count: int | None = 1
    ) -> dict[str, Any]:
        """A subscription item as Stripe returns it: the full price, with its product and recurring terms."""
        recurring = {"interval": interval} if count is None else {"interval": interval, "interval_count": count}
        price = {"id": price_id, "product": product, "recurring": recurring}
        return {"data": [{"price": price, "current_period_end": 1}]}

    def test_an_older_price_of_the_pro_product_keeps_its_plan_but_is_not_sold(self) -> None:
        monthly = self.raw(items=self.priced("price_old"))
        yearly = self.raw(items=self.priced("price_old_y", interval="year"))
        assert to_snapshot(monthly, CATALOG).plan_key is PlanKey.PRO_MONTHLY
        assert to_snapshot(yearly, CATALOG).plan_key is PlanKey.PRO_YEARLY
        assert to_snapshot(monthly, CATALOG).price_id == "price_old"
        assert CATALOG.price_for(PlanKey.PRO_MONTHLY) == "price_m"  # checkout still sells only the current price
        # Without the product a price change drops Pro, which is what the product setting prevents.
        assert to_snapshot(monthly, PriceCatalog(CATALOG.prices)).plan_key is PlanKey.UNKNOWN

    def test_the_product_may_come_expanded(self) -> None:
        raw = self.raw(items=self.priced("price_old", product={"id": PRODUCT, "object": "product"}))
        assert to_snapshot(raw, CATALOG).plan_key is PlanKey.PRO_MONTHLY

    @pytest.mark.parametrize(
        ("product", "interval", "count"),
        [
            ("prod_other", "month", 1),  # another product
            (None, "month", 1),
            (PRODUCT, "week", 1),
            (PRODUCT, "month", 3),  # every three months
            (PRODUCT, "year", 2),
            (PRODUCT, "month", None),  # no interval count: not assumed to be one
        ],
    )
    def test_any_other_price_is_unknown(self, product: str | None, interval: str, count: int | None) -> None:
        raw = self.raw(items=self.priced("price_old", product, interval, count))
        assert to_snapshot(raw, CATALOG).plan_key is PlanKey.UNKNOWN

    def test_a_price_with_no_recurring_terms_is_unknown(self) -> None:
        one_time = {"data": [{"price": {"id": "price_once", "product": PRODUCT, "recurring": None}}]}
        assert to_snapshot(self.raw(items=one_time), CATALOG).plan_key is PlanKey.UNKNOWN

    def test_the_plan_comes_from_the_item_of_the_pro_product(self) -> None:
        addon = {"price": {"id": "price_addon", "product": "prod_addon", "recurring": {"interval": "month"}}}
        older = {
            "price": {"id": "price_old", "product": PRODUCT, "recurring": {"interval": "year", "interval_count": 1}},
            "current_period_end": 1_790_000_000,
        }
        snap = to_snapshot(self.raw(items={"data": [addon, older]}), CATALOG)
        assert (snap.plan_key, snap.price_id) == (PlanKey.PRO_YEARLY, "price_old")
        assert snap.current_period_end is not None
        assert int(snap.current_period_end.timestamp()) == 1_790_000_000

    def test_the_plan_comes_from_the_item_with_a_known_price(self) -> None:
        raw = self.raw(
            items={
                "data": [
                    {"price": {"id": "price_addon"}, "current_period_end": 1},
                    {"price": {"id": "price_y"}, "current_period_end": 1_790_000_000},
                ]
            }
        )
        snap = to_snapshot(raw, CATALOG)
        assert (snap.plan_key, snap.price_id) == (PlanKey.PRO_YEARLY, "price_y")
        assert snap.current_period_end is not None
        assert int(snap.current_period_end.timestamp()) == 1_790_000_000


class TestLLMProviders:
    def make(self, cls: Any, handler: Any, timeout: float = 30.0) -> Any:
        provider = cls(
            api_key="sk-test-key-not-real-000", model="model-x", timeout_seconds=timeout, sleep=lambda _s: None
        )
        provider._client = httpx.Client(base_url="https://llm.invalid", transport=httpx.MockTransport(handler))
        return provider

    @pytest.mark.parametrize("cls", [AnthropicProvider, OpenAIProvider])
    def test_success_uses_model_metadata_endpoint(self, cls: Any) -> None:
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.path)
            return httpx.Response(200, json={"id": "model-x"})

        result = self.make(cls, handler).check()
        assert result.ok
        assert seen == ["/v1/models/model-x"]

    def test_auth_failure_is_not_retried(self) -> None:
        calls = 0

        def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(401)

        result = self.make(AnthropicProvider, handler).check()
        assert not result.ok
        assert result.error_code == "auth_failed"
        assert calls == 1

    @pytest.mark.parametrize(
        ("response", "calls", "error"),
        [
            # Retried only with retry guidance short enough to honour (LLM-03).
            (lambda: httpx.Response(503, headers={"retry-after": "1"}), 3, "unavailable"),
            (lambda: httpx.Response(429, headers={"retry-after": "2"}), 3, "rate_limited"),
            (lambda: httpx.Response(429, headers={"retry-after": "60"}), 1, "rate_limited"),
            (lambda: httpx.Response(503), 1, "unavailable"),
            (lambda: httpx.Response(500), 1, "unavailable"),
            (lambda: httpx.Response(404), 1, "invalid_request"),
            # Retry-After takes ASCII digits only: other digits are no guidance, never an error.
            (lambda: httpx.Response(429, headers=[(b"retry-after", b"\xb2")]), 1, "rate_limited"),
            (lambda: httpx.Response(503, headers=[(b"retry-after", "\u0663".encode())]), 1, "unavailable"),
        ],
    )
    def test_retries_only_with_retry_guidance(self, response: Any, calls: int, error: str) -> None:
        seen = 0

        def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal seen
            seen += 1
            return response()

        result = self.make(OpenAIProvider, handler).check()
        assert (seen, result.error_code) == (calls, error)

    def test_retries_fit_inside_the_checks_own_timeout(self) -> None:
        """LLM-03: a retry whose wait cannot fit in LLM_TIMEOUT_SECONDS is not made."""
        timeouts: list[float] = []

        def handler(request: httpx.Request) -> httpx.Response:
            timeouts.append(request.extensions["timeout"]["read"])
            return httpx.Response(429, headers={"retry-after": "2"})

        result = self.make(OpenAIProvider, handler, timeout=1.0).check()
        assert (len(timeouts), result.error_code) == (1, "rate_limited")  # a 2 s wait cannot fit in 1 s
        assert all(t <= 1.0 for t in timeouts)

    def test_a_retry_gets_only_what_is_left_of_the_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """LLM-03: attempts share LLM_TIMEOUT_SECONDS, so a late retry cannot run a full timeout past it."""
        now = [1_000.0]
        monkeypatch.setattr(llm_adapters, "time", SimpleNamespace(monotonic=lambda: now[0]))
        timeouts: list[dict[str, float]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            timeouts.append(request.extensions["timeout"])
            if len(timeouts) == 1:
                now[0] += 25.0  # the first attempt used 25 s of the 30 s budget
                return httpx.Response(503, headers={"retry-after": "1"})
            return httpx.Response(200)

        def sleep(seconds: float) -> None:
            now[0] += seconds

        provider = OpenAIProvider(
            api_key="sk-test-key-not-real-000", model="model-x", timeout_seconds=30.0, sleep=sleep
        )
        provider._client = httpx.Client(base_url="https://llm.invalid", transport=httpx.MockTransport(handler))
        assert provider.check().ok
        left = 30.0 - 25.0 - 1.0
        assert [t["read"] for t in timeouts] == [pytest.approx(30.0), pytest.approx(left)]
        assert all(timeouts[1][phase] == pytest.approx(left) for phase in ("connect", "write", "pool"))

    def test_the_response_body_is_never_read(self) -> None:
        """A slow or endless body cannot hold the check: only the status and headers matter."""

        class EndlessBody(httpx.SyncByteStream):
            def __iter__(self) -> Any:
                raise AssertionError("the body was read")

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, stream=EndlessBody())

        assert self.make(AnthropicProvider, handler).check().ok

    def test_connection_failures_before_sending_are_retried(self) -> None:
        seen = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal seen
            seen += 1
            raise httpx.ConnectError("refused", request=request)

        assert self.make(AnthropicProvider, handler).check().error_code == "unavailable"
        assert seen == 3

    def test_timeout_normalized_and_never_retried(self) -> None:
        """A read timeout may mean the provider accepted the request."""
        seen = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal seen
            seen += 1
            raise httpx.ReadTimeout("slow", request=request)

        assert self.make(AnthropicProvider, handler).check().error_code == "timeout"
        assert seen == 1


class _EventCapture(Transport):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[dict[str, Any]] = []

    def capture_envelope(self, envelope: Envelope) -> None:
        self.events += [item.payload.json for item in envelope.items if item.type == "event"]


CANARIES = [
    "sk_" + "live_CANARY1234567890",
    "sk_test_CANARY1234567890",
    "whsec_CANARY1234567890",
    "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJjYW5hcnkifQ.c2lnbmF0dXJlY2FuYXJ5",
    "canary.person@example.com",
]


class TestRedaction:
    @pytest.mark.parametrize("canary", CANARIES)
    def test_text_patterns(self, canary: str) -> None:
        assert canary not in redact_text(f"value={canary} trailing")

    def test_dsn_password(self) -> None:
        assert "hunter2" not in redact_text("postgresql://user:hunter2@db.example:5432/app")

    def test_sensitive_keys(self) -> None:
        out = redact({"Authorization": "Bearer abc", "nested": {"prompt": "secret plan", "ok": "fine"}})
        assert out["Authorization"] == REDACTED
        assert out["nested"]["prompt"] == REDACTED
        assert out["nested"]["ok"] == "fine"

    def test_sentry_event_scrubbed(self) -> None:
        sentry_event: dict[str, Any] = {
            "request": {
                "headers": {"Authorization": f"Bearer {CANARIES[3]}", "Stripe-Signature": "t=1,v1=x", "Accept": "x"},
                "cookies": {"__session": CANARIES[3]},
                "data": {"email": CANARIES[4]},
                "query_string": f"token={CANARIES[0]}",
            },
            "user": {"email": CANARIES[4]},
            "exception": {
                "values": [
                    {
                        "value": f"failed for {CANARIES[4]} with {CANARIES[1]}",
                        "stacktrace": {"frames": [{"vars": {"key": CANARIES[2]}}]},
                    }
                ]
            },
            "extra": {"api_key": CANARIES[0]},
            # From a log record: the template, the text with its arguments filled in, and the arguments.
            "logentry": {
                "message": f"upload failed for %s ({CANARIES[2]})",
                "formatted": f"upload failed for {CANARIES[4]} ({CANARIES[2]})",
                "params": [CANARIES[4]],
            },
        }
        scrubbed = json.dumps(scrub_event(sentry_event, None))
        for canary in CANARIES:
            assert canary not in scrubbed

    def test_logged_error_reaches_sentry_scrubbed(self) -> None:
        """OBS-02: an ERROR log becomes an event whose filled-in message carries the log's arguments."""
        transport = _EventCapture()
        options = sentry_options(
            make_settings(error_reporting_mode="sandbox", sentry_dsn="https://public@o0.ingest.example/0")
        )
        # Only the logging integration, so no other library stays patched for the rest of the run.
        sentry_sdk.init(**options, transport=transport, default_integrations=False, integrations=[LoggingIntegration()])
        try:
            logging.getLogger("tests.library").error("upload failed for %s with key %s", CANARIES[4], CANARIES[0])
            sentry_sdk.flush(timeout=2)
        finally:
            sentry_sdk.get_client().close()
            sentry_sdk.get_global_scope().set_client(None)
        [event] = transport.events
        assert event["logentry"] == {
            "message": "upload failed for %s with key %s",
            "formatted": f"upload failed for {REDACTED} with key {REDACTED}",
        }
        assert not [canary for canary in CANARIES if canary in json.dumps(event)]

    def test_sentry_event_carries_the_request_id(self) -> None:
        """OBS-05: an operator can go from a response's X-Request-ID to the error event."""
        from app.kernel.request_context import request_id_var

        token = request_id_var.set("req-1234567890")
        try:
            event = scrub_event({"tags": {"handler": "x"}}, None)
        finally:
            request_id_var.reset(token)
        assert event["tags"] == {"handler": "x", "request_id": "req-1234567890"}


def test_sentry_collects_no_personal_or_request_data() -> None:
    from app.kernel.infrastructure.observability import scrub_breadcrumb, sentry_options
    from tests.conftest import make_settings

    options = sentry_options(
        make_settings(error_reporting_mode="sandbox", sentry_dsn="https://public@o0.ingest.example/0")
    )
    collection = options["data_collection"]
    assert collection["user_info"] is False
    assert collection["http_bodies"] == []
    for key in ("cookies", "url_query_params"):
        assert collection[key] == {"mode": "off"}
    assert collection["http_headers"] == {"request": {"mode": "off"}}
    assert collection["stack_frame_variables"] is False
    assert options["before_send"] is scrub_event
    assert options["before_breadcrumb"] is scrub_breadcrumb
