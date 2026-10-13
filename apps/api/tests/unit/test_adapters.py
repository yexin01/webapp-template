"""Adapter behavior: Stripe mapping, webhook signatures, LLM retries, redaction."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

import httpx
import pytest

from app.domain.catalog import PriceCatalog
from app.domain.entitlements import PlanKey, SubscriptionStatus
from app.domain.errors import WebhookSignatureError
from app.infrastructure.llm_providers import AnthropicProvider, OpenAIProvider
from app.infrastructure.observability import scrub_event
from app.infrastructure.redaction import REDACTED, redact, redact_text
from app.infrastructure.stripe_billing import StripeWebhookVerifier, to_snapshot

SECRET = "whsec_test_only_not_a_real_secret_000000"
CATALOG = PriceCatalog({PlanKey.PRO_MONTHLY: "price_m", PlanKey.PRO_YEARLY: "price_y"})


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


class TestLLMProviders:
    def make(self, cls: Any, handler: Any) -> Any:
        provider = cls(api_key="sk-test-key-not-real-000", model="model-x", timeout_seconds=1.0, sleep=lambda _s: None)
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

    def test_unavailable_is_retried_at_most_twice(self) -> None:
        calls = 0

        def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(503)

        result = self.make(OpenAIProvider, handler).check()
        assert result.error_code == "unavailable"
        assert calls == 3

    def test_timeout_normalized(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("slow", request=request)

        assert self.make(AnthropicProvider, handler).check().error_code == "timeout"


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
        }
        scrubbed = json.dumps(scrub_event(sentry_event, None))
        for canary in CANARIES:
            assert canary not in scrubbed


def test_sentry_collects_no_personal_or_request_data() -> None:
    from app.infrastructure.observability import scrub_breadcrumb, sentry_options
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
