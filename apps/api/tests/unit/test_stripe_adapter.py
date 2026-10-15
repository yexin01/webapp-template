"""The Stripe billing adapter against a loopback stub: the real SDK, no network access (mocked).

These pin the boundary the billing service relies on: what each call sends to
Stripe, how answers map to domain values, and which failures are rejections
(``InvalidRequestError``: retrying cannot help) and which are outages
(``DependencyUnavailableError``: a 503, retried later).
"""

from __future__ import annotations

import socket
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from app.kernel.errors import DependencyUnavailableError, InvalidRequestError
from app.modules.billing.adapters.stripe import StripeBillingProvider
from app.modules.billing.domain import PlanKey, PriceCatalog, SubscriptionSnapshot, SubscriptionStatus
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
        session = {"id": "cs_1", "object": "checkout.session", "url": "https://checkout.stripe.test/cs_1"}
        with stub_server(lambda req: (200, session)) as stub:
            url = provider(stub.url).create_checkout_session(
                customer_id="cus_1",
                price_id="price_y",
                user_id=USER,
                success_url="https://app.test/billing/success?session_id={CHECKOUT_SESSION_ID}",
                cancel_url="https://app.test/billing?checkout=canceled",
                idempotency_key="checkout-key-1",
            )
        assert url == "https://checkout.stripe.test/cs_1"
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
        session = {"id": "bps_1", "object": "billing_portal.session", "url": "https://billing.stripe.test/p/1"}
        with stub_server(lambda req: (200, session)) as stub:
            url = provider(stub.url).create_portal_session(customer_id="cus_1", return_url="https://app.test/billing")
        assert url == "https://billing.stripe.test/p/1"
        [request] = stub.requests
        assert (request.method, request.path) == ("POST", "/v1/billing_portal/sessions")
        assert form(request) == {"customer": ["cus_1"], "return_url": ["https://app.test/billing"]}


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
        [(401, "invalid_request_error", 1), (429, "invalid_request_error", 1), (500, "api_error", 3)],
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
