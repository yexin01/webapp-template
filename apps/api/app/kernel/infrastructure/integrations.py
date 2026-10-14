"""Non-secret descriptions of the kernel's integrations for the diagnostics page.

Never include key values, DSNs, connection strings, or hostnames that identify
a hosted project.
"""

from __future__ import annotations

from urllib.parse import urlparse

from app.kernel.diagnostics import IntegrationInfo, IntegrationKey, IntegrationMode
from app.kernel.infrastructure.config import AppEnv, CoreSettings

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", ""})


def describe_database(s: CoreSettings) -> IntegrationInfo:
    db_url = s.database_url.get_secret_value() if s.database_url else ""
    local = (urlparse(db_url).hostname or "") in _LOOPBACK_HOSTS
    if local:
        mode = IntegrationMode.LOCAL
    else:
        mode = IntegrationMode.LIVE if s.app_env is AppEnv.PRODUCTION else IntegrationMode.SANDBOX
    return IntegrationInfo(
        IntegrationKey.DATABASE,
        "PostgreSQL",
        mode,
        (
            "Local server" if local else "Hosted server",
            f"Connection pools: up to {s.database_pool_max_size} request and "
            f"{s.database_worker_pool_max_size} worker connections",
            f"Statement timeout: {s.database_statement_timeout_ms} ms",
        ),
    )


def describe_analytics(s: CoreSettings) -> IntegrationInfo:
    return IntegrationInfo(
        IntegrationKey.ANALYTICS,
        "PostHog",
        IntegrationMode(s.analytics_mode.value),
        (f"Host: {s.posthog_host}", "Only allowlisted events and properties are sent"),
    )


def describe_error_reporting(s: CoreSettings) -> IntegrationInfo:
    return IntegrationInfo(
        IntegrationKey.ERROR_REPORTING,
        "Sentry",
        IntegrationMode(s.error_reporting_mode.value),
        (
            f"Environment: {s.app_env.value}",
            f"Trace sample rate: {s.sentry_traces_sample_rate}",
            "User data, headers, bodies, and query strings are not collected",
        ),
    )
