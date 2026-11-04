"""Configuration bound to the deployment platform (ENV-01, ENV-03): Vercel's environment, the origins of a
deployment, and the integration modes a deployment must name. Messages carry names and enum labels, never values."""

from __future__ import annotations

from typing import Any

import pytest

from app.kernel.infrastructure import config
from app.kernel.infrastructure.config import (
    AppEnv,
    ConfigError,
    DatabaseSettings,
    VercelEnv,
    http_origin,
    platform_problems,
    require_development_environment,
)
from app.settings import Settings, load_settings
from tests.conftest import (
    PRICE_MONTHLY,
    PRICE_YEARLY,
    PRODUCT_PRO,
    TEST_STRIPE_WEBHOOK_SECRET,
    _is_settings_variable,
)
from tests.unit.test_config import SECRET_MARKER, pk

PREVIEW_URL = "https://preview.example.com"
PRODUCTION_URL = "https://app.example.com"

# A valid configuration of a preview deployment, as keyword settings: no APP_ENV, so a test chooses how it is set.
PREVIEW: dict[str, Any] = {
    "_env_file": None,
    "database_url": "postgresql://unused:unused@127.0.0.1:1/unused",
    "app_base_url": PREVIEW_URL,
    "clerk_publishable_key": pk("example-app.clerk.accounts.dev"),
    "clerk_secret_key": "sk_test_only_abcdefghijklmnop",
    "billing_mode": "mock",
    "email_mode": "mock",
    "llm_mode": "mock",
    "analytics_mode": "disabled",
    "error_reporting_mode": "disabled",
}
# A valid configuration of a production deployment.
PRODUCTION: dict[str, Any] = {
    **PREVIEW,
    "app_env": "production",
    "app_base_url": PRODUCTION_URL,
    "clerk_publishable_key": pk("clerk.example.com", live=True),
    "clerk_secret_key": "sk_" + "live_only_abcdefghijklmnop",
    "billing_mode": "live",
    "stripe_secret_key": "sk_" + "live_only_abcdefghijklmnop",
    "stripe_webhook_secret": TEST_STRIPE_WEBHOOK_SECRET,
    "stripe_price_pro_monthly": PRICE_MONTHLY,
    "stripe_price_pro_yearly": PRICE_YEARLY,
    "stripe_product_pro": PRODUCT_PRO,
    "email_mode": "live",
    "resend_api_key": "re_" + "k" * 30,
    "email_from": "Example <noreply@example.com>",
    "llm_mode": "live",
    "llm_api_key": "llm-" + "k" * 30,
    "llm_model": "test-model",
}


def settings(base: dict[str, Any], **overrides: Any) -> Settings:
    """``base`` with overrides applied; a value of ``...`` removes the setting."""
    values = {**base, **overrides}
    return load_settings(**{k: v for k, v in values.items() if v is not ...})


def refusal(base: dict[str, Any], **overrides: Any) -> str:
    with pytest.raises(ConfigError) as excinfo:
        settings(base, **overrides)
    return str(excinfo.value)


@pytest.fixture
def bundle(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run as a deployed bundle: no repository checkout, so no .env file (DEC-001)."""
    monkeypatch.setattr(config, "ENV_FILE", None)


@pytest.mark.parametrize("name", ["VERCEL_ENV", "vercel_url", "VERCEL_BRANCH_URL"])
def test_tests_never_inherit_the_platform_variables_of_the_machine_they_run_on(name: str) -> None:
    """A CI job or a developer shell on Vercel's tooling must not change what a settings test sees."""
    assert _is_settings_variable(name)


# --- ENV-01: APP_ENV and the platform -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("app_env", "vercel_env", "problem"),
    [
        (AppEnv.PRODUCTION, VercelEnv.PREVIEW, "APP_ENV=production is not allowed when VERCEL_ENV=preview"),
        (AppEnv.PRODUCTION, VercelEnv.DEVELOPMENT, "APP_ENV=production is not allowed when VERCEL_ENV=development"),
        (
            AppEnv.LOCAL,
            VercelEnv.PREVIEW,
            "APP_ENV=local is not allowed when VERCEL_ENV=preview (set preview)",
        ),
        (
            AppEnv.TEST,
            VercelEnv.PREVIEW,
            "APP_ENV=test is not allowed when VERCEL_ENV=preview (set preview)",
        ),
        (
            AppEnv.LOCAL,
            VercelEnv.PRODUCTION,
            "APP_ENV=local is not allowed when VERCEL_ENV=production (set preview or production)",
        ),
        (
            AppEnv.TEST,
            VercelEnv.PRODUCTION,
            "APP_ENV=test is not allowed when VERCEL_ENV=production (set preview or production)",
        ),
    ],
)
def test_the_application_environment_may_not_contradict_the_platform(
    app_env: AppEnv, vercel_env: VercelEnv, problem: str
) -> None:
    assert platform_problems(app_env, vercel_env, bundle=False) == [problem]
    assert platform_problems(app_env, vercel_env, bundle=True) == [problem]


@pytest.mark.parametrize(
    ("app_env", "vercel_env"),
    [
        (AppEnv.PRODUCTION, VercelEnv.PRODUCTION),
        (AppEnv.PREVIEW, VercelEnv.PREVIEW),
        (AppEnv.PREVIEW, VercelEnv.PRODUCTION),  # a project's first deployment, or a staging project
        (AppEnv.PREVIEW, VercelEnv.DEVELOPMENT),
        (AppEnv.LOCAL, VercelEnv.DEVELOPMENT),
        (AppEnv.TEST, VercelEnv.DEVELOPMENT),
        (AppEnv.TEST, None),
        (AppEnv.PREVIEW, None),
        (AppEnv.PRODUCTION, None),
    ],
)
def test_combinations_that_stay_allowed(app_env: AppEnv, vercel_env: VercelEnv | None) -> None:
    assert platform_problems(app_env, vercel_env, bundle=False) == []
    assert platform_problems(app_env, vercel_env, bundle=True) == []


def test_a_bundle_refuses_local_unless_the_platform_says_development() -> None:
    message = "APP_ENV=local is not allowed in a deployed bundle (set preview or production)"
    assert platform_problems(AppEnv.LOCAL, None, bundle=True) == [message]
    assert platform_problems(AppEnv.LOCAL, None, bundle=False) == []
    assert platform_problems(AppEnv.LOCAL, VercelEnv.DEVELOPMENT, bundle=True) == []
    assert platform_problems(AppEnv.TEST, None, bundle=True) == []  # the bundle layout test runs it so


def test_a_deployment_that_pastes_the_local_env_is_refused() -> None:
    message = refusal(PREVIEW, app_env="local", vercel_env="preview")
    assert "APP_ENV=local is not allowed when VERCEL_ENV=preview" in message


def test_production_settings_on_a_preview_deployment_are_refused() -> None:
    message = refusal(PRODUCTION, vercel_env="preview")
    assert "APP_ENV=production is not allowed when VERCEL_ENV=preview" in message
    assert settings(PRODUCTION, vercel_env="production").app_env is AppEnv.PRODUCTION


def test_an_unset_app_env_is_taken_from_the_platform() -> None:
    assert settings(PREVIEW, vercel_env="preview").app_env is AppEnv.PREVIEW
    assert settings(PRODUCTION, app_env=..., vercel_env="production").app_env is AppEnv.PRODUCTION
    local = {**PREVIEW, "clerk_authorized_parties": "http://localhost:3000"}
    assert settings(local, app_base_url="http://localhost:3000", vercel_env="development").app_env is AppEnv.LOCAL


def test_an_explicit_app_env_wins_over_the_derived_one() -> None:
    assert settings(PREVIEW, app_env="preview", vercel_env="production").app_env is AppEnv.PREVIEW


def test_nothing_set_still_means_local() -> None:
    assert settings({**PREVIEW, "app_base_url": "http://localhost:3000"}).app_env is AppEnv.LOCAL


def test_an_empty_app_env_is_unset_so_the_platform_decides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "")
    monkeypatch.setenv("VERCEL_ENV", "preview")
    assert settings(PREVIEW).app_env is AppEnv.PREVIEW


def test_an_unknown_platform_environment_is_refused_by_name() -> None:
    message = refusal(PREVIEW, app_env="preview", vercel_env=SECRET_MARKER)
    assert "VERCEL_ENV" in message
    assert SECRET_MARKER not in message


def test_a_derived_app_env_counts_as_set_in_a_bundle(bundle: None) -> None:
    assert settings(PREVIEW, vercel_env="preview").app_env is AppEnv.PREVIEW
    assert "APP_ENV is required outside a repository checkout" in refusal(PREVIEW)


def test_a_bundle_refuses_local(bundle: None) -> None:
    local = {**PREVIEW, "app_base_url": "http://localhost:3000"}
    assert "APP_ENV=local is not allowed in a deployed bundle" in refusal(local, app_env="local")
    assert settings(local, app_env="local", vercel_env="development").app_env is AppEnv.LOCAL


def test_maintenance_commands_still_need_an_explicit_app_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """ENV-05: the platform's development environment does not stand in for APP_ENV=local."""
    monkeypatch.setitem(DatabaseSettings.model_config, "env_file", None)
    monkeypatch.setenv("VERCEL_ENV", "development")
    with pytest.raises(ConfigError, match="APP_ENV is not set"):
        require_development_environment("reset the database")
    monkeypatch.setenv("APP_ENV", "local")
    require_development_environment("reset the database")


# --- ENV-03: APP_BASE_URL and the integration modes ---------------------------------------------------------


@pytest.mark.parametrize("app_env", ["preview", "production"])
def test_a_deployment_must_set_app_base_url(app_env: str) -> None:
    base = PRODUCTION if app_env == "production" else {**PREVIEW, "app_env": "preview"}
    assert f"APP_BASE_URL is required when APP_ENV={app_env}" in refusal(base, app_base_url=...)


def test_local_and_test_keep_the_localhost_default() -> None:
    values = {**PREVIEW, "app_base_url": ...}
    assert settings(values, app_env="test").app_base_url == "http://localhost:3000"
    assert settings(values, app_env="local").app_base_url == "http://localhost:3000"


@pytest.mark.parametrize(
    "value",
    ["localhost:3000", "https://a.example/x", "https://a.example?x=1", "https://u:p@a.example", "ftp://a.example"],
)
def test_app_base_url_must_be_an_origin(value: str) -> None:
    message = refusal({**PREVIEW, "app_env": "preview"}, app_base_url=value)
    assert "APP_BASE_URL must be an http(s) origin" in message
    assert value not in message


def test_app_base_url_is_kept_in_the_form_a_browser_sends() -> None:
    values = {**PREVIEW, "app_env": "preview", "clerk_authorized_parties": "https://app.example.com"}
    assert settings(values, app_base_url="https://App.Example.com:443/").app_base_url == "https://app.example.com"


def test_production_still_requires_https() -> None:
    assert "APP_BASE_URL must use https in production" in refusal(PRODUCTION, app_base_url="http://app.example.com")


@pytest.mark.parametrize("prefix", ["sk_" + "test_", "rk_" + "test_"])
def test_live_billing_refuses_a_test_mode_stripe_key(prefix: str) -> None:
    """ENV-04: a production deployment must not start charging through a sandbox. The reverse is in test_config.py."""
    key = prefix + "x" * 24
    message = refusal(PRODUCTION, stripe_secret_key=key)
    assert "STRIPE_SECRET_KEY does not match BILLING_MODE=live" in message
    assert key not in message


@pytest.mark.parametrize("prefix", ["sk_" + "live_", "rk_" + "live_"])
def test_live_billing_takes_a_secret_or_a_restricted_live_key(prefix: str) -> None:
    assert settings(PRODUCTION, stripe_secret_key=prefix + "x" * 24).billing_mode.value == "live"


@pytest.mark.parametrize("product", [..., "", PRICE_MONTHLY, "pro"])
def test_live_billing_needs_the_pro_product_as_a_stripe_product_id(product: Any) -> None:
    assert "STRIPE_PRODUCT_PRO is required and must be a Stripe product ID" in refusal(
        PRODUCTION, stripe_product_pro=product
    )


@pytest.mark.parametrize("name", ["billing_mode", "email_mode", "llm_mode", "analytics_mode", "error_reporting_mode"])
@pytest.mark.parametrize("app_env", ["preview", "production"])
def test_a_deployment_names_every_integration_mode(name: str, app_env: str) -> None:
    base = PRODUCTION if app_env == "production" else {**PREVIEW, "app_env": "preview"}
    assert f"{name.upper()} is required when APP_ENV={app_env}" in refusal(base, **{name: ...})


def test_local_development_falls_back_to_default_modes() -> None:
    values = {**PREVIEW, "app_base_url": "http://localhost:3000"}
    local = settings(
        values,
        app_env="local",
        billing_mode=...,
        email_mode=...,
        llm_mode=...,
        analytics_mode=...,
        error_reporting_mode=...,
    )
    assert local.billing_mode.value == "mock"


# --- CLERK_AUTHORIZED_PARTIES --------------------------------------------------------------------------------


def test_authorized_parties_default_to_the_origin_of_app_base_url() -> None:
    assert settings(PREVIEW, app_env="preview").clerk_authorized_parties == [PREVIEW_URL]
    assert settings(PRODUCTION).clerk_authorized_parties == [PRODUCTION_URL]


def test_authorized_parties_are_kept_in_the_form_a_browser_sends() -> None:
    parties = "HTTPS://Preview.Example.com:443/, http://Localhost:3000, http://[::1]:3000"
    result = settings(PREVIEW, app_env="preview", clerk_authorized_parties=parties).clerk_authorized_parties
    assert result == [PREVIEW_URL, "http://localhost:3000", "http://[::1]:3000"]


@pytest.mark.parametrize(
    "entry", ["localhost:3000", "not an origin", "https://a.example/x", "https://a.example?x=1", "https://u@a.example"]
)
def test_an_authorized_party_that_is_no_origin_is_refused_by_name(entry: str) -> None:
    message = refusal({**PREVIEW, "app_env": "preview"}, clerk_authorized_parties=f"{PREVIEW_URL},{entry}")
    assert "CLERK_AUTHORIZED_PARTIES must list http(s) origins" in message
    assert entry not in message


def test_the_origin_of_app_base_url_must_be_listed() -> None:
    message = refusal({**PREVIEW, "app_env": "preview"}, clerk_authorized_parties="https://other.example.com")
    assert "CLERK_AUTHORIZED_PARTIES must list the origin of APP_BASE_URL" in message
    assert settings(PREVIEW, app_env="preview", clerk_authorized_parties=f"https://other.example.com,{PREVIEW_URL}")


def test_automated_tests_need_no_authorized_parties() -> None:
    assert settings(PREVIEW, app_env="test", clerk_authorized_parties="https://other.example.com")
    # Without a Clerk instance every token is refused, so nothing is defaulted from APP_BASE_URL either.
    assert settings(PREVIEW, app_env="test").clerk_authorized_parties == []


# --- Preview deployments: their own origins are trusted, additionally ---------------------------------------

PREVIEW_PLATFORM = {
    "app_env": "preview",
    "vercel_env": "preview",
    "vercel_url": "my-app-abc123-team.vercel.app",
    "vercel_branch_url": "my-app-git-feature-team.vercel.app",
}


def test_a_preview_deployment_trusts_its_own_and_its_branchs_origin() -> None:
    parties = settings(PREVIEW, **PREVIEW_PLATFORM).clerk_authorized_parties
    assert parties == [
        PREVIEW_URL,
        "https://my-app-abc123-team.vercel.app",
        "https://my-app-git-feature-team.vercel.app",
    ]


def test_the_derived_origins_are_added_to_the_list_and_never_replace_it() -> None:
    listed = "https://stable.example.com,https://my-app-abc123-team.vercel.app"
    values = {**PREVIEW, "app_base_url": "https://stable.example.com"}
    parties = settings(values, clerk_authorized_parties=listed, **PREVIEW_PLATFORM).clerk_authorized_parties
    assert parties == [
        "https://stable.example.com",
        "https://my-app-abc123-team.vercel.app",
        "https://my-app-git-feature-team.vercel.app",
    ]


@pytest.mark.parametrize(
    "platform",
    [
        {**PREVIEW_PLATFORM, "vercel_env": "production"},  # a staging project on Vercel production
        {**PREVIEW_PLATFORM, "vercel_env": "development"},
        {**PREVIEW_PLATFORM, "vercel_env": ...},
    ],
)
def test_other_environments_derive_nothing(platform: dict[str, Any]) -> None:
    assert settings(PREVIEW, **platform).clerk_authorized_parties == [PREVIEW_URL]


def test_local_development_derives_nothing() -> None:
    values = {**PREVIEW, "app_base_url": "http://localhost:3000"}
    platform = {**PREVIEW_PLATFORM, "app_env": "local", "vercel_env": "development"}
    assert settings(values, **platform).clerk_authorized_parties == ["http://localhost:3000"]


def test_production_derives_nothing() -> None:
    values = {"vercel_env": "production", "vercel_url": "my-app-abc123-team.vercel.app"}
    assert settings(PRODUCTION, **values).clerk_authorized_parties == [PRODUCTION_URL]


@pytest.mark.parametrize(
    "host",
    [
        "https://evil.example/x",
        "evil.example:8443",
        "evil.example/x",
        "EVIL.example.com",
        "a b.example.com",
        "localhost",
    ],
)
def test_only_bare_hostnames_are_taken_from_the_platform(host: str) -> None:
    parties = settings(PREVIEW, **{**PREVIEW_PLATFORM, "vercel_url": host}).clerk_authorized_parties
    assert parties == [PREVIEW_URL, "https://my-app-git-feature-team.vercel.app"]


def test_a_preview_that_sets_no_host_is_unchanged() -> None:
    values = {**PREVIEW_PLATFORM, "vercel_url": ..., "vercel_branch_url": ...}
    assert settings(PREVIEW, **values).clerk_authorized_parties == [PREVIEW_URL]


@pytest.mark.parametrize(
    ("value", "origin"),
    [
        ("https://app.example.com", "https://app.example.com"),
        ("https://app.example.com/", "https://app.example.com"),
        ("HTTPS://App.Example.com:443", "https://app.example.com"),
        ("http://localhost:3000", "http://localhost:3000"),
        ("http://localhost:80", "http://localhost"),
        ("http://127.0.0.1:3100", "http://127.0.0.1:3100"),
        ("http://[::1]:3000/", "http://[::1]:3000"),
        ("https://xn--bcher-kva.example", "https://xn--bcher-kva.example"),
    ],
)
def test_http_origin_gives_the_form_a_browser_sends(value: str, origin: str) -> None:
    assert http_origin(value) == origin


@pytest.mark.parametrize(
    "value",
    [
        "",
        "localhost:3000",
        "app.example.com",
        "ftp://a.example",
        "https://",
        "https://a.example//",
        "https://a.example/x",
        "https://a.example?x",
        "https://a.example#x",
        "https://u@a.example",
        "https://u:p@a.example",
        "https://a.example:notaport",
        "https://a.example:99999",
        "https://a b.example",
        "https://a.example,https://b.example",
        "http://[::1",
        "http://[nonsense]:80",
    ],
)
def test_http_origin_refuses_what_is_no_origin(value: str) -> None:
    assert http_origin(value) is None
