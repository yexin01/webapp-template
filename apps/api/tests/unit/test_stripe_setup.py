"""``setup-stripe`` against a loopback stand-in for Stripe: no network access (mocked).

The stand-in keeps products and prices, so a second run can be seen to create nothing.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from app import cli
from app.kernel.infrastructure.config import DatabaseSettings
from app.modules.billing.adapters import stripe_setup
from app.modules.billing.adapters.stripe_setup import Amounts, SetupError
from tests.stub_server import Recorded, Stub, stub_server

KEY = "sk_test_" + "0" * 24
AMOUNTS = Amounts("usd", 900, 9000)
LIVE_KEY = "sk_" + "live_" + "0" * 24


def price(plan: str, **fields: Any) -> dict[str, Any]:
    interval = "month" if plan == "pro_monthly" else "year"
    return {
        "id": f"price_{plan}",
        "object": "price",
        "active": True,
        "currency": "usd",
        "unit_amount": 900 if interval == "month" else 9000,
        "recurring": {"interval": interval, "interval_count": 1},
        "lookup_key": plan,
        "product": "prod_existing",
        "livemode": False,
        "type": "recurring",
        **fields,
    }


def portal(**features: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "payment_method_update": {"enabled": True},
        "invoice_history": {"enabled": True},
        "subscription_cancel": {"enabled": True, "mode": "at_period_end"},
        "subscription_update": {
            "enabled": True,
            "default_allowed_updates": ["price"],
            "products": [{"product": "prod_pro", "prices": ["price_pro_monthly", "price_pro_yearly"]}],
        },
    }
    return {"id": "bpc_1", "object": "billing_portal.configuration", "active": True, "features": {**base, **features}}


class FakeStripe:
    """Products, prices, and the default portal configuration, as Stripe answers for them."""

    def __init__(self, prices: list[dict[str, Any]] | None = None, portal_config: Any = None) -> None:
        self.prices = {p["lookup_key"]: p for p in prices or []}
        self.products: dict[str, dict[str, Any]] = {}
        self.portal = portal_config
        self.portal_status = 200

    def __call__(self, req: Recorded) -> tuple[int, Any]:
        parts = urlsplit(req.path)
        query = parse_qs(parts.query)
        body = parse_qs(req.body.decode())
        match (req.method, parts.path):
            case ("GET", path) if path.startswith("/v1/products/"):
                found = self.products.get(path.rsplit("/", 1)[1])
                if found is None:
                    return 404, {"error": {"type": "invalid_request_error", "code": "resource_missing", "message": "m"}}
                return 200, found
            case ("POST", "/v1/products"):
                product = {"id": body["id"][0], "object": "product", "active": True, "name": body["name"][0]}
                self.products[product["id"]] = product
                return 200, product
            case ("GET", "/v1/prices"):
                wanted = {v[0] for k, v in query.items() if k.startswith("lookup_keys")}
                return 200, _listing([p for key, p in self.prices.items() if key in wanted])
            case ("POST", "/v1/prices"):
                key = body["lookup_key"][0]
                created = price(
                    key,
                    id=f"price_{key}",
                    currency=body["currency"][0],
                    unit_amount=int(body["unit_amount"][0]),
                    product=body["product"][0],
                    recurring={"interval": body["recurring[interval]"][0], "interval_count": 1},
                )
                self.prices[key] = created
                return 200, created
            case ("GET", "/v1/billing_portal/configurations"):
                if self.portal_status != 200:
                    return self.portal_status, {"error": {"type": "invalid_request_error", "message": "m"}}
                return 200, _listing([self.portal] if self.portal else [])
        return 500, {"error": {"type": "api_error", "message": f"unexpected {req.method} {parts.path}"}}


def _listing(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {"object": "list", "url": "/v1/x", "has_more": False, "data": items}


def run(fake: FakeStripe, amounts: Amounts = AMOUNTS) -> tuple[list[str], Stub, SetupError | None]:
    lines: list[str] = []
    error: SetupError | None = None
    with stub_server(fake) as stub:
        try:
            stripe_setup.run(KEY, amounts, api_base=stub.url, out=lines.append)
        except SetupError as exc:
            error = exc
    return lines, stub, error


def writes(stub: Stub) -> list[tuple[str, str]]:
    return [(r.method, urlsplit(r.path).path) for r in stub.requests if r.method != "GET"]


class TestCreation:
    def test_an_empty_sandbox_gets_the_product_and_both_prices_exactly_as_asked(self) -> None:
        fake = FakeStripe(portal_config=portal())
        lines, stub, error = run(fake)

        assert error is None
        assert writes(stub) == [("POST", "/v1/products"), ("POST", "/v1/prices"), ("POST", "/v1/prices")]
        product, monthly, yearly = (parse_qs(r.body.decode()) for r in stub.requests if r.method == "POST")
        assert product == {"id": ["prod_pro"], "name": ["Pro"]}
        assert monthly == {
            "product": ["prod_pro"],
            "currency": ["usd"],
            "unit_amount": ["900"],
            "recurring[interval]": ["month"],
            "lookup_key": ["pro_monthly"],
            "nickname": ["Pro monthly"],
        }
        assert yearly["unit_amount"] == ["9000"]
        assert yearly["recurring[interval]"] == ["year"]
        assert yearly["lookup_key"] == ["pro_yearly"]
        assert all(r.headers["idempotency-key"] for r in stub.requests if r.method == "POST")
        # The IDs to set, exactly as .env wants them.
        assert "BILLING_MODE=sandbox" in lines
        assert "STRIPE_PRICE_PRO_MONTHLY=price_pro_monthly" in lines
        assert "STRIPE_PRICE_PRO_YEARLY=price_pro_yearly" in lines
        assert "STRIPE_PRODUCT_PRO=prod_pro" in lines
        assert lines.index("STRIPE_PRODUCT_PRO=prod_pro") == lines.index("STRIPE_PRICE_PRO_YEARLY=price_pro_yearly") + 1

    def test_a_second_run_creates_nothing_and_prints_the_same_lines(self) -> None:
        fake = FakeStripe(portal_config=portal())
        first, _, _ = run(fake)
        second, stub, error = run(fake)

        assert error is None
        assert writes(stub) == []
        env_lines = [line for line in first if "=" in line]
        assert "STRIPE_PRODUCT_PRO=prod_pro" in env_lines
        assert [line for line in second if "=" in line] == env_lines

    def test_one_missing_price_is_created_on_the_product_of_the_other(self) -> None:
        fake = FakeStripe([price("pro_monthly")], portal())
        lines, stub, error = run(fake)

        assert error is None
        assert writes(stub) == [("POST", "/v1/prices")]  # not even a product: the existing price has one
        assert parse_qs(stub.requests[-2].body.decode())["product"] == ["prod_existing"]
        assert "STRIPE_PRODUCT_PRO=prod_existing" in lines

    def test_prices_made_in_the_dashboard_give_the_product_they_belong_to(self) -> None:
        lines, stub, error = run(FakeStripe([price("pro_monthly"), price("pro_yearly")], portal()))

        assert error is None
        assert writes(stub) == []
        assert "STRIPE_PRODUCT_PRO=prod_existing" in lines

    def test_an_interrupted_run_finds_its_product_again(self) -> None:
        fake = FakeStripe(portal_config=portal())
        fake.products["prod_pro"] = {"id": "prod_pro", "object": "product", "active": True, "name": "Pro"}
        lines, stub, _ = run(fake)
        assert ("POST", "/v1/products") not in writes(stub)
        assert "Product prod_pro: already exists" in lines
        assert "STRIPE_PRODUCT_PRO=prod_pro" in lines

    def test_an_archived_product_stops_the_run(self) -> None:
        fake = FakeStripe(portal_config=portal())
        fake.products["prod_pro"] = {"id": "prod_pro", "object": "product", "active": False, "name": "Pro"}
        _, stub, error = run(fake)
        assert error is not None
        assert "archived" in str(error)
        assert writes(stub) == []


class TestPricesThatDiffer:
    @pytest.mark.parametrize(
        ("existing", "fragment"),
        [
            (price("pro_monthly", unit_amount=1000), "amount is 1000"),
            (price("pro_monthly", currency="eur"), "currency is EUR, not USD"),
            (price("pro_monthly", active=False), "archived"),
            (price("pro_monthly", recurring={"interval": "year", "interval_count": 1}), "once every month"),
            (price("pro_monthly", recurring={"interval": "month", "interval_count": 3}), "once every month"),
        ],
    )
    def test_the_run_stops_before_creating_anything_and_says_how_to_change_a_price(
        self, existing: dict[str, Any], fragment: str
    ) -> None:
        fake = FakeStripe([existing], portal())  # the yearly price is missing: it is not created either
        lines, stub, error = run(fake)

        assert error is not None
        assert fragment in str(error)
        assert "Nothing was created or changed" in str(error)
        assert "STRIPE_PRICE_PRO_MONTHLY" in str(error)
        assert "on the same product" in str(error)  # the old price keeps Pro because it belongs to the Pro product
        assert "RETAINED" not in str(error)
        assert writes(stub) == []
        assert lines == []  # no .env lines for a setup that did not finish

    def test_prices_on_different_products_stop_the_run_because_pro_is_granted_by_one_product(self) -> None:
        fake = FakeStripe([price("pro_monthly", product="prod_a"), price("pro_yearly", product="prod_b")], portal())
        lines, stub, error = run(fake)

        assert error is not None
        assert "Nothing was created or changed" in str(error)
        assert "different products (prod_a, prod_b)" in str(error)
        assert writes(stub) == []
        assert lines == []

    def test_a_product_the_application_would_refuse_stops_the_run_before_it_prints_a_line_for_it(self) -> None:
        """The custom ID an earlier version of this command gave its product is not a prod_ ID."""
        lines, stub, error = run(
            FakeStripe([price("pro_monthly", product="pro"), price("pro_yearly", product="pro")], portal())
        )

        assert error is not None
        assert "does not start with prod_" in str(error)
        assert "STRIPE_PRODUCT_PRO" in str(error)
        assert writes(stub) == []
        assert lines == []

    def test_every_difference_is_listed(self) -> None:
        fake = FakeStripe([price("pro_monthly", unit_amount=1), price("pro_yearly", currency="eur")])
        _, _, error = run(fake)
        assert error is not None
        assert "pro_monthly" in str(error)
        assert "pro_yearly" in str(error)


class TestPortalReport:
    def test_a_portal_that_follows_the_policy_needs_nothing(self) -> None:
        lines, stub, _ = run(FakeStripe(portal_config=portal()))
        assert "Customer Portal: the default configuration matches DEC-010." in lines
        assert not any("Customer portal, turn on" in line for line in lines)
        assert writes(stub) == [("POST", "/v1/products"), ("POST", "/v1/prices"), ("POST", "/v1/prices")]

    def test_what_is_missing_is_listed_with_the_dashboard_step(self) -> None:
        config = portal(
            invoice_history={"enabled": False},
            subscription_cancel={"enabled": True, "mode": "immediately"},
        )
        lines, stub, _ = run(FakeStripe(portal_config=config))
        text = "\n".join(lines)
        assert "the portal does not show invoice history" in text
        assert "cancels immediately" in text
        assert "Settings > Billing > Customer portal" in text
        # Read only: the portal is never created or changed.
        assert not any("billing_portal" in urlsplit(r.path).path and r.method != "GET" for r in stub.requests)

    def test_a_mode_without_a_saved_portal_says_to_save_one(self) -> None:
        lines, _, _ = run(FakeStripe(portal_config=None))
        assert any("no default Customer Portal configuration" in line for line in lines)

    def test_a_key_that_cannot_read_the_portal_is_reported_as_not_checked(self) -> None:
        fake = FakeStripe(portal_config=portal())
        fake.portal_status = 403
        lines, _, error = run(fake)
        assert error is None
        assert any(line.startswith("Customer Portal: not checked") for line in lines)

    def test_the_other_dashboard_only_steps_are_always_listed(self) -> None:
        lines, _, _ = run(FakeStripe(portal_config=portal()))
        text = "\n".join(lines)
        assert "Revenue recovery" in text
        assert "Snapshot" in text
        assert "stripe listen --forward-to localhost:8000/api/v1/webhooks/stripe" in text


class TestWhatIsNeverPrinted:
    def test_the_secret_key_is_not_in_the_output_nor_in_an_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        lines, _, _ = run(FakeStripe(portal_config=portal()))
        assert KEY not in "\n".join(lines)
        assert "STRIPE_SECRET_KEY=<your sk_test_ key" in "\n".join(lines)

        body = {"error": {"type": "invalid_request_error", "message": f"Invalid API Key provided: {KEY}"}}
        with stub_server(lambda req: (401, body)) as stub, pytest.raises(SetupError) as caught:
            stripe_setup.run(KEY, AMOUNTS, api_base=stub.url, out=lambda line: None)
        assert KEY not in str(caught.value)
        assert "Invalid API Key" not in str(caught.value)
        captured = capsys.readouterr()
        assert KEY not in captured.out + captured.err


@pytest.fixture(autouse=True)
def no_developer_env_file(monkeypatch: pytest.MonkeyPatch) -> None:
    for settings in (DatabaseSettings, stripe_setup.StripeSetupSettings):
        monkeypatch.setitem(settings.model_config, "env_file", None)  # never the developer's .env


class TestRefusals:
    """Everything below happens before any request: there is no stub to answer one."""

    def command(self, *extra: str) -> list[str]:
        return ["setup-stripe", "--currency", "usd", "--monthly", "900", "--yearly", "9000", *extra]

    @pytest.mark.parametrize("key", [LIVE_KEY, "rk_test_" + "0" * 24, "", "pk_test_" + "0" * 24])
    def test_only_a_test_mode_secret_key_is_accepted(self, key: str) -> None:
        with pytest.raises(SetupError, match="sk_test_"):
            stripe_setup.run(key, AMOUNTS, api_base="http://127.0.0.1:1", out=lambda line: None)

    @pytest.mark.parametrize("app_env", [None, "production", "preview"])
    def test_only_a_local_or_test_environment_is_accepted(
        self, app_env: str | None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("STRIPE_SECRET_KEY", KEY)
        monkeypatch.delenv("APP_ENV", raising=False)
        if app_env:
            monkeypatch.setenv("APP_ENV", app_env)
        assert cli.main(self.command()) == 1
        captured = capsys.readouterr()
        assert "APP_ENV" in captured.err
        assert KEY not in captured.out + captured.err

    def test_a_live_key_is_refused_by_the_command_without_echoing_it(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("APP_ENV", "local")
        monkeypatch.setenv("STRIPE_SECRET_KEY", LIVE_KEY)
        assert cli.main(self.command()) == 1
        captured = capsys.readouterr()
        assert "test-mode secret key" in captured.err
        assert LIVE_KEY not in captured.out + captured.err

    def test_a_missing_key_is_refused(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("APP_ENV", "test")
        monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
        assert cli.main(self.command()) == 1
        assert "STRIPE_SECRET_KEY is not set" in capsys.readouterr().err

    @pytest.mark.parametrize(
        "argv",
        [
            ["setup-stripe"],
            ["setup-stripe", "--currency", "usd", "--monthly", "900"],
            ["setup-stripe", "--monthly", "900", "--yearly", "9000"],
            ["setup-stripe", "--currency", "usd", "--yearly", "9000"],
        ],
        ids=["none", "no-yearly", "no-currency", "no-monthly"],
    )
    def test_currency_and_both_amounts_are_required_with_no_defaults(self, argv: list[str]) -> None:
        with pytest.raises(SystemExit) as caught:
            cli.main(argv)
        assert caught.value.code == 2

    @pytest.mark.parametrize(
        ("flag", "value"),
        [
            ("--currency", "dollars"),
            ("--currency", "u1d"),
            ("--monthly", "0"),
            ("--monthly", "9.99"),
            ("--yearly", "-5"),
        ],
    )
    def test_malformed_values_are_refused(self, flag: str, value: str) -> None:
        argv = ["setup-stripe", "--currency", "usd", "--monthly", "900", "--yearly", "9000"]
        argv[argv.index(flag) + 1] = value
        with pytest.raises(SystemExit) as caught:
            cli.main(argv)
        assert caught.value.code == 2

    def test_the_currency_is_sent_lowercase(self) -> None:
        assert cli._currency("USD") == "usd"
