"""Centralized, validated configuration (ENV-02 to ENV-05).

Settings are read once at startup from the process environment and, for local
development, from the repository-root ``.env`` file. Validation failures name
the offending variables but never print their values.
"""

from __future__ import annotations

import base64
import binascii
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Self

from pydantic import AliasChoices, Field, SecretStr, ValidationError, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[4]


class AppEnv(StrEnum):
    LOCAL = "local"
    TEST = "test"
    PREVIEW = "preview"
    PRODUCTION = "production"


class Mode(StrEnum):
    """Integration mode for one external provider."""

    DISABLED = "disabled"
    MOCK = "mock"
    SANDBOX = "sandbox"
    LIVE = "live"


class LLMProviderName(StrEnum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"


class ConfigError(RuntimeError):
    """Raised for invalid configuration. Messages contain names only."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        hide_input_in_errors=True,
    )

    app_env: AppEnv = AppEnv.LOCAL
    app_base_url: str = "http://localhost:3000"
    log_level: str = "INFO"
    max_request_body_bytes: int = Field(default=1_000_000, ge=1_024, le=4_000_000)

    # Database (DEC-008)
    database_url: SecretStr | None = None
    database_pool_max_size: int = Field(default=5, ge=1, le=50)
    database_connect_timeout_seconds: float = Field(default=5.0, gt=0, le=30)
    database_statement_timeout_ms: int = Field(default=10_000, ge=100, le=60_000)

    # Clerk (authentication + organization directory, DEC-009)
    clerk_publishable_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("CLERK_PUBLISHABLE_KEY", "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY"),
    )
    clerk_secret_key: SecretStr | None = None
    clerk_jwt_key: SecretStr | None = None
    clerk_authorized_parties: Annotated[list[str], NoDecode] = Field(default_factory=list)
    clerk_webhook_signing_secret: SecretStr | None = None
    clerk_api_url: str = "https://api.clerk.com"
    membership_max_staleness_seconds: int = Field(default=300, ge=0, le=86_400)

    # Stripe (DEC-010)
    billing_mode: Mode = Mode.MOCK
    stripe_secret_key: SecretStr | None = None
    stripe_webhook_secret: SecretStr | None = None
    stripe_price_pro_monthly: str | None = None
    stripe_price_pro_yearly: str | None = None
    checkouts_per_user_per_hour: int = Field(default=10, ge=1, le=1_000)

    # LLM (DEC-006: provider chosen by the adopter)
    llm_mode: Mode = Mode.MOCK
    llm_provider: LLMProviderName = LLMProviderName.ANTHROPIC
    llm_api_key: SecretStr | None = None
    llm_model: str | None = None
    llm_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    llm_checks_per_user_per_minute: int = Field(default=5, ge=1, le=600)
    llm_checks_global_per_day: int = Field(default=200, ge=1, le=100_000)

    # Telemetry
    analytics_mode: Mode = Mode.DISABLED
    posthog_project_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("POSTHOG_PROJECT_API_KEY", "NEXT_PUBLIC_POSTHOG_KEY"),
    )
    posthog_host: str = Field(
        default="https://us.i.posthog.com",
        validation_alias=AliasChoices("POSTHOG_HOST", "NEXT_PUBLIC_POSTHOG_HOST"),
    )
    error_reporting_mode: Mode = Mode.DISABLED
    sentry_dsn: SecretStr | None = None
    sentry_traces_sample_rate: float = Field(default=0.0, ge=0.0, le=1.0)

    # Integration diagnostics page. Unset means enabled everywhere except production.
    diagnostics_enabled: bool | None = None
    diagnostics_runs_per_user_per_minute: int = Field(default=20, ge=1, le=600)
    diagnostics_runs_global_per_day: int = Field(default=1_000, ge=1, le=100_000)

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, data: object) -> object:
        if isinstance(data, dict):
            for key in list(data):
                # `KEY=` in .env means "not set", never an empty secret.
                if isinstance(data[key], str) and not data[key].strip():
                    del data[key]
                    continue
                if key.lower() == "clerk_authorized_parties" and isinstance(data[key], str):
                    data[key] = [p.strip().rstrip("/") for p in data[key].split(",") if p.strip()]
        return data

    @model_validator(mode="after")
    def _validate_consistency(self) -> Self:
        problems: list[str] = []
        production = self.app_env is AppEnv.PRODUCTION

        modes = {
            "BILLING_MODE": self.billing_mode,
            "LLM_MODE": self.llm_mode,
            "ANALYTICS_MODE": self.analytics_mode,
            "ERROR_REPORTING_MODE": self.error_reporting_mode,
        }
        for name, mode in modes.items():
            if mode is Mode.LIVE and not production:
                problems.append(f"{name}=live is only allowed when APP_ENV=production")
            if production and mode is Mode.MOCK:
                problems.append(f"{name}=mock is not allowed when APP_ENV=production")
            if production and mode is Mode.SANDBOX:
                problems.append(f"{name}=sandbox is not allowed when APP_ENV=production")
        if self.billing_mode is Mode.DISABLED:
            problems.append("BILLING_MODE must be mock, sandbox, or live")
        if self.llm_mode is Mode.DISABLED:
            problems.append("LLM_MODE must be mock, sandbox, or live")

        if self.database_url is None:
            problems.append("DATABASE_URL is required")

        if self.app_env is not AppEnv.TEST:
            problems += self._clerk_problems(production)
        problems += self._stripe_problems(production)
        problems += self._llm_problems()
        problems += self._telemetry_problems()

        if not self.app_base_url.startswith(("http://", "https://")):
            problems.append("APP_BASE_URL must be an absolute http(s) URL")
        if production and not self.app_base_url.startswith("https://"):
            problems.append("APP_BASE_URL must use https in production")

        if problems:
            raise ValueError("; ".join(problems))
        return self

    def _clerk_problems(self, production: bool) -> list[str]:
        problems: list[str] = []
        if not self.clerk_publishable_key:
            problems.append("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY is required")
        elif clerk_issuer_from_publishable_key(self.clerk_publishable_key) is None:
            problems.append("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY is malformed")
        if self.clerk_secret_key is None:
            problems.append("CLERK_SECRET_KEY is required")
        if not self.clerk_authorized_parties:
            problems.append("CLERK_AUTHORIZED_PARTIES is required (comma-separated frontend origins)")
        live_prefix = ("pk_live_", "sk_live_")
        keys = {
            "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY": self.clerk_publishable_key or "",
            "CLERK_SECRET_KEY": _secret(self.clerk_secret_key),
        }
        for name, value in keys.items():
            if value and value.startswith(live_prefix) and not production:
                problems.append(f"{name} is a live key; live keys are only allowed when APP_ENV=production")
            if value and production and not value.startswith(live_prefix):
                problems.append(f"{name} must be a live key when APP_ENV=production")
        return problems

    def _stripe_problems(self, production: bool) -> list[str]:
        if self.billing_mode is Mode.MOCK:
            return []
        problems: list[str] = []
        key = _secret(self.stripe_secret_key)
        expected = ("sk_live_", "rk_live_") if self.billing_mode is Mode.LIVE else ("sk_test_", "rk_test_")
        if not key:
            problems.append("STRIPE_SECRET_KEY is required when BILLING_MODE is sandbox or live")
        elif not key.startswith(expected):
            problems.append(f"STRIPE_SECRET_KEY does not match BILLING_MODE={self.billing_mode.value}")
        if not _secret(self.stripe_webhook_secret).startswith("whsec_"):
            problems.append("STRIPE_WEBHOOK_SECRET is required and must start with whsec_")
        for name, value in (
            ("STRIPE_PRICE_PRO_MONTHLY", self.stripe_price_pro_monthly),
            ("STRIPE_PRICE_PRO_YEARLY", self.stripe_price_pro_yearly),
        ):
            if not value or not value.startswith("price_"):
                problems.append(f"{name} is required and must be a Stripe price ID")
        return problems

    def _llm_problems(self) -> list[str]:
        if self.llm_mode is Mode.MOCK:
            return []
        problems: list[str] = []
        if self.llm_api_key is None:
            problems.append("LLM_API_KEY is required when LLM_MODE is sandbox or live")
        if not self.llm_model:
            problems.append("LLM_MODEL is required when LLM_MODE is sandbox or live")
        return problems

    def _telemetry_problems(self) -> list[str]:
        problems: list[str] = []
        if self.analytics_mode is Mode.MOCK:
            problems.append("ANALYTICS_MODE must be disabled, sandbox, or live")
        if self.analytics_mode in (Mode.SANDBOX, Mode.LIVE) and not self.posthog_project_api_key:
            problems.append("NEXT_PUBLIC_POSTHOG_KEY is required when ANALYTICS_MODE is enabled")
        if self.error_reporting_mode is Mode.MOCK:
            problems.append("ERROR_REPORTING_MODE must be disabled, sandbox, or live")
        if self.error_reporting_mode in (Mode.SANDBOX, Mode.LIVE) and self.sentry_dsn is None:
            problems.append("SENTRY_DSN is required when ERROR_REPORTING_MODE is enabled")
        return problems

    @property
    def diagnostics_active(self) -> bool:
        if self.diagnostics_enabled is None:
            return self.app_env is not AppEnv.PRODUCTION
        return self.diagnostics_enabled

    @property
    def clerk_issuer(self) -> str | None:
        if not self.clerk_publishable_key:
            return None
        return clerk_issuer_from_publishable_key(self.clerk_publishable_key)


def _secret(value: SecretStr | None) -> str:
    return value.get_secret_value() if value is not None else ""


def clerk_issuer_from_publishable_key(key: str) -> str | None:
    """Derive the Clerk Frontend API origin, which is the token issuer.

    A publishable key is ``pk_(test|live)_`` followed by the base64-encoded
    Frontend API host and a trailing ``$``.
    """
    for prefix in ("pk_test_", "pk_live_"):
        if key.startswith(prefix):
            encoded = key[len(prefix) :]
            break
    else:
        return None
    try:
        decoded = base64.b64decode(encoded + "=" * (-len(encoded) % 4)).decode("ascii")
    except (binascii.Error, UnicodeDecodeError):
        return None
    host = decoded.rstrip("$")
    if not host or "/" in host or " " in host:
        return None
    return f"https://{host}"


class DatabaseSettings(BaseSettings):
    """Only what database maintenance commands need."""

    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore", hide_input_in_errors=True
    )

    database_url: SecretStr | None = None


def load_database_url() -> str:
    url = DatabaseSettings().database_url
    if url is None or not url.get_secret_value().strip():
        raise ConfigError("Invalid configuration: DATABASE_URL is required")
    return url.get_secret_value()


def load_settings(**overrides: object) -> Settings:
    """Load settings, converting validation errors into name-only messages."""
    try:
        return Settings(**overrides)  # type: ignore[arg-type]
    except ValidationError as exc:
        details: list[str] = []
        for err in exc.errors(include_input=False, include_url=False):
            loc = ".".join(str(part) for part in err["loc"]).upper()
            msg = str(err["msg"]).removeprefix("Value error, ")
            details.append(f"{loc}: {msg}" if loc else msg)
        raise ConfigError("Invalid configuration: " + " | ".join(details)) from None
