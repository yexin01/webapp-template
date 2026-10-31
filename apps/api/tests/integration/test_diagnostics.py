"""Integration diagnostics: configuration report, checks, and safety rules."""

from __future__ import annotations

import base64
import json
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
import pytest
from psycopg import sql

from app.kernel.errors import ForbiddenError
from app.kernel.infrastructure.config import AppEnv
from app.kernel.infrastructure.store import PgKernelStore
from app.kernel.ports import ConnectionDescription
from app.kernel.tenancy import Role
from app.modules.billing.domain import PortalConfiguration
from app.modules.billing.ports import PriceDescription
from app.modules.identity.domain import ExternalMembership
from app.modules.llm.adapters import MockLLMProvider
from app.settings import Settings
from tests.conftest import PRICE_MONTHLY, PRICE_YEARLY, PRODUCT_PRO, Harness, grant_to_administrator, superuser_query

pytestmark = pytest.mark.db

BASE = "/api/v1/diagnostics/integrations"
PUBLISHABLE = "pk_test_" + base64.b64encode(b"example.clerk.accounts.dev$").decode()
# Test-only placeholders; assembled at runtime so secret scanners do not flag them.
SECRETS = {
    "clerk_secret_key": "sk_test_" + "c" * 30,
    "clerk_webhook_signing_secret": "whsec_" + "w" * 30,
    "stripe_secret_key": "sk_test_" + "s" * 30,
    "llm_api_key": "llm-" + "k" * 30,
    "sentry_dsn": "https://" + "d" * 32 + "@o1.ingest.sentry.invalid/2",
}
OPERATORS = {"diagnostics_operator_ids": "user_a,user_b"}
SANDBOX = {
    **OPERATORS,
    "clerk_publishable_key": PUBLISHABLE,
    "clerk_authorized_parties": "http://localhost:3000",
    "billing_mode": "sandbox",
    "llm_mode": "sandbox",
    "llm_model": "test-model",
    "analytics_mode": "sandbox",
    "posthog_project_api_key": "phc_test_only",
    "error_reporting_mode": "sandbox",
    **SECRETS,
}


class FakeErrorReporter:
    def __init__(self, event_id: str | None = "0123456789abcdef0123456789abcdef") -> None:
        self.event_id = event_id
        self.sent = 0

    def send_test_event(self) -> str | None:
        self.sent += 1
        return self.event_id


def _price(price_id: str, interval: str, **kwargs: Any) -> PriceDescription:
    values: dict[str, Any] = {
        "price_id": price_id,
        "product_id": PRODUCT_PRO,
        "active": True,
        "recurring_interval": interval,
        "currency": "eur",
        "livemode": False,
    }
    values.update(kwargs)
    return PriceDescription(**values)


def _portal(**kwargs: Any) -> PortalConfiguration:
    """A default Customer Portal configuration that follows DEC-010."""
    values: dict[str, Any] = {
        "active": True,
        "payment_method_update": True,
        "invoice_history": True,
        "cancel_enabled": True,
        "cancel_mode": "at_period_end",
        "switch_enabled": True,
        "allowed_updates": ("price",),
        "switch_prices": frozenset({PRICE_MONTHLY, PRICE_YEARLY}),
    }
    values.update(kwargs)
    return PortalConfiguration(**values)


@pytest.fixture
def sandbox(make_harness: Callable[..., Harness]) -> Callable[..., Harness]:
    def factory(settings: dict[str, Any] | None = None, **overrides: Any) -> Harness:
        overrides.setdefault("error_reporter", FakeErrorReporter())
        h = make_harness({**SANDBOX, **(settings or {})}, **overrides)
        h.billing.set_price(_price(PRICE_MONTHLY, "month"))
        h.billing.set_price(_price(PRICE_YEARLY, "year"))
        h.billing.set_portal(_portal())
        return h

    return factory


@pytest.fixture
def operator_harness(make_harness: Callable[..., Harness]) -> Harness:
    return make_harness(OPERATORS)


def check(h: Harness, key: str, user: str = "user_a") -> dict[str, Any]:
    response = h.client.post(f"{BASE}/{key}/check", headers=h.login(user))
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def test_requires_authentication(harness: Harness) -> None:
    assert harness.client.get(BASE).status_code == 401
    assert harness.client.post(f"{BASE}/database/check").status_code == 401


def test_report_lists_every_integration_without_secrets(sandbox: Callable[..., Harness]) -> None:
    h = sandbox()
    response = h.client.get(BASE, headers=h.login("user_a"))
    assert response.status_code == 200
    body = response.json()
    assert body["environment"] == "test"
    modes = {item["key"]: item["mode"] for item in body["integrations"]}
    assert modes == {
        "database": "local",
        "identity": "sandbox",
        "billing": "sandbox",
        "llm": "sandbox",
        "analytics": "sandbox",
        "error_reporting": "sandbox",
    }
    raw = response.text
    for name, value in SECRETS.items():
        assert value not in raw, name
    # Connection strings, including the local one, are never echoed.
    assert "postgresql://" not in raw
    assert "postgres:postgres" not in raw


def test_non_operators_cannot_see_or_run_diagnostics(make_harness: Callable[..., Harness]) -> None:
    """Diagnostics expose configuration and call providers: deny by default."""
    h = make_harness({"diagnostics_operator_ids": "user_ops"})
    outsider = h.login("user_a")
    assert h.client.get(BASE, headers=outsider).status_code == 404
    assert h.client.post(f"{BASE}/database/check", headers=outsider).status_code == 404
    operator = h.login("user_ops")
    assert h.client.get(BASE, headers=operator).status_code == 200

    # Without any operator, only APP_ENV=local opens diagnostics to signed-in users.
    closed = make_harness()
    assert closed.client.get(BASE, headers=closed.login("user_a")).status_code == 404


def test_mock_and_disabled_integrations_are_skipped_not_passed(operator_harness: Harness) -> None:
    h = operator_harness
    for key in ("billing", "llm", "identity", "analytics", "error_reporting"):
        body = check(h, key)
        assert body["outcome"] == "skipped", key
        assert body["mode"] in ("mock", "disabled")
    assert h.billing.calls == []
    assert h.analytics.events == []


def test_database_check_runs_as_least_privileged_role_with_rls(operator_harness: Harness) -> None:
    body = check(operator_harness, "database")
    assert body["outcome"] == "passed", body
    assert body["mode"] == "local"
    assert "as app_api" in body["summary"]
    assert "row-level security" in body["summary"]


def describe_as(url: str, role: str | None) -> ConnectionDescription:
    with psycopg.connect(url) as conn, conn.transaction():
        if role is not None:
            conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))
        return PgKernelStore(conn).describe_connection()


def test_connection_description_reads_role_attributes_and_table_protection(db: str) -> None:
    """DB-06 as the database probe sees it, read with the runtime role's own privileges."""
    api = describe_as(db, "app_api")
    assert (api.role, api.superuser, api.bypass_rls, api.unprotected_tables, api.owned_tables) == (
        "app_api",
        False,
        False,
        (),
        (),
    )
    # The test database's administrator owns the app tables. Its attributes depend on how the
    # server was set up (CREATE ROLE ... SUPERUSER leaves out BYPASSRLS), so pg_roles is the reference.
    admin = describe_as(db, None)
    [attributes] = superuser_query(db, "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
    assert (admin.superuser, admin.bypass_rls) == attributes
    assert {"projects", "audit_log", "outbox"} <= set(admin.owned_tables)


@contextmanager
def scratch_role(url: str, attributes: str) -> Iterator[str]:
    """A uniquely named role that owns nothing and is granted nothing.

    Roles belong to the whole server, which other test sessions share, so app_api itself is never altered.
    """
    role = f"app_test_role_{uuid.uuid4().hex[:10]}"
    superuser_query(url, f"CREATE ROLE {role} NOLOGIN NOINHERIT {attributes}")
    try:
        grant_to_administrator(url, role)  # so the test can SET ROLE to it
        yield role
    finally:
        superuser_query(url, f"DROP ROLE {role}")


def test_connection_description_tells_bypassrls_from_superuser(db: str) -> None:
    """A role need not be a superuser to bypass row-level security, so each attribute is read on its own."""
    with scratch_role(db, "NOSUPERUSER BYPASSRLS") as role:
        described = describe_as(db, role)
    assert (described.role, described.superuser, described.bypass_rls) == (role, False, True)


SCRATCH_TABLE = "diagnostics_scratch"


@contextmanager
def scratch_table(url: str, *changes: str) -> Iterator[None]:
    """A throwaway table in schema app of this test database; the real tables and their grants stay untouched."""
    superuser_query(url, f"CREATE TABLE app.{SCRATCH_TABLE} (id integer)")
    try:
        for change in changes:
            if change.startswith("OWNER TO "):
                # Without superuser rights, the new owner needs CREATE on the schema, as in the core schema's hand-over.
                owner = change.removeprefix("OWNER TO ")
                superuser_query(url, f"GRANT CREATE ON SCHEMA app TO {owner}")
                try:
                    superuser_query(url, f"ALTER TABLE app.{SCRATCH_TABLE} {change}")
                finally:
                    superuser_query(url, f"REVOKE CREATE ON SCHEMA app FROM {owner}")
            else:
                superuser_query(url, f"ALTER TABLE app.{SCRATCH_TABLE} {change}")
        yield
    finally:
        superuser_query(url, f"DROP TABLE app.{SCRATCH_TABLE}")


@pytest.mark.parametrize(
    "changes",
    [
        (),
        ("ENABLE ROW LEVEL SECURITY",),  # not forced: its owner is exempt
        ("FORCE ROW LEVEL SECURITY",),  # forced, but not enabled
    ],
)
def test_database_check_fails_for_a_table_without_enforced_rls(operator_harness: Harness, changes: tuple[str]) -> None:
    with scratch_table(operator_harness.database_url, *changes):
        body = check(operator_harness, "database")
    assert (body["outcome"], body["error_code"]) == ("failed", "unsafe_session")
    assert f"not enabled and forced on {SCRATCH_TABLE}" in body["summary"]


def test_database_check_fails_when_the_role_owns_a_table(operator_harness: Harness) -> None:
    changes = ("ENABLE ROW LEVEL SECURITY", "FORCE ROW LEVEL SECURITY", "OWNER TO app_api")
    with scratch_table(operator_harness.database_url, *changes):
        body = check(operator_harness, "database")
    assert (body["outcome"], body["error_code"]) == ("failed", "unsafe_session")
    assert f"app_api owns {SCRATCH_TABLE}" in body["summary"]


def test_identity_check_reads_memberships_from_directory(sandbox: Callable[..., Harness]) -> None:
    h = sandbox()
    h.directory.set("user_a", [ExternalMembership("org_1", Role.ADMIN), ExternalMembership("org_2", Role.MEMBER)])
    body = check(h, "identity")
    assert body["outcome"] == "passed"
    assert "2 organization memberships" in body["summary"]


def test_identity_check_reports_directory_outage(sandbox: Callable[..., Harness]) -> None:
    h = sandbox()
    h.directory.available = False
    body = check(h, "identity")
    assert body["outcome"] == "failed"
    assert body["error_code"] == "dependency_unavailable"


def test_billing_check_verifies_both_prices(sandbox: Callable[..., Harness]) -> None:
    h = sandbox()
    body = check(h, "billing")
    assert body["outcome"] == "passed", body
    assert "monthly (month, eur)" in body["summary"]
    assert "yearly (year, eur)" in body["summary"]


@pytest.mark.parametrize(
    ("monthly", "expected"),
    [
        ({"recurring_interval": "year"}, "bills every year, expected every month"),
        ({"active": False}, "archived"),
        ({"livemode": True}, "belongs to live mode"),
        ({"currency": "usd"}, "different currencies"),
    ],
)
def test_billing_check_flags_misconfigured_prices(
    sandbox: Callable[..., Harness], monthly: dict[str, Any], expected: str
) -> None:
    h = sandbox()
    h.billing.set_price(_price(PRICE_MONTHLY, "month", **monthly))
    body = check(h, "billing")
    assert body["outcome"] == "failed"
    assert body["error_code"] == "price_mismatch"
    assert expected in body["summary"]


def test_billing_check_names_the_product_a_price_belongs_to_when_it_is_not_the_pro_product(
    sandbox: Callable[..., Harness],
) -> None:
    """The owner can copy the product ID from the message into STRIPE_PRODUCT_PRO."""
    h = sandbox()
    h.billing.set_price(_price(PRICE_MONTHLY, "month", product_id="prod_other"))
    body = check(h, "billing")
    assert body["outcome"] == "failed"
    assert body["error_code"] == "price_mismatch"
    assert f"Monthly price belongs to product prod_other, but STRIPE_PRODUCT_PRO is {PRODUCT_PRO}" in body["summary"]
    assert "Yearly price belongs to" not in body["summary"]


def test_billing_check_names_each_price_that_belongs_to_another_product(sandbox: Callable[..., Harness]) -> None:
    h = sandbox()
    h.billing.set_price(_price(PRICE_MONTHLY, "month", product_id="prod_a"))
    h.billing.set_price(_price(PRICE_YEARLY, "year", product_id=None))
    summary = check(h, "billing")["summary"]
    assert f"Monthly price belongs to product prod_a, but STRIPE_PRODUCT_PRO is {PRODUCT_PRO}" in summary
    assert f"Yearly price belongs to product ?, but STRIPE_PRODUCT_PRO is {PRODUCT_PRO}" in summary


def test_billing_check_passes_with_the_portal_verified_and_the_webhook_not_checked(
    sandbox: Callable[..., Harness],
) -> None:
    h = sandbox()
    body = check(h, "billing")
    assert body["outcome"] == "passed", body
    assert "Customer Portal default configuration matches DEC-010" in body["summary"]
    assert "Webhook endpoint and signing secret: not checked" in body["summary"]
    assert "describe_portal" in h.billing.calls


@pytest.mark.parametrize(
    ("portal", "expected"),
    [
        (None, "no default Customer Portal configuration"),
        (_portal(payment_method_update=False), "update their payment method"),
        (_portal(invoice_history=False), "invoice history"),
        (_portal(cancel_mode="immediately"), "cancels immediately"),
        (_portal(cancel_enabled=False), "does not let customers cancel"),
        (_portal(switch_enabled=False), "switch between the Pro prices"),
        (_portal(switch_prices=frozenset({PRICE_MONTHLY})), f"cannot switch to the pro_yearly price {PRICE_YEARLY}"),
        (
            _portal(switch_prices=frozenset({PRICE_MONTHLY, PRICE_YEARLY, "price_extra"})),
            "also offers price_extra, which checkout does not sell",
        ),
    ],
)
def test_billing_check_flags_a_portal_that_departs_from_the_policy(
    sandbox: Callable[..., Harness], portal: PortalConfiguration | None, expected: str
) -> None:
    h = sandbox()
    h.billing.set_portal(portal)
    body = check(h, "billing")
    assert body["outcome"] == "failed"
    assert body["error_code"] == "portal_mismatch"
    assert expected in body["summary"]


def test_billing_check_reports_prices_and_portal_together(sandbox: Callable[..., Harness]) -> None:
    h = sandbox()
    h.billing.set_price(_price(PRICE_MONTHLY, "month", active=False))
    h.billing.set_portal(_portal(invoice_history=False))
    body = check(h, "billing")
    assert body["error_code"] == "price_mismatch"
    assert "archived" in body["summary"]
    assert "invoice history" in body["summary"]


def test_billing_check_says_so_when_the_key_cannot_read_the_portal(sandbox: Callable[..., Harness]) -> None:
    """A restricted key without portal access still verifies the prices; the portal is reported as not checked."""
    h = sandbox()
    h.billing.set_portal(ForbiddenError("The Stripe API key cannot read Customer Portal configurations."))
    body = check(h, "billing")
    assert body["outcome"] == "passed"
    assert "Customer Portal: not checked" in body["summary"]


def test_billing_check_reports_unknown_price(sandbox: Callable[..., Harness]) -> None:
    h = sandbox({"stripe_price_pro_yearly": "price_missing"})
    body = check(h, "billing")
    assert body["outcome"] == "failed"
    assert body["error_code"] == "invalid_request"


def test_llm_check_reports_provider_failure(sandbox: Callable[..., Harness]) -> None:
    h = sandbox(llm_provider=MockLLMProvider(ok=False, error_code="auth_failed"))
    body = check(h, "llm")
    assert body["outcome"] == "failed"
    assert body["error_code"] == "auth_failed"


def test_analytics_check_sends_allowlisted_event_and_detects_export_failure(
    sandbox: Callable[..., Harness],
) -> None:
    h = sandbox()
    assert check(h, "analytics")["outcome"] == "passed"
    assert [(event, props) for _, event, props in h.analytics.events] == [("integration_test_sent", {"source": "api"})]
    h.analytics.export_ok = False
    body = check(h, "analytics")
    assert body["outcome"] == "failed"
    assert body["error_code"] == "export_failed"
    # The check cannot tell a failed export from a queue that did not drain, so it names both.
    assert body["summary"] == (
        "Could not confirm that PostHog accepted the test event: an export failed, "
        "or this API instance's analytics queue did not drain within the flush timeout."
    )


def test_error_reporting_check_returns_event_reference(sandbox: Callable[..., Harness]) -> None:
    reporter = FakeErrorReporter()
    h = sandbox(error_reporter=reporter)
    body = check(h, "error_reporting")
    assert body["outcome"] == "sent"
    assert body["reference"] == reporter.event_id
    assert reporter.sent == 1

    h2 = sandbox(error_reporter=FakeErrorReporter(event_id=None))
    assert check(h2, "error_reporting")["outcome"] == "failed"


def audit_rows(h: Harness) -> list[tuple[Any, ...]]:
    return superuser_query(
        h.database_url,
        "SELECT u.clerk_user_id, a.org_id, a.action, a.target_type, a.target_id, a.outcome, a.request_id "
        "FROM app.audit_log a LEFT JOIN app.users u ON u.id = a.actor_user_id ORDER BY a.id",
    )


def test_every_run_and_every_non_operator_attempt_is_audited(sandbox: Callable[..., Harness]) -> None:
    """ADM-01: operator tooling records the actor, action, target, outcome, and request ID of each run."""
    h = sandbox(
        {"diagnostics_operator_ids": "user_ops"}, llm_provider=MockLLMProvider(ok=False, error_code="auth_failed")
    )
    operator, outsider = h.login("user_ops"), h.login("user_a")

    def run(key: str, headers: dict[str, str], request_id: str) -> int:
        return h.client.post(f"{BASE}/{key}/check", headers={**headers, "X-Request-ID": request_id}).status_code

    assert run("database", operator, "req-diag-0001") == 200  # passed
    assert run("llm", operator, "req-diag-0002") == 200  # the provider rejected the key: failed
    assert run("database", outsider, "req-diag-0003") == 404  # still indistinguishable from a missing route
    assert h.client.get(BASE, headers=outsider).status_code == 404  # reading the report runs nothing
    assert audit_rows(h) == [
        ("user_ops", None, "diagnostics:run", "integration", "database", "succeeded", "req-diag-0001"),
        ("user_ops", None, "diagnostics:run", "integration", "llm", "failed", "req-diag-0002"),
        ("user_a", None, "diagnostics:run", "integration", "database", "denied", "req-diag-0003"),
    ]


def test_refused_attempts_are_audited_only_up_to_the_per_user_limit(make_harness: Callable[..., Harness]) -> None:
    """Anyone signed in can be refused, so refusals must not grow the kept-forever audit log without bound."""
    h = make_harness(
        {
            "diagnostics_operator_ids": "user_ops",
            "diagnostics_runs_per_user_per_minute": 2,
            "diagnostics_runs_global_per_day": 1,
        }
    )
    outsider = h.login("user_a")

    def attempts(n: int) -> list[int]:
        return [h.client.post(f"{BASE}/database/check", headers=outsider).status_code for _ in range(n)]

    def denied() -> int:
        return sum(1 for row in audit_rows(h) if row[5] == "denied")

    assert attempts(3) == [404, 404, 404]  # past the limit too: a 429 would reveal the route
    assert denied() == 2
    h.clock.advance(minutes=1)
    assert attempts(1) == [404]
    assert denied() == 3
    # Refusals spend neither an operator's limit nor the global budget.
    assert h.client.post(f"{BASE}/database/check", headers=h.login("user_ops")).status_code == 200


def test_checks_are_rate_limited_per_user(make_harness: Callable[..., Harness]) -> None:
    h = make_harness({**OPERATORS, "diagnostics_runs_per_user_per_minute": 2})
    headers = h.login("user_a")
    assert h.client.post(f"{BASE}/database/check", headers=headers).status_code == 200
    assert h.client.post(f"{BASE}/database/check", headers=headers).status_code == 200
    limited = h.client.post(f"{BASE}/database/check", headers=headers)
    assert limited.status_code == 429
    assert limited.headers["retry-after"]
    # Another user is unaffected.
    assert h.client.post(f"{BASE}/database/check", headers=h.login("user_b")).status_code == 200


def test_disabled_diagnostics_are_not_found(make_harness: Callable[..., Harness]) -> None:
    h = make_harness({**OPERATORS, "diagnostics_enabled": False})
    headers = h.login("user_a")
    assert h.client.get(BASE, headers=headers).status_code == 404
    assert h.client.post(f"{BASE}/database/check", headers=headers).status_code == 404


def test_unknown_integration_is_rejected(operator_harness: Harness) -> None:
    response = operator_harness.client.post(f"{BASE}/smtp/check", headers=operator_harness.login("user_a"))
    assert response.status_code == 422
    assert json.loads(response.text)["code"] == "VALIDATION_FAILED"


def test_operator_ids_parsed_and_local_default_is_open_only_locally() -> None:
    from app.modules.diagnostics.settings import open_to_any_signed_in_user
    from tests.conftest import make_settings

    def s(**kw: Any) -> Settings:
        return Settings.model_construct(**kw)

    assert make_settings(diagnostics_operator_ids=" user_1, ,user_2 ").diagnostics_operator_ids == ["user_1", "user_2"]
    local_open = s(app_env=AppEnv.LOCAL, diagnostics_operator_ids=[])
    assert open_to_any_signed_in_user(local_open, local_open)
    local_listed = s(app_env=AppEnv.LOCAL, diagnostics_operator_ids=["user_1"])
    assert not open_to_any_signed_in_user(local_listed, local_listed)
    for env in (AppEnv.TEST, AppEnv.PREVIEW, AppEnv.PRODUCTION):
        other = s(app_env=env, diagnostics_operator_ids=[])
        assert not open_to_any_signed_in_user(other, other)


def test_diagnostics_default_off_in_production_only() -> None:
    from app.modules.diagnostics.settings import diagnostics_active

    def s(**kw: Any) -> Settings:
        return Settings.model_construct(**kw)

    prod_default = s(app_env=AppEnv.PRODUCTION, diagnostics_enabled=None)
    assert diagnostics_active(prod_default, prod_default) is False
    prod_on = s(app_env=AppEnv.PRODUCTION, diagnostics_enabled=True)
    assert diagnostics_active(prod_on, prod_on) is True
    for env in (AppEnv.LOCAL, AppEnv.PREVIEW, AppEnv.TEST):
        other = s(app_env=env, diagnostics_enabled=None)
        assert diagnostics_active(other, other) is True
