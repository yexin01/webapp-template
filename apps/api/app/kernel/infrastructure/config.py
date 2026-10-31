"""Core settings shared by every module (ENV-02 to ENV-05).

Each module declares its own settings next to its code
(``app/modules/<name>/settings.py``), and ``app.settings`` composes them into
one validated ``Settings`` class. Settings are read once at startup from the
process environment and, for local development, from the repository-root
``.env`` file. Validation failures name the offending variables but never
print their values.
"""

from __future__ import annotations

import ipaddress
import logging
import re
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlsplit

import psycopg
import sentry_sdk.utils
from psycopg.conninfo import conninfo_to_dict
from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
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


class VercelEnv(StrEnum):
    """The environment Vercel runs a deployment in (VERCEL_ENV), at build time and at runtime."""

    PRODUCTION = "production"
    PREVIEW = "preview"
    DEVELOPMENT = "development"


# The application environment a deployment gets when APP_ENV is unset.
_APP_ENV_FROM_PLATFORM = {
    VercelEnv.PRODUCTION: AppEnv.PRODUCTION,
    VercelEnv.PREVIEW: AppEnv.PREVIEW,
    VercelEnv.DEVELOPMENT: AppEnv.LOCAL,
}


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


def database_url_invalid(name: str) -> str:
    """Both likely causes, and no more: libpq's own reason quotes the URL, password included, so it is never shown."""
    return (
        f"{name} is not a valid PostgreSQL connection string. Percent-encode special characters in the password, "
        "and remove query parameters PostgreSQL's client does not know, such as ?pgbouncer=true or "
        "connection_limit (sslmode is accepted)"
    )


DATABASE_URL_INVALID = database_url_invalid("DATABASE_URL")


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


def http_origin(value: str) -> str | None:
    """``value`` as a browser sends it in ``Origin`` (lowercase scheme and host, default port dropped).

    None unless ``value`` is a bare http(s) origin: no path, query, fragment, or credentials.
    """
    if not value or any(c.isspace() or c in "?#@" for c in value):
        return None
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        return None
    host = parts.hostname
    if parts.scheme not in ("http", "https") or not host or parts.path not in ("", "/"):
        return None
    if ":" in host:
        try:
            ipaddress.IPv6Address(host)
        except ValueError:
            return None
        host = f"[{host}]"
    elif not re.fullmatch(r"[a-z0-9_]([a-z0-9_.-]*[a-z0-9_])?", host):
        return None
    default_port = 443 if parts.scheme == "https" else 80
    return f"{parts.scheme}://{host}" + (f":{port}" if port not in (None, default_port) else "")


def is_bare_hostname(value: str) -> bool:
    """A lowercase hostname with at least one dot: no scheme, port, path, or wildcard."""
    return re.fullmatch(r"(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", value) is not None


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
    # What Vercel sets on a deployment (build and runtime). They bind app_env to the platform (ENV-01).
    vercel_env: VercelEnv | None = None
    vercel_url: str | None = None
    vercel_branch_url: str | None = None
    app_base_url: str = "http://localhost:3000"
    log_level: str = "INFO"
    max_request_body_bytes: int = Field(default=1_000_000, ge=1_024, le=4_000_000)
    # POST, PUT, PATCH and DELETE requests one signed-in user may send per minute to routes without a limit of
    # their own (SEC-09).
    writes_per_user_per_minute: int = Field(default=60, ge=1, le=600)

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
    # The least time between the starts of two runs of one job through the job endpoint (SEC-09); 0 turns it off.
    job_min_spacing_seconds: int = Field(default=60, ge=0, le=3_600)
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

    @model_validator(mode="before")
    @classmethod
    def _app_env_from_platform(cls, data: object) -> object:
        # An unset APP_ENV on a Vercel deployment is the deployment's own environment, not `local`.
        if isinstance(data, dict) and not str(data.get("app_env") or "").strip():
            try:
                platform = VercelEnv(str(data.get("vercel_env") or "").strip())
            except ValueError:
                return data  # unset, or invalid: the field reports it
            data["app_env"] = _APP_ENV_FROM_PLATFORM[platform].value
        return data

    @field_validator("app_base_url")
    @classmethod
    def _origin_form(cls, value: str) -> str:
        return http_origin(value) or value


def platform_problems(app_env: AppEnv, vercel_env: VercelEnv | None, *, bundle: bool) -> list[str]:
    """ENV-01: the application environment may not contradict the platform's. Names and enum labels only.

    ``preview`` stays valid on a Vercel production deployment: a project's first deployment is a
    production one, and so is a staging project.
    """
    if app_env is AppEnv.PRODUCTION and vercel_env in (VercelEnv.PREVIEW, VercelEnv.DEVELOPMENT):
        return [f"APP_ENV=production is not allowed when VERCEL_ENV={vercel_env.value}"]
    if app_env in (AppEnv.LOCAL, AppEnv.TEST) and vercel_env in (VercelEnv.PRODUCTION, VercelEnv.PREVIEW):
        allowed = "preview or production" if vercel_env is VercelEnv.PRODUCTION else "preview"
        return [f"APP_ENV={app_env.value} is not allowed when VERCEL_ENV={vercel_env.value} (set {allowed})"]
    if bundle and app_env is AppEnv.LOCAL and vercel_env is None:
        return ["APP_ENV=local is not allowed in a deployed bundle (set preview or production)"]
    return []


def explicit_mode_problems(s: BaseSettings) -> list[str]:
    """ENV-03: a deployment names each integration mode; only local development and tests fall back to defaults."""
    app_env = getattr(s, "app_env", None)
    if app_env not in (AppEnv.PREVIEW, AppEnv.PRODUCTION):
        return []
    return [
        f"{name.upper()} is required when APP_ENV={app_env.value}"
        for name, field in type(s).model_fields.items()
        if field.annotation is Mode and name not in s.model_fields_set
    ]


def core_problems(s: CoreSettings) -> list[str]:
    problems: list[str] = []
    production = s.app_env is AppEnv.PRODUCTION
    if ENV_FILE is None and "app_env" not in s.model_fields_set:
        # A deployed bundle has no .env. Falling back to `local` there would
        # allow mock integrations and open diagnostics to every signed-in user.
        problems.append("APP_ENV is required outside a repository checkout (set preview or production)")
    problems += platform_problems(s.app_env, s.vercel_env, bundle=ENV_FILE is None)
    problems += explicit_mode_problems(s)
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
    if s.app_env in (AppEnv.PREVIEW, AppEnv.PRODUCTION) and "app_base_url" not in s.model_fields_set:
        problems.append(f"APP_BASE_URL is required when APP_ENV={s.app_env.value}")
    elif http_origin(s.app_base_url) is None:
        problems.append("APP_BASE_URL must be an http(s) origin (scheme, host, optional port; no path or credentials)")
    elif production and not s.app_base_url.startswith("https://"):
        problems.append("APP_BASE_URL must use https in production")
    if s.analytics_mode is Mode.MOCK:
        problems.append("ANALYTICS_MODE must be disabled, sandbox, or live")
    if s.analytics_mode in (Mode.SANDBOX, Mode.LIVE) and not s.posthog_project_api_key:
        problems.append("NEXT_PUBLIC_POSTHOG_KEY is required when ANALYTICS_MODE is enabled")
    if s.posthog_project_api_key:
        # The key is inlined into every browser bundle, so a secret one must never get this far.
        if re.match(r"ph[xsar]_", s.posthog_project_api_key):
            problems.append(
                "NEXT_PUBLIC_POSTHOG_KEY (or POSTHOG_PROJECT_API_KEY) holds a secret PostHog credential "
                "(personal, secret or OAuth key); use the project token (phc_...) from Project settings"
            )
        elif not re.fullmatch(r"phc_\S+", s.posthog_project_api_key):
            problems.append(
                "NEXT_PUBLIC_POSTHOG_KEY (or POSTHOG_PROJECT_API_KEY) must be a PostHog project token (phc_...)"
            )
    host = urlsplit(s.posthog_host)
    if host.scheme not in ("http", "https") or not host.hostname:
        problems.append("NEXT_PUBLIC_POSTHOG_HOST (or POSTHOG_HOST) must be an absolute http(s) URL")
    if s.error_reporting_mode is Mode.MOCK:
        problems.append("ERROR_REPORTING_MODE must be disabled, sandbox, or live")
    if s.error_reporting_mode in (Mode.SANDBOX, Mode.LIVE) and s.sentry_dsn is None:
        problems.append("SENTRY_DSN is required when ERROR_REPORTING_MODE is enabled")
    if s.sentry_dsn is not None:
        try:
            sentry_sdk.utils.Dsn(secret(s.sentry_dsn))
        except sentry_sdk.utils.BadDsn:  # its message can quote the DSN
            problems.append("SENTRY_DSN must be a Sentry DSN (https://<key>@<host>/<id>)")
    if s.cron_secret is not None and len(secret(s.cron_secret)) < 32:
        problems.append("CRON_SECRET must be at least 32 characters")
    return problems


class DatabaseSettings(BaseSettings):
    """Only what database maintenance commands need."""

    model_config = SettingsConfigDict(
        env_file=ENV_FILE, env_file_encoding="utf-8", extra="ignore", hide_input_in_errors=True
    )

    database_url: SecretStr | None = None
    # The schema owner's connection, for migrations and local seed and reset. The running API never reads it.
    migration_database_url: SecretStr | None = None
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


def requires_restricted_login_role(app_env: AppEnv) -> bool:
    """Preview and production refuse a login role that could skip row-level security (DEC-008)."""
    return app_env in (AppEnv.PREVIEW, AppEnv.PRODUCTION)


def load_database_url() -> str:
    url = DatabaseSettings().database_url
    if url is None or not url.get_secret_value().strip():
        raise ConfigError("Invalid configuration: DATABASE_URL is required")
    if not database_url_is_valid(url.get_secret_value()):
        raise ConfigError(f"Invalid configuration: {DATABASE_URL_INVALID}")
    return url.get_secret_value()


def load_migration_database_url() -> str:
    """The connection for migrate, seed, reset, and api-login: MIGRATION_DATABASE_URL, else DATABASE_URL."""
    url = DatabaseSettings().migration_database_url
    if url is None or not url.get_secret_value().strip():
        return load_database_url()
    if not database_url_is_valid(url.get_secret_value()):
        raise ConfigError(f"Invalid configuration: {database_url_invalid('MIGRATION_DATABASE_URL')}")
    return url.get_secret_value()
