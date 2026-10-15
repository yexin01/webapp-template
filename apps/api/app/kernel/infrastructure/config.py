"""Core settings shared by every module (ENV-02 to ENV-05).

Each module declares its own settings next to its code
(``app/modules/<name>/settings.py``), and ``app.settings`` composes them into
one validated ``Settings`` class. Settings are read once at startup from the
process environment and, for local development, from the repository-root
``.env`` file. Validation failures name the offending variables but never
print their values.
"""

from __future__ import annotations

import logging
from enum import StrEnum
from pathlib import Path

import psycopg
from psycopg.conninfo import conninfo_to_dict
from pydantic import AliasChoices, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.kernel.infrastructure.db import REQUEST_THREADS

# Both pools together leave request threads for threads waiting for a connection and
# for bounded blocking work, such as billing synchronization across Stripe calls.
POOLS_MAX_TOTAL = REQUEST_THREADS - 10

# In a repository checkout this file is apps/api/app/kernel/infrastructure/config.py,
# five levels below the root, which holds pnpm-workspace.yaml. A deployed bundle
# holds only the API package (index.py and app/ at the function root), so it has
# no repository root and no .env file (DEC-001). Symlinks are kept, not resolved,
# so a symlinked checkout still finds its root.
_HERE = Path(__file__).absolute()
_CANDIDATE = _HERE.parents[5] if len(_HERE.parents) > 5 else None
REPO_ROOT: Path | None = (
    _CANDIDATE if _CANDIDATE is not None and (_CANDIDATE / "pnpm-workspace.yaml").is_file() else None
)
if REPO_ROOT is not None and not (REPO_ROOT / "supabase" / "migrations").is_dir():  # pragma: no cover - layout guard
    raise RuntimeError(f"repository root not found from {__file__}")
ENV_FILE: Path | None = REPO_ROOT / ".env" if REPO_ROOT is not None else None


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


class ConfigError(RuntimeError):
    """Raised for invalid configuration. Messages contain names only."""


def secret(value: SecretStr | None) -> str:
    return value.get_secret_value() if value is not None else ""


DATABASE_URL_INVALID = (
    "DATABASE_URL is not a valid PostgreSQL connection string (percent-encode special characters in the password)"
)


def database_url_is_valid(url: str) -> bool:
    """Whether libpq can parse ``url`` into what it looks like. Never report why: the reason quotes the URL.

    A malformed URL fails at every connection attempt with an error that
    quotes the password, and some that parse put part of the password in the
    port, the host, or the database name, which connection errors quote too.
    An unencoded ``/`` in the password ends the host part early, so the rest
    of the password and the real host land in the database name, after an
    ``@`` that a database name never needs.
    """
    try:
        params = conninfo_to_dict(url)
    except psycopg.Error:
        return False
    ports = str(params.get("port") or "").split(",")
    return all(not p or (p.isascii() and p.isdigit()) for p in ports) and not any(
        "@" in str(params.get(key) or "") for key in ("host", "dbname")
    )


def mode_problems(name: str, mode: Mode, app_env: AppEnv) -> list[str]:
    """The environment rules every integration mode follows."""
    production = app_env is AppEnv.PRODUCTION
    if mode is Mode.LIVE and not production:
        return [f"{name}=live is only allowed when APP_ENV=production"]
    if production and mode in (Mode.MOCK, Mode.SANDBOX):
        return [f"{name}={mode.value} is not allowed when APP_ENV=production"]
    return []


class CoreSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        hide_input_in_errors=True,
        # Empty means unset in the environment too, so an empty name never hides its alias (SENTRY_RELEASE).
        env_ignore_empty=True,
    )

    app_env: AppEnv = AppEnv.LOCAL
    app_base_url: str = "http://localhost:3000"
    log_level: str = "INFO"
    max_request_body_bytes: int = Field(default=1_000_000, ge=1_024, le=4_000_000)

    # Database (DEC-008). Two pools: request work (app_api) and system work (app_worker).
    database_url: SecretStr | None = None
    # Well below the request threads (40, db.REQUEST_THREADS): admitted scoped
    # requests plus threads waiting for a connection must fit in them.
    database_pool_max_size: int = Field(default=5, ge=1, le=30)
    # At least two: billing synchronization may hold all but one (BILL-10). At most
    # ten: each of those slots holds a request thread across Stripe calls.
    database_worker_pool_max_size: int = Field(default=3, ge=2, le=10)
    database_connect_timeout_seconds: float = Field(default=5.0, gt=0, le=30)
    database_statement_timeout_ms: int = Field(default=10_000, ge=100, le=60_000)

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
    # OBS-02. A deployed bundle has no git checkout, and sentry-sdk does not read Vercel's variables.
    sentry_release: str | None = Field(
        default=None, validation_alias=AliasChoices("SENTRY_RELEASE", "VERCEL_GIT_COMMIT_SHA")
    )

    # Jobs and the outbox (ADR-0003). Without CRON_SECRET the job endpoint is off.
    cron_secret: SecretStr | None = None
    outbox_max_attempts: int = Field(default=10, ge=1, le=100)
    # Longer than any function may run (Vercel: 300 s by default, up to 800 s),
    # so a lease expires only once its worker is gone.
    outbox_lease_seconds: int = Field(default=900, ge=60, le=86_400)
    outbox_retention_days: int = Field(default=7, ge=1, le=365)

    @model_validator(mode="before")
    @classmethod
    def _empty_means_unset(cls, data: object) -> object:
        # `KEY=` in .env means "not set", never an empty secret.
        if isinstance(data, dict):
            for key in list(data):
                if isinstance(data[key], str) and not data[key].strip():
                    del data[key]
        return data


def core_problems(s: CoreSettings) -> list[str]:
    problems: list[str] = []
    production = s.app_env is AppEnv.PRODUCTION
    if ENV_FILE is None and "app_env" not in s.model_fields_set:
        # A deployed bundle has no .env. Falling back to `local` there would
        # allow mock integrations and open diagnostics to every signed-in user.
        problems.append("APP_ENV is required outside a repository checkout (set preview or production)")
    problems += mode_problems("ANALYTICS_MODE", s.analytics_mode, s.app_env)
    problems += mode_problems("ERROR_REPORTING_MODE", s.error_reporting_mode, s.app_env)
    if s.log_level.upper() not in logging.getLevelNamesMapping():  # as configure_logging passes it
        problems.append("LOG_LEVEL must be one of DEBUG, INFO, WARNING, ERROR, CRITICAL")
    if s.database_url is None:
        problems.append("DATABASE_URL is required")
    elif not database_url_is_valid(secret(s.database_url)):
        problems.append(DATABASE_URL_INVALID)
    if s.database_pool_max_size + s.database_worker_pool_max_size > POOLS_MAX_TOTAL:
        problems.append(f"DATABASE_POOL_MAX_SIZE + DATABASE_WORKER_POOL_MAX_SIZE must be at most {POOLS_MAX_TOTAL}")
    if not s.app_base_url.startswith(("http://", "https://")):
        problems.append("APP_BASE_URL must be an absolute http(s) URL")
    if production and not s.app_base_url.startswith("https://"):
        problems.append("APP_BASE_URL must use https in production")
    if s.analytics_mode is Mode.MOCK:
        problems.append("ANALYTICS_MODE must be disabled, sandbox, or live")
    if s.analytics_mode in (Mode.SANDBOX, Mode.LIVE) and not s.posthog_project_api_key:
        problems.append("NEXT_PUBLIC_POSTHOG_KEY is required when ANALYTICS_MODE is enabled")
    if s.error_reporting_mode is Mode.MOCK:
        problems.append("ERROR_REPORTING_MODE must be disabled, sandbox, or live")
    if s.error_reporting_mode in (Mode.SANDBOX, Mode.LIVE) and s.sentry_dsn is None:
        problems.append("SENTRY_DSN is required when ERROR_REPORTING_MODE is enabled")
    if s.cron_secret is not None and len(secret(s.cron_secret)) < 32:
        problems.append("CRON_SECRET must be at least 32 characters")
    return problems


class DatabaseSettings(BaseSettings):
    """Only what database maintenance commands need."""

    model_config = SettingsConfigDict(
        env_file=ENV_FILE, env_file_encoding="utf-8", extra="ignore", hide_input_in_errors=True
    )

    database_url: SecretStr | None = None
    # No default: destructive commands need APP_ENV set explicitly (ENV-05).
    app_env: AppEnv | None = None


def require_development_environment(operation: str) -> None:
    """ENV-05: development and test commands run only when APP_ENV is explicitly local or test.

    An unset APP_ENV does not count as local here: a shell that holds a remote
    DATABASE_URL, but no APP_ENV, must not pass.
    """
    try:
        app_env = DatabaseSettings().app_env
    except ValueError as exc:
        raise ConfigError("Invalid configuration: APP_ENV") from exc
    if app_env is None:
        raise ConfigError(
            f"Refusing to {operation}: APP_ENV is not set. Set APP_ENV=local (or test) in .env or the environment."
        )
    if app_env not in (AppEnv.LOCAL, AppEnv.TEST):
        raise ConfigError(f"Refusing to {operation}: APP_ENV={app_env.value}; this command needs local or test.")


def load_database_url() -> str:
    url = DatabaseSettings().database_url
    if url is None or not url.get_secret_value().strip():
        raise ConfigError("Invalid configuration: DATABASE_URL is required")
    if not database_url_is_valid(url.get_secret_value()):
        raise ConfigError(f"Invalid configuration: {DATABASE_URL_INVALID}")
    return url.get_secret_value()
