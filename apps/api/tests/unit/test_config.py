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
    "clerk_secret_key": "sk_test_only_abcdefghijklmnop",
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
            stripe_secret_key="sk_test_only_abcdefghijklmnop",
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


def test_the_request_pool_stays_well_below_the_request_threads() -> None:
    """Admitted scoped requests (one per request connection) plus threads waiting for a connection
    must fit in the request threads, or connection holders could find no thread to finish on. The
    threads that bounded external work may hold count too: with the default bounds and a worker pool
    of 2, 40 - 15 - (4 + 4 + 8 + 1) leaves the minimum of 8 for connection waiters."""
    settings = make_settings(database_pool_max_size=15, database_worker_pool_max_size=2)
    assert settings.database_pool_max_size == 15
    for size in (16, 28, 31):
        with pytest.raises(ConfigError) as excinfo:
            make_settings(database_pool_max_size=size, database_worker_pool_max_size=2)
        assert "DATABASE_POOL_MAX_SIZE" in str(excinfo.value)


def test_the_worker_pool_is_capped_below_the_request_threads() -> None:
    """Billing synchronization holds a request thread in each of its DATABASE_WORKER_POOL_MAX_SIZE - 1
    slots, across Stripe calls, so the worker pool cannot be large enough to take every thread."""
    assert make_settings(database_worker_pool_max_size=10).database_worker_pool_max_size == 10
    with pytest.raises(ConfigError) as excinfo:
        make_settings(database_worker_pool_max_size=11)
    assert "DATABASE_WORKER_POOL_MAX_SIZE" in str(excinfo.value)


def test_both_pools_together_leave_request_threads_for_everything_else() -> None:
    """Billing synchronization holds a request thread in all but one worker connection, so a larger
    worker pool leaves room for a smaller request pool: 40 - 7 - (4 + 4 + 8 + 9) = 8."""
    defaults = make_settings()
    assert defaults.database_pool_max_size + defaults.database_worker_pool_max_size <= 30
    assert make_settings(database_pool_max_size=7, database_worker_pool_max_size=10).database_pool_max_size == 7
    with pytest.raises(ConfigError, match="DATABASE_POOL_MAX_SIZE, DATABASE_WORKER_POOL_MAX_SIZE"):
        make_settings(database_pool_max_size=8, database_worker_pool_max_size=10)
    with pytest.raises(ConfigError) as excinfo:
        make_settings(database_pool_max_size=21, database_worker_pool_max_size=10)
    message = str(excinfo.value)
    assert "DATABASE_POOL_MAX_SIZE + DATABASE_WORKER_POOL_MAX_SIZE must be at most 30" in message
    with pytest.raises(ConfigError, match="DATABASE_POOL_MAX_SIZE \\+ DATABASE_WORKER_POOL_MAX_SIZE"):
        make_settings(database_pool_max_size=30)  # with the default worker pool of 3


MALFORMED_DATABASE_URLS = [
    # An unencoded "%" or space, or a scheme typo: libpq's parse errors quote the password.
    ("postgresql://u:pa%ssw0rdLEAKA@127.0.0.1/app", ["pa%ssw0rdLEAKA"]),
    ("postgresql://u:LEAKB SECRET@127.0.0.1/app", ["LEAKB"]),
    ("postgresql//u:LEAKCSecret@127.0.0.1/app", ["LEAKCSecret"]),
    # These parse, but put part of the password in the port, the host, or the database name,
    # which connect errors quote.
    ("postgresql://u:LEAKD/LEAKE@127.0.0.1:5432/app", ["LEAKD", "LEAKE"]),
    ("postgresql://u:LEAKF@LEAKG@127.0.0.1/app", ["LEAKF", "LEAKG"]),
    ("postgresql://u:4821/LEAKH@127.0.0.1/app", ["4821", "LEAKH"]),
    ("postgresql://u:/LEAKI@127.0.0.1:5432/app", ["LEAKI"]),
]


@pytest.mark.parametrize(("url", "secrets"), MALFORMED_DATABASE_URLS)
def test_a_malformed_database_url_is_refused_at_startup_without_echoing_it(url: str, secrets: list[str]) -> None:
    """ENV-03: the format is validated at startup, so libpq never logs a parse error quoting the password."""
    with pytest.raises(ConfigError) as excinfo:
        make_settings(database_url=url)
    message = str(excinfo.value)
    assert "DATABASE_URL is not a valid PostgreSQL connection string" in message
    for fragment in secrets:
        assert fragment not in message


@pytest.mark.parametrize(("url", "secrets"), MALFORMED_DATABASE_URLS)
def test_maintenance_commands_refuse_a_malformed_database_url_without_echoing_it(
    url: str, secrets: list[str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from app import cli
    from app.kernel.infrastructure.config import DatabaseSettings

    monkeypatch.setitem(DatabaseSettings.model_config, "env_file", None)  # never the developer's .env
    monkeypatch.setenv("DATABASE_URL", url)
    assert cli.main(["migrate", "--allow-remote"]) == 1
    output = capsys.readouterr()
    assert "DATABASE_URL is not a valid PostgreSQL connection string" in output.err
    for fragment in secrets:
        assert fragment not in output.out + output.err


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://postgres:postgres@127.0.0.1:5432/app",
        "postgres://u:pa%25ss%20word@db.example.com:6543/postgres?sslmode=require",
        "postgresql://user%40server:p%2Fw@db.example.com/app",  # an encoded "@" in the user, "/" in the password
        "postgresql://u:p@host-a:5432,host-b:5433/app",
        "postgresql://u:p@host-a,host-b/app",
        "postgresql://u:p@[::1]/app",
        "postgresql:///app?host=/var/run/postgresql",
        "host=localhost port=5432 dbname=app user=u",
    ],
)
def test_well_formed_database_urls_are_accepted(url: str) -> None:
    assert make_settings(database_url=url).database_url is not None


@pytest.mark.parametrize("level", ["verbose", "10", "trace"])
def test_an_unknown_log_level_is_refused_by_name(level: str) -> None:
    """Otherwise logging would raise its own ValueError at import of index.py, without naming LOG_LEVEL."""
    with pytest.raises(ConfigError) as excinfo:
        make_settings(log_level=level)
    message = str(excinfo.value)
    assert "LOG_LEVEL must be one of DEBUG, INFO, WARNING, ERROR, CRITICAL" in message
    assert level not in message.lower().replace("log_level", "")


@pytest.mark.parametrize("level", ["DEBUG", "info", "Warning", "warn", "error", "CRITICAL"])
def test_standard_log_levels_are_accepted_in_any_case(level: str) -> None:
    import logging

    assert make_settings(log_level=level).log_level == level
    assert level.upper() in logging.getLevelNamesMapping()  # what configure_logging passes to logging


def test_empty_values_mean_unset() -> None:
    settings = make_settings(clerk_jwt_key="", clerk_webhook_signing_secret="  ", llm_api_key="", sentry_dsn="")
    assert settings.clerk_jwt_key is None
    assert settings.clerk_webhook_signing_secret is None
    assert settings.llm_api_key is None
    assert settings.sentry_dsn is None


@pytest.mark.parametrize(
    ("field", "name", "alias", "value"),
    [
        ("sentry_release", "SENTRY_RELEASE", "VERCEL_GIT_COMMIT_SHA", "0123abcd"),
        ("posthog_project_api_key", "POSTHOG_PROJECT_API_KEY", "NEXT_PUBLIC_POSTHOG_KEY", "phc_test_only"),
        ("posthog_host", "POSTHOG_HOST", "NEXT_PUBLIC_POSTHOG_HOST", "https://eu.i.posthog.com"),
        ("clerk_publishable_key", "CLERK_PUBLISHABLE_KEY", "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", "pk_test_"),
    ],
)
def test_an_empty_variable_never_hides_its_alias(
    monkeypatch: pytest.MonkeyPatch, field: str, name: str, alias: str, value: str
) -> None:
    """Empty means unset in the process environment too: a setting read from either of two names
    takes the second when the first is present but empty."""
    monkeypatch.setenv(name, "")
    monkeypatch.setenv(alias, value)
    assert getattr(make_settings(), field) == value


def test_env_example_ships_a_pair_that_means_clerk_is_not_configured(tmp_path: Path) -> None:
    """A fresh copy of .env.example is valid, so the app starts without accounts, and says Clerk is not configured."""
    from app.kernel.infrastructure.config import REPO_ROOT
    from app.modules.identity.module import clerk_not_configured, describe
    from app.settings import load_settings

    settings = load_settings(_env_file=REPO_ROOT / ".env.example")
    assert clerk_not_configured(settings)
    assert describe(settings).mode.value == "mock"


def test_the_older_env_example_placeholders_fail_clearly(tmp_path: Path) -> None:
    """The earlier pair (pk_test_replace_me) is malformed: a copy made from an older .env.example fails with names."""
    from app.kernel.infrastructure.config import REPO_ROOT
    from app.settings import load_settings

    older = tmp_path / "env"
    older.write_text(
        "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_test_replace_me\nCLERK_SECRET_KEY=sk_test_replace_me\n"
        + "\n".join(
            line
            for line in (REPO_ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
            if not line.startswith(("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=", "CLERK_SECRET_KEY="))
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError) as excinfo:
        load_settings(_env_file=older)
    message = str(excinfo.value)
    assert "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY is still the .env.example placeholder" in message
    assert "CLERK_SECRET_KEY is malformed or still the .env.example placeholder" in message


def test_the_web_apps_verdict_on_the_clerk_keys_is_the_apis() -> None:
    """The same .env gets the same Clerk verdicts from both apps (env.server.test.ts has the mirror cases)."""
    for secret_key in ("sk_test_replace_me", "hello", "sk_test_", "sk_live_x y"):
        with pytest.raises(ConfigError) as excinfo:
            make_settings(**{**LOCAL_CLERK, "clerk_secret_key": secret_key})
        assert "CLERK_SECRET_KEY is malformed or still the .env.example placeholder" in str(excinfo.value)
        assert secret_key not in str(excinfo.value)
    for publishable in ("not-a-key", "pk_test_%%%"):
        with pytest.raises(ConfigError, match="NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY is malformed"):
            make_settings(**{**LOCAL_CLERK, "clerk_publishable_key": publishable})
    assert make_settings(**{**LOCAL_CLERK, "clerk_secret_key": "sk_test_abc123"}).clerk_secret_key is not None


def test_automated_tests_need_no_clerk_secret_key() -> None:
    assert make_settings(clerk_secret_key="hello").clerk_secret_key is not None


@pytest.mark.parametrize(
    "dsn", ["not-a-dsn", "https://o1.ingest.sentry.io/2", "ftp://key@host/1", "https://key@host/abc"]
)
def test_a_malformed_sentry_dsn_is_refused_by_name_at_startup(dsn: str) -> None:
    """Otherwise sentry_sdk.init raises BadDsn from bootstrap, naming no variable (ENV-03)."""
    for mode in ("disabled", "sandbox"):
        with pytest.raises(ConfigError) as excinfo:
            make_settings(error_reporting_mode=mode, sentry_dsn=dsn)
        message = str(excinfo.value)
        assert "SENTRY_DSN must be a Sentry DSN" in message
        assert dsn not in message
        assert "Unsupported scheme" not in message
        assert "Missing public key" not in message


@pytest.mark.parametrize("host", ["not a url", "eu.i.posthog.com", "ftp://eu.i.posthog.com", "https://"])
def test_a_malformed_posthog_host_is_refused_by_name(host: str) -> None:
    with pytest.raises(ConfigError) as excinfo:
        make_settings(posthog_host=host)
    assert "NEXT_PUBLIC_POSTHOG_HOST (or POSTHOG_HOST) must be an absolute http(s) URL" in str(excinfo.value)
    assert make_settings(posthog_host="https://eu.i.posthog.com").posthog_host == "https://eu.i.posthog.com"


@pytest.mark.parametrize("prefix", ["phx", "phs", "pha", "phr"])
@pytest.mark.parametrize("mode", ["disabled", "sandbox"])
def test_a_secret_posthog_credential_never_reaches_the_browser_bundle(prefix: str, mode: str) -> None:
    """NEXT_PUBLIC_POSTHOG_KEY is inlined into every browser bundle, whatever ANALYTICS_MODE says."""
    key = f"{prefix}_{SECRET_MARKER}"
    with pytest.raises(ConfigError) as excinfo:
        make_settings(analytics_mode=mode, posthog_project_api_key=key)
    message = str(excinfo.value)
    assert "holds a secret PostHog credential" in message
    assert key not in message
    assert SECRET_MARKER not in message


@pytest.mark.parametrize("key", ["pk_live_x", "sk_test_x", "phc", "phc_ x", "token"])
def test_a_posthog_key_must_be_a_project_token(key: str) -> None:
    with pytest.raises(ConfigError, match="must be a PostHog project token"):
        make_settings(posthog_project_api_key=key)
    assert make_settings(posthog_project_api_key="phc_x").posthog_project_api_key == "phc_x"


def test_live_clerk_keys_rejected_in_tests_too() -> None:
    """ENV-02/ENV-04: the test environment relaxes what is required, never key/environment consistency."""
    with pytest.raises(ConfigError, match="live key"):
        make_settings(clerk_secret_key="sk_" + "live_abcdefghijklmnop")


def test_clerk_jwt_key_must_be_an_rsa_public_key() -> None:
    with pytest.raises(ConfigError, match="CLERK_JWT_KEY must be a PEM-encoded RSA public key") as excinfo:
        make_settings(clerk_jwt_key="not-a-pem-" + SECRET_MARKER)
    assert SECRET_MARKER not in str(excinfo.value)


def test_clerk_jwt_key_accepts_escaped_newlines() -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    pem = (
        rsa.generate_private_key(public_exponent=65537, key_size=2048)
        .public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    for value in (pem, pem.strip().replace("\n", "\\n")):
        assert make_settings(clerk_jwt_key=value).clerk_jwt_key is not None
