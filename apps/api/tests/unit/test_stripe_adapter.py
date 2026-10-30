"""The Stripe billing adapter against a loopback stub: the real SDK, no network access (mocked).

These pin the boundary the billing service relies on: what each call sends to
Stripe, how answers map to domain values, and which failures are rejections
(``InvalidRequestError``: retrying cannot help) and which are outages
(``DependencyUnavailableError``: a 503, retried later).
"""

from __future__ import annotations

import logging
import socket
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, ClassVar
from urllib.parse import parse_qs, urlsplit

import pytest

from app.kernel.errors import DependencyUnavailableError, ForbiddenError, InvalidRequestError, NotConfiguredError
from app.modules.billing.adapters.stripe import StripeBillingProvider
from app.modules.billing.domain import (
    PlanKey,
    PortalConfiguration,
    PriceCatalog,
    SubscriptionSnapshot,
    SubscriptionStatus,
    portal_problems,
)
from app.modules.billing.ports import CheckoutSession
from tests.stub_server import Recorded, stub_server

CATALOG = PriceCatalog({PlanKey.PRO_MONTHLY: "price_m", PlanKey.PRO_YEARLY: "price_y"})
FAKE_KEY = "sk_test_" + "0" * 24
USER = uuid.UUID("2f1a6a3e-8f4b-4f57-9a43-3c1b1f0e9d21")
PERIOD_END = 1_900_000_000
MISSING = {
    "error": {"type": "invalid_request_error", "code": "resource_missing", "message": "No such customer: 'cus_1'"}
}

Read = Callable[[StripeBillingProvider], object]
READS: dict[str, Read] = {
    "list": lambda p: p.list_customer_subscriptions("cus_1"),
    "retrieve": lambda p: p.retrieve_subscription("sub_1"),
    "customer": lambda p: p.get_customer_app_user_id("cus_1"),
}


def provider(url: str) -> StripeBillingProvider:
    return StripeBillingProvider(secret_key=FAKE_KEY, catalog=CATALOG, api_base=url)


def form(request: Recorded) -> dict[str, list[str]]:
    """The form-encoded body the SDK posts."""
    return parse_qs(request.body.decode())


def subscription(status: str = "active", price: str = "price_m", **fields: Any) -> dict[str, Any]:
    item = {"id": "si_1", "object": "subscription_item", "price": {"id": price, "object": "price"}}
    return {
        "id": "sub_1",
        "object": "subscription",
        "customer": "cus_1",
        "status": status,
        "cancel_at_period_end": False,
        "cancel_at": None,
        "ended_at": None,
        "items": {"object": "list", "data": [{**item, "current_period_end": PERIOD_END}]},
        **fields,
    }


class TestRequests:
    def test_a_checkout_session_sells_one_of_the_given_price_to_the_given_customer(self) -> None:
        session = {
            "id": "cs_1",
            "object": "checkout.session",
            "url": "https://checkout.stripe.com/c/pay/cs_1",
            "expires_at": PERIOD_END,
        }
        with stub_server(lambda req: (200, session)) as stub:
            created = provider(stub.url).create_checkout_session(
                customer_id="cus_1",
                price_id="price_y",
                user_id=USER,
                success_url="https://app.test/billing/success?session_id={CHECKOUT_SESSION_ID}",
                cancel_url="https://app.test/billing?checkout=canceled",
                idempotency_key="checkout-key-1",
            )
        assert created == CheckoutSession(
            "cs_1", "https://checkout.stripe.com/c/pay/cs_1", datetime.fromtimestamp(PERIOD_END, UTC)
        )
        [request] = stub.requests
        assert (request.method, request.path) == ("POST", "/v1/checkout/sessions")
        assert form(request) == {
            "mode": ["subscription"],
            "customer": ["cus_1"],
            "client_reference_id": [str(USER)],
            "line_items[0][price]": ["price_y"],
            "line_items[0][quantity]": ["1"],
            "success_url": ["https://app.test/billing/success?session_id={CHECKOUT_SESSION_ID}"],
            "cancel_url": ["https://app.test/billing?checkout=canceled"],
            "subscription_data[metadata][app_user_id]": [str(USER)],
        }
        assert request.headers["idempotency-key"] == "checkout-key-1"

    def test_a_checkout_session_without_a_url_is_an_outage(self) -> None:
        session = {"id": "cs_1", "object": "checkout.session", "url": None}
        with stub_server(lambda req: (200, session)) as stub, pytest.raises(DependencyUnavailableError):
            provider(stub.url).create_checkout_session(
                customer_id="cus_1",
                price_id="price_m",
                user_id=USER,
                success_url="https://app.test/s",
                cancel_url="https://app.test/c",
                idempotency_key="checkout-key-1",
            )

    def test_a_customer_carries_only_the_user_reference_and_is_created_once_per_user(self) -> None:
        with stub_server(lambda req: (200, {"id": "cus_new", "object": "customer"})) as stub:
            assert provider(stub.url).create_customer(USER) == "cus_new"
        [request] = stub.requests
        assert (request.method, request.path) == ("POST", "/v1/customers")
        assert form(request) == {"metadata[app_user_id]": [str(USER)]}
        assert request.headers["idempotency-key"] == f"customer-create-{USER}"

    def test_a_portal_session_returns_to_the_given_url(self) -> None:
        session = {
            "id": "bps_1",
            "object": "billing_portal.session",
            "url": "https://billing.stripe.com/p/session/bps_1",
        }
        with stub_server(lambda req: (200, session)) as stub:
            url = provider(stub.url).create_portal_session(customer_id="cus_1", return_url="https://app.test/billing")
        assert url == "https://billing.stripe.com/p/session/bps_1"
        [request] = stub.requests
        assert (request.method, request.path) == ("POST", "/v1/billing_portal/sessions")
        assert form(request) == {"customer": ["cus_1"], "return_url": ["https://app.test/billing"]}


class TestCheckoutSessionLifecycle:
    """DEC-013: a session the user could still complete is closed at Stripe before another is created."""

    OPEN: ClassVar[dict[str, Any]] = {
        "id": "cs_1",
        "object": "checkout.session",
        "status": "open",
        "url": "https://checkout.stripe.com/c/pay/cs_1",
    }

    def test_a_session_is_expired_by_id_with_no_other_input(self) -> None:
        with stub_server(lambda req: (200, {**self.OPEN, "status": "expired", "url": None})) as stub:
            provider(stub.url).expire_checkout_session("cs_1")
        [request] = stub.requests
        assert (request.method, request.path, request.body) == ("POST", "/v1/checkout/sessions/cs_1/expire", b"")

    @pytest.mark.parametrize("status", [400, 404])
    def test_a_session_that_is_not_open_any_more_is_a_rejection(self, status: int) -> None:
        body = {"error": {"type": "invalid_request_error", "message": "Only open sessions can be expired."}}
        with stub_server(lambda req: (status, body)) as stub, pytest.raises(InvalidRequestError):
            provider(stub.url).expire_checkout_session("cs_1")
        assert len(stub.requests) == 1

    def test_an_outage_while_expiring_is_an_outage(self) -> None:
        body = {"error": {"type": "api_error", "message": "down"}}
        with stub_server(lambda req: (500, body)) as stub, pytest.raises(DependencyUnavailableError):
            provider(stub.url).expire_checkout_session("cs_1")

    def test_an_open_session_gives_its_url_and_a_closed_one_does_not(self) -> None:
        with stub_server(lambda req: (200, self.OPEN)) as stub:
            assert provider(stub.url).open_checkout_session_url("cs_1") == "https://checkout.stripe.com/c/pay/cs_1"
        assert (stub.requests[0].method, stub.requests[0].path) == ("GET", "/v1/checkout/sessions/cs_1")
        for closed in ("complete", "expired"):
            body = {**self.OPEN, "status": closed, "url": None}
            with stub_server(lambda req, body=body: (200, body)) as stub:
                assert provider(stub.url).open_checkout_session_url("cs_1") is None

    def test_a_url_on_another_host_is_never_returned(self) -> None:
        body = {**self.OPEN, "url": "https://evil.test/c/pay/cs_1"}
        with stub_server(lambda req: (200, body)) as stub, pytest.raises(NotConfiguredError):
            provider(stub.url).open_checkout_session_url("cs_1")


class TestSubscriptionReads:
    def test_listing_reads_every_status_and_maps_each_subscription(self) -> None:
        listing = {"object": "list", "url": "/v1/subscriptions", "has_more": False, "data": [subscription()]}
        with stub_server(lambda req: (200, listing)) as stub:
            subs = provider(stub.url).list_customer_subscriptions("cus_1")
        [request] = stub.requests
        assert (request.method, urlsplit(request.path).path) == ("GET", "/v1/subscriptions")
        assert parse_qs(urlsplit(request.path).query) == {"customer": ["cus_1"], "status": ["all"], "limit": ["100"]}
        assert subs == [
            SubscriptionSnapshot(
                stripe_subscription_id="sub_1",
                stripe_customer_id="cus_1",
                status=SubscriptionStatus.ACTIVE,
                plan_key=PlanKey.PRO_MONTHLY,
                price_id="price_m",
                current_period_end=datetime.fromtimestamp(PERIOD_END, UTC),
                cancel_at_period_end=False,
                cancel_at=None,
                ended_at=None,
            )
        ]

    def test_a_subscription_is_read_by_id(self) -> None:
        ended = subscription(status="canceled", price="price_y", ended_at=PERIOD_END - 60)
        with stub_server(lambda req: (200, ended)) as stub:
            sub = provider(stub.url).retrieve_subscription("sub_1")
        assert (stub.requests[0].method, stub.requests[0].path) == ("GET", "/v1/subscriptions/sub_1")
        assert (sub.status, sub.plan_key, sub.ended_at) == (
            SubscriptionStatus.CANCELED,
            PlanKey.PRO_YEARLY,
            datetime.fromtimestamp(PERIOD_END - 60, UTC),
        )


class TestErrors:
    @pytest.mark.parametrize("read", READS)
    def test_a_missing_resource_is_a_rejection_not_an_outage(self, read: str) -> None:
        """Synchronization and reconciliation stop retrying only on a rejection (a deleted customer)."""
        with stub_server(lambda req: (404, MISSING)) as stub, pytest.raises(InvalidRequestError) as caught:
            READS[read](provider(stub.url))
        assert len(stub.requests) == 1  # a rejection is not retried
        assert "No such customer" not in caught.value.public_message  # vendor messages never reach clients

    @pytest.mark.parametrize(
        ("status", "error_type", "attempts"),
        [(429, "invalid_request_error", 1), (500, "api_error", 3)],
    )
    def test_other_failures_are_outages(self, status: int, error_type: str, attempts: int) -> None:
        body = {"error": {"type": error_type, "message": "Stripe could not answer"}}
        with stub_server(lambda req: (status, body)) as stub, pytest.raises(DependencyUnavailableError):
            provider(stub.url).list_customer_subscriptions("cus_1")
        assert len(stub.requests) == attempts  # a server error is retried at most twice

    def test_an_unreachable_stripe_is_an_outage(self) -> None:
        with socket.socket() as probe:  # a loopback port with nothing listening
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        with pytest.raises(DependencyUnavailableError):
            provider(f"http://127.0.0.1:{port}").list_customer_subscriptions("cus_1")


class TestCustomerAdoption:
    """Adoption links a customer to the user named in metadata this server wrote, re-read from Stripe."""

    @pytest.mark.parametrize(
        ("customer", "expected"),
        [
            ({"metadata": {"app_user_id": str(USER)}}, USER),
            ({"deleted": True, "metadata": {"app_user_id": str(USER)}}, None),
            ({"metadata": {"app_user_id": "not-a-uuid"}}, None),
            ({"metadata": {}}, None),
            ({}, None),
        ],
        ids=["linked", "deleted", "malformed", "no-reference", "no-metadata"],
    )
    def test_only_a_live_customer_with_a_valid_reference_names_a_user(
        self, customer: dict[str, Any], expected: uuid.UUID | None
    ) -> None:
        body = {"id": "cus_1", "object": "customer", **customer}
        with stub_server(lambda req: (200, body)) as stub:
            assert provider(stub.url).get_customer_app_user_id("cus_1") == expected
        assert (stub.requests[0].method, stub.requests[0].path) == ("GET", "/v1/customers/cus_1")


class TestConfigurationErrors:
    """A rejected key, or a rejected request built only from server values, is a setup mistake: it must be heard."""

    CALLS: ClassVar[dict[str, Read]] = {
        "customer": lambda p: p.create_customer(USER),
        "checkout": lambda p: p.create_checkout_session(
            customer_id="cus_1",
            price_id="price_m",
            user_id=USER,
            success_url="https://app.test/s",
            cancel_url="https://app.test/c",
            idempotency_key="checkout-key-1",
        ),
        "portal": lambda p: p.create_portal_session(customer_id="cus_1", return_url="https://app.test/billing"),
    }

    @staticmethod
    def stripe_says(status: int, error_type: str, **fields: str) -> tuple[int, dict[str, Any]]:
        leaked = "secret detail: price_attacker for cus_victim"
        return status, {"error": {"type": error_type, "message": leaked, **fields}}

    @pytest.mark.parametrize("call", CALLS)
    @pytest.mark.parametrize(
        "answer",
        [
            ("authentication", (401, "invalid_request_error")),
            ("permission", (403, "invalid_request_error")),
            ("rejected", (400, "invalid_request_error")),
        ],
        ids=lambda a: a[0],
    )
    def test_each_is_a_503_not_configured_and_one_error_without_stripes_message(
        self, call: str, answer: tuple[str, tuple[int, str]], caplog: pytest.LogCaptureFixture
    ) -> None:
        status, error_type = answer[1]
        respond = lambda req: self.stripe_says(status, error_type, code="x_code", param="line_items")  # noqa: E731
        with stub_server(respond) as stub, caplog.at_level(logging.WARNING), pytest.raises(NotConfiguredError):
            self.CALLS[call](provider(stub.url))
        errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert [r.getMessage() for r in errors] == ["stripe_configuration_error"]
        assert "price_attacker" not in caplog.text
        assert "cus_victim" not in caplog.text
        assert errors[0].http_status == status  # type: ignore[attr-defined]

    @pytest.mark.parametrize("call", ["checkout", "portal"])
    def test_a_customer_stripe_does_not_have_stays_a_rejection(
        self, call: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A deleted customer is the one server-built input that goes stale; sync and reconciliation own that case."""
        missing = (404, {"error": {**MISSING["error"], "param": "customer"}})
        with (
            stub_server(lambda req: missing) as stub,
            caplog.at_level(logging.WARNING),
            pytest.raises(InvalidRequestError),
        ):
            self.CALLS[call](provider(stub.url))
        assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []

    @pytest.mark.parametrize("read", READS)
    def test_reads_still_treat_a_rejection_as_a_rejection(self, read: str) -> None:
        body = {"error": {"type": "invalid_request_error", "message": "m", "param": "customer"}}
        with stub_server(lambda req: (400, body)) as stub, pytest.raises(InvalidRequestError):
            READS[read](provider(stub.url))

    @pytest.mark.parametrize("read", READS)
    def test_a_rejected_key_is_a_configuration_error_for_reads_too(
        self, read: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        rejected = (401, {"error": {"type": "invalid_request_error", "message": "m"}})
        with (
            stub_server(lambda req: rejected) as stub,
            caplog.at_level(logging.ERROR),
            pytest.raises(NotConfiguredError),
        ):
            READS[read](provider(stub.url))
        assert [r.getMessage() for r in caplog.records] == ["stripe_configuration_error"]


def _portal(**features: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "payment_method_update": {"enabled": True},
        "invoice_history": {"enabled": True},
        "subscription_cancel": {"enabled": True, "mode": "at_period_end"},
        "subscription_update": {
            "enabled": True,
            "default_allowed_updates": ["price"],
            "products": [{"product": "prod_1", "prices": ["price_m", "price_y"]}],
        },
    }
    return {"id": "bpc_1", "object": "billing_portal.configuration", "active": True, "is_default": True,
            "features": {**base, **features}}  # fmt: skip


def _listing(*configs: dict[str, Any]) -> dict[str, Any]:
    return {"object": "list", "url": "/v1/billing_portal/configurations", "has_more": False, "data": list(configs)}


class TestPortalConfiguration:
    def test_the_default_configuration_is_read_with_nothing_written(self) -> None:
        with stub_server(lambda req: (200, _listing(_portal()))) as stub:
            portal = provider(stub.url).describe_portal()
        [request] = stub.requests
        assert (request.method, urlsplit(request.path).path) == ("GET", "/v1/billing_portal/configurations")
        assert parse_qs(urlsplit(request.path).query) == {"is_default": ["true"], "limit": ["1"]}
        assert portal == PortalConfiguration(
            active=True,
            payment_method_update=True,
            invoice_history=True,
            cancel_enabled=True,
            cancel_mode="at_period_end",
            switch_enabled=True,
            allowed_updates=("price",),
            switch_prices=frozenset({"price_m", "price_y"}),
        )
        assert portal_problems(portal, CATALOG) == []

    def test_every_price_of_every_product_counts(self) -> None:
        products = [{"product": "a", "prices": ["price_m"]}, {"product": "b", "prices": ["price_y", "price_x"]}]
        update = {"enabled": True, "default_allowed_updates": ["price"], "products": products}
        with stub_server(lambda req: (200, _listing(_portal(subscription_update=update)))) as stub:
            portal = provider(stub.url).describe_portal()
        assert portal is not None
        assert portal.switch_prices == {"price_m", "price_y", "price_x"}

    def test_a_mode_without_a_default_configuration_has_none(self) -> None:
        with stub_server(lambda req: (200, _listing())) as stub:
            assert provider(stub.url).describe_portal() is None

    def test_switching_turned_off_reads_as_no_prices(self) -> None:
        update = {"enabled": False, "default_allowed_updates": [], "products": None}
        with stub_server(lambda req: (200, _listing(_portal(subscription_update=update)))) as stub:
            portal = provider(stub.url).describe_portal()
        assert portal is not None
        assert (portal.switch_enabled, portal.allowed_updates, portal.switch_prices) == (False, (), frozenset())

    @pytest.mark.parametrize(
        ("status", "error", "message"),
        [
            (401, DependencyUnavailableError, "rejected the API key"),
            (403, ForbiddenError, "cannot read Customer Portal"),
            (400, InvalidRequestError, "portal configuration"),
            (500, DependencyUnavailableError, "temporarily unavailable"),
        ],
    )
    def test_errors_are_specific_and_never_echo_the_key(self, status: int, error: type, message: str) -> None:
        body = {"error": {"type": "invalid_request_error" if status < 500 else "api_error", "message": FAKE_KEY}}
        with stub_server(lambda req: (status, body)) as stub, pytest.raises(error) as caught:
            provider(stub.url).describe_portal()
        assert message in caught.value.public_message  # type: ignore[attr-defined]
        assert FAKE_KEY not in caught.value.public_message  # type: ignore[attr-defined]


class TestRedirectHosts:
    """The user is sent to the URL Stripe returns: Stripe's own hosts, or the account's custom domain, nothing else."""

    SESSION_ID = "cs_live_a1b2c3"

    @staticmethod
    def checkout(url: str, custom_domain: str | None = None) -> str:
        session = {"id": "cs_1", "object": "checkout.session", "url": url}
        with stub_server(lambda req: (200, session)) as stub:
            return (
                StripeBillingProvider(
                    secret_key=FAKE_KEY, catalog=CATALOG, api_base=stub.url, custom_domain=custom_domain
                )
                .create_checkout_session(
                    customer_id="cus_1",
                    price_id="price_m",
                    user_id=USER,
                    success_url="https://app.test/s",
                    cancel_url="https://app.test/c",
                    idempotency_key="checkout-key-1",
                )
                .url
            )

    @staticmethod
    def portal(url: str, custom_domain: str | None = None) -> str:
        session = {"id": "bps_1", "object": "billing_portal.session", "url": url}
        with stub_server(lambda req: (200, session)) as stub:
            return StripeBillingProvider(
                secret_key=FAKE_KEY, catalog=CATALOG, api_base=stub.url, custom_domain=custom_domain
            ).create_portal_session(customer_id="cus_1", return_url="https://app.test/billing")

    @pytest.mark.parametrize(
        "url", ["https://checkout.stripe.com/c/pay/cs_1", "https://billing.stripe.com/p/session/x"]
    )
    def test_stripe_hosts_are_accepted_without_any_setting(self, url: str) -> None:
        assert self.checkout(url) == url
        assert self.portal(url) == url

    def test_the_configured_custom_domain_is_accepted_unchanged(self) -> None:
        url = f"https://payments.example.com/c/pay/{self.SESSION_ID}#fid"
        assert self.checkout(url, "payments.example.com") == url
        assert self.portal("https://payments.example.com/p/session/bps_1", "payments.example.com") == (
            "https://payments.example.com/p/session/bps_1"
        )

    @pytest.mark.parametrize(
        "url",
        [
            "https://payments.example.com/c/pay/cs_1",  # no custom domain is configured
            "http://checkout.stripe.com/c/pay/cs_1",
            "https://stripe.com.evil.test/c/pay/cs_1",
            "https://notstripe.com/c/pay/cs_1",
            "https://checkout.stripe.com@evil.test/c/pay/cs_1",
            "https://evil.test\\@checkout.stripe.com/c/pay/cs_1",
            "https://[::1/c/pay/cs_1",
            "/c/pay/cs_1",
        ],
    )
    @pytest.mark.parametrize("call", ["checkout", "portal"])
    def test_any_other_host_is_a_configuration_error_that_logs_the_host_only(
        self, call: str, url: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        url = url.replace("cs_1", self.SESSION_ID)
        with caplog.at_level(logging.WARNING), pytest.raises(NotConfiguredError, match="STRIPE_CUSTOM_DOMAIN"):
            getattr(self, call)(url)
        [record] = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert record.getMessage() == "stripe_session_host_unexpected"
        assert self.SESSION_ID not in caplog.text  # the session ID is in the path

    @pytest.mark.parametrize("host", ["payments.example.com.evil.test", "xpayments.example.com", "example.com"])
    def test_a_custom_domain_matches_exactly(self, host: str) -> None:
        with pytest.raises(NotConfiguredError):
            self.checkout(f"https://{host}/c/pay/x", "payments.example.com")
