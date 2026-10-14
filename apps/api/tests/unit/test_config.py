"""Configuration validation and key/mode safety (ENV-02 to ENV-05)."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from app.kernel.infrastructure.config import ConfigError
from app.modules.identity.settings import clerk_issuer_from_publishable_key
from tests.conftest import make_settings

SECRET_MARKER = "sk_" + "live_SHOULDNEVERAPPEAR123"  # built at runtime to avoid tripping secret scanners


def pk(host: str, live: bool = False) -> str:
    return ("pk_live_" if live else "pk_test_") + base64.b64encode(f"{host}$".encode()).decode()


LOCAL_CLERK = {
    "app_env": "local",
    "clerk_publishable_key": pk("example-app.clerk.accounts.dev"),
    "clerk_secret_key": "sk_test_abcdefghijklmnop",
    "clerk_authorized_parties": "http://localhost:3000",
}


def test_issuer_derivation() -> None:
    assert clerk_issuer_from_publishable_key(pk("example-app.clerk.accounts.dev")) == (
        "https://example-app.clerk.accounts.dev"
    )
    assert clerk_issuer_from_publishable_key("pk_test_%%%") is None
    assert clerk_issuer_from_publishable_key("not-a-key") is None


def test_test_env_defaults_are_valid() -> None:
    settings = make_settings()
    assert settings.app_env.value == "test"


def test_local_env_requires_clerk() -> None:
    with pytest.raises(ConfigError, match="CLERK_SECRET_KEY is required"):
        make_settings(app_env="local")


def test_live_mode_rejected_outside_production() -> None:
    with pytest.raises(ConfigError, match="BILLING_MODE=live is only allowed"):
        make_settings(billing_mode="live")


def test_live_stripe_key_in_sandbox_mode_rejected_without_echoing_value() -> None:
    with pytest.raises(ConfigError) as excinfo:
        make_settings(billing_mode="sandbox", stripe_secret_key=SECRET_MARKER)
    message = str(excinfo.value)
    assert "STRIPE_SECRET_KEY does not match BILLING_MODE=sandbox" in message
    assert SECRET_MARKER not in message


def test_live_clerk_key_rejected_outside_production() -> None:
    with pytest.raises(ConfigError, match="live key"):
        make_settings(**{**LOCAL_CLERK, "clerk_secret_key": "sk_" + "live_abcdefghijklmnop"})


def test_production_rejects_mock_and_sandbox_modes() -> None:
    with pytest.raises(ConfigError) as excinfo:
        make_settings(app_env="production", app_base_url="https://app.example.com")
    message = str(excinfo.value)
    assert "BILLING_MODE=mock is not allowed" in message
    assert "LLM_MODE=mock is not allowed" in message


def test_sandbox_billing_requires_prices_and_webhook_secret() -> None:
    with pytest.raises(ConfigError) as excinfo:
        make_settings(
            billing_mode="sandbox",
            stripe_secret_key="sk_test_abcdefghijklmnop",
            stripe_webhook_secret="",
            stripe_price_pro_monthly="",
        )
    message = str(excinfo.value)
    assert "STRIPE_WEBHOOK_SECRET" in message
    assert "STRIPE_PRICE_PRO_MONTHLY" in message


def test_authorized_parties_parsed_from_comma_list() -> None:
    settings = make_settings(**{**LOCAL_CLERK, "clerk_authorized_parties": "http://localhost:3000/, https://a.example"})
    assert settings.clerk_authorized_parties == ["http://localhost:3000", "https://a.example"]


def test_type_errors_do_not_echo_input() -> None:
    with pytest.raises(ConfigError) as excinfo:
        make_settings(database_pool_max_size=SECRET_MARKER)
    assert SECRET_MARKER not in str(excinfo.value)
    assert "DATABASE_POOL_MAX_SIZE" in str(excinfo.value)


def test_empty_values_mean_unset() -> None:
    settings = make_settings(clerk_jwt_key="", clerk_webhook_signing_secret="  ", llm_api_key="", sentry_dsn="")
    assert settings.clerk_jwt_key is None
    assert settings.clerk_webhook_signing_secret is None
    assert settings.llm_api_key is None
    assert settings.sentry_dsn is None


def test_env_example_placeholders_fail_clearly(tmp_path: Path) -> None:
    """A fresh copy of .env.example must fail fast with names, not run with placeholders."""
    from app.kernel.infrastructure.config import REPO_ROOT
    from app.settings import load_settings

    with pytest.raises(ConfigError) as excinfo:
        load_settings(_env_file=REPO_ROOT / ".env.example")
    assert "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY is malformed" in str(excinfo.value)
