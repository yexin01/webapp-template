"""Composition root: wires adapters into services by environment and mode.

This is the only module that knows which concrete adapter backs each port.
Tests build a ``Container`` with ``build_container(settings, overrides=...)``
instead of activating any test-only path in application code (ENV-08).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from urllib.parse import urlparse

from app.application.billing import BillingService, BillingUrls
from app.application.context import ContextResolver
from app.application.diagnostics import DiagnosticsService, IntegrationInfo, IntegrationKey, IntegrationMode
from app.application.identity_sync import IdentitySyncService
from app.application.llm import LLMService
from app.application.projects import ProjectService
from app.application.rate_limit import RateLimiter
from app.domain.catalog import PriceCatalog
from app.domain.entitlements import PlanKey
from app.infrastructure.clerk import ClerkMembershipDirectory, ClerkTokenVerifier, ClerkWebhookVerifier
from app.infrastructure.config import AppEnv, LLMProviderName, Mode, Settings, load_settings
from app.infrastructure.db import Database, PgUnitOfWork
from app.infrastructure.fakes import InMemoryBillingProvider, RejectingVerifier, StaticMembershipDirectory, SystemClock
from app.infrastructure.llm_providers import AnthropicProvider, MockLLMProvider, OpenAIProvider
from app.infrastructure.observability import (
    NoopAnalytics,
    NoopErrorReporter,
    PostHogAnalytics,
    SentryErrorReporter,
    configure_logging,
    init_error_reporting,
)
from app.infrastructure.stripe_billing import StripeBillingProvider, StripeWebhookVerifier
from app.ports import (
    Analytics,
    BillingProvider,
    BillingWebhookVerifier,
    Clock,
    ErrorReporter,
    IdentityVerifier,
    IdentityWebhookVerifier,
    LLMProvider,
    MembershipDirectory,
    UnitOfWork,
)


@dataclass
class Container:
    settings: Settings
    clock: Clock
    uow: UnitOfWork
    verifier: IdentityVerifier
    resolver: ContextResolver
    projects: ProjectService
    billing: BillingService
    identity_sync: IdentitySyncService
    llm: LLMService
    diagnostics: DiagnosticsService
    analytics: Analytics
    closers: list[Any] = field(default_factory=list)

    def close(self) -> None:
        self.analytics.shutdown()
        for closer in self.closers:
            closer()


def build_container(settings: Settings, **overrides: Any) -> Container:
    """Build all services. Keyword overrides replace individual adapters."""
    closers: list[Any] = []

    def pick(key: str, factory: Callable[[], Any]) -> Any:
        # An explicit override wins, including an explicit ``None``.
        return overrides[key] if key in overrides else factory()

    clock: Clock = pick("clock", SystemClock)
    uow: UnitOfWork = pick("uow", lambda: _unit_of_work(settings, closers))
    verifier: IdentityVerifier = pick("verifier", lambda: _identity_verifier(settings))
    directory: MembershipDirectory = pick("directory", lambda: _directory(settings, closers))
    identity_webhooks: IdentityWebhookVerifier | None = pick(
        "identity_webhook_verifier",
        lambda: (
            ClerkWebhookVerifier(settings.clerk_webhook_signing_secret.get_secret_value())
            if settings.clerk_webhook_signing_secret
            else None
        ),
    )

    catalog = PriceCatalog(
        {
            key: price
            for key, price in (
                (PlanKey.PRO_MONTHLY, settings.stripe_price_pro_monthly),
                (PlanKey.PRO_YEARLY, settings.stripe_price_pro_yearly),
            )
            if price
        }
    )
    billing_provider: BillingProvider | None = pick("billing_provider", lambda: _billing_provider(settings, catalog))
    billing_webhooks: BillingWebhookVerifier | None = pick(
        "billing_webhook_verifier",
        lambda: (
            StripeWebhookVerifier(settings.stripe_webhook_secret.get_secret_value())
            if settings.stripe_webhook_secret
            else None
        ),
    )
    analytics: Analytics = pick("analytics", lambda: _analytics(settings))
    llm_provider: LLMProvider = pick("llm_provider", lambda: _llm_provider(settings))
    error_reporter: ErrorReporter = pick("error_reporter", lambda: _error_reporter(settings))

    rate_limiter = RateLimiter(uow, clock)
    resolver = ContextResolver(uow, directory, clock, timedelta(seconds=settings.membership_max_staleness_seconds))
    llm = LLMService(
        llm_provider,
        rate_limiter,
        analytics,
        checks_per_user_per_minute=settings.llm_checks_per_user_per_minute,
        checks_global_per_day=settings.llm_checks_global_per_day,
    )
    return Container(
        settings=settings,
        clock=clock,
        uow=uow,
        verifier=verifier,
        resolver=resolver,
        projects=ProjectService(uow, analytics),
        billing=BillingService(
            uow=uow,
            provider=billing_provider,
            webhook_verifier=billing_webhooks,
            catalog=catalog,
            urls=BillingUrls(settings.app_base_url.rstrip("/")),
            clock=clock,
            analytics=analytics,
            rate_limiter=rate_limiter,
            checkouts_per_hour=settings.checkouts_per_user_per_hour,
        ),
        identity_sync=IdentitySyncService(uow, identity_webhooks, resolver),
        llm=llm,
        diagnostics=DiagnosticsService(
            enabled=settings.diagnostics_active,
            integrations=pick("integrations", lambda: describe_integrations(settings)),
            uow=uow,
            directory=directory,
            billing_provider=billing_provider,
            catalog=catalog,
            analytics=analytics,
            error_reporter=error_reporter,
            llm=llm,
            rate_limiter=rate_limiter,
            runs_per_user_per_minute=settings.diagnostics_runs_per_user_per_minute,
            runs_global_per_day=settings.diagnostics_runs_global_per_day,
        ),
        analytics=analytics,
        closers=closers,
    )


def _unit_of_work(settings: Settings, closers: list[Any]) -> UnitOfWork:
    if settings.database_url is None:  # pragma: no cover - validated in Settings
        raise RuntimeError("DATABASE_URL is required")
    db = Database(
        settings.database_url.get_secret_value(),
        max_size=settings.database_pool_max_size,
        connect_timeout=settings.database_connect_timeout_seconds,
        statement_timeout_ms=settings.database_statement_timeout_ms,
    )
    closers.append(db.close)
    return PgUnitOfWork(db)


def _identity_verifier(settings: Settings) -> IdentityVerifier:
    issuer = settings.clerk_issuer
    if issuer is None or not settings.clerk_authorized_parties:
        # Only reachable with APP_ENV=test, where Settings allows missing Clerk config.
        return RejectingVerifier()
    return ClerkTokenVerifier(
        issuer=issuer,
        authorized_parties=settings.clerk_authorized_parties,
        jwt_public_key=settings.clerk_jwt_key.get_secret_value() if settings.clerk_jwt_key else None,
    )


def _directory(settings: Settings, closers: list[Any]) -> MembershipDirectory:
    if settings.clerk_secret_key is None:
        return StaticMembershipDirectory()
    directory = ClerkMembershipDirectory(
        secret_key=settings.clerk_secret_key.get_secret_value(), api_url=settings.clerk_api_url
    )
    closers.append(directory.close)
    return directory


def _billing_provider(settings: Settings, catalog: PriceCatalog) -> BillingProvider | None:
    if settings.billing_mode is Mode.MOCK:
        if settings.app_env is AppEnv.PRODUCTION:  # pragma: no cover - rejected by Settings
            return None
        return InMemoryBillingProvider(settings.app_base_url)
    if settings.stripe_secret_key is None:  # pragma: no cover - validated in Settings
        return None
    return StripeBillingProvider(secret_key=settings.stripe_secret_key.get_secret_value(), catalog=catalog)


def _analytics(settings: Settings) -> Analytics:
    if settings.analytics_mode in (Mode.SANDBOX, Mode.LIVE) and settings.posthog_project_api_key:
        return PostHogAnalytics(api_key=settings.posthog_project_api_key, host=settings.posthog_host)
    return NoopAnalytics()


def _llm_provider(settings: Settings) -> LLMProvider:
    if settings.llm_mode is Mode.MOCK or settings.llm_api_key is None or not settings.llm_model:
        return MockLLMProvider()
    cls = AnthropicProvider if settings.llm_provider is LLMProviderName.ANTHROPIC else OpenAIProvider
    return cls(
        api_key=settings.llm_api_key.get_secret_value(),
        model=settings.llm_model,
        timeout_seconds=settings.llm_timeout_seconds,
    )


def _error_reporter(settings: Settings) -> ErrorReporter:
    if settings.error_reporting_mode in (Mode.SANDBOX, Mode.LIVE):
        return SentryErrorReporter()
    return NoopErrorReporter()


_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", ""})


def _mode(mode: Mode) -> IntegrationMode:
    return IntegrationMode(mode.value)


def describe_integrations(settings: Settings) -> list[IntegrationInfo]:
    """Non-secret configuration facts for the diagnostics page.

    Never include key values, DSNs, connection strings, or hostnames that
    identify a hosted project.
    """
    production = settings.app_env is AppEnv.PRODUCTION
    db_url = settings.database_url.get_secret_value() if settings.database_url else ""
    db_local = (urlparse(db_url).hostname or "") in _LOOPBACK_HOSTS
    db_mode = IntegrationMode.LIVE if production else IntegrationMode.SANDBOX
    if db_local:
        db_mode = IntegrationMode.LOCAL
    database = IntegrationInfo(
        IntegrationKey.DATABASE,
        "PostgreSQL",
        db_mode,
        (
            "Local server" if db_local else "Hosted server",
            f"Connection pool: up to {settings.database_pool_max_size} connections",
            f"Statement timeout: {settings.database_statement_timeout_ms} ms",
        ),
    )

    publishable = settings.clerk_publishable_key or ""
    if publishable.startswith("pk_live_"):
        identity_mode, instance = IntegrationMode.LIVE, "Production instance"
    elif publishable.startswith("pk_test_"):
        identity_mode, instance = IntegrationMode.SANDBOX, "Development instance"
    else:
        identity_mode, instance = IntegrationMode.MOCK, "Not configured"
    identity = IntegrationInfo(
        IntegrationKey.IDENTITY,
        "Clerk",
        identity_mode,
        (
            instance,
            "Authorized origins: " + (", ".join(settings.clerk_authorized_parties) or "none"),
            "Token keys: " + ("PEM public key (no network)" if settings.clerk_jwt_key else "instance JWKS"),
            (
                "Membership webhooks: configured"
                if settings.clerk_webhook_signing_secret
                else "Membership webhooks: not configured"
            ),
            f"Membership re-verification after {settings.membership_max_staleness_seconds} s",
        ),
    )

    configured_prices = (("monthly", settings.stripe_price_pro_monthly), ("yearly", settings.stripe_price_pro_yearly))
    prices = [label for label, value in configured_prices if value]
    billing = IntegrationInfo(
        IntegrationKey.BILLING,
        "Stripe",
        _mode(settings.billing_mode),
        (
            "Pro prices configured: " + (", ".join(prices) or "none"),
            "Webhook signing secret: " + ("configured" if settings.stripe_webhook_secret else "missing"),
        ),
    )

    llm_mode = _mode(settings.llm_mode)
    llm_target = (
        ("Mock provider",)
        if llm_mode is IntegrationMode.MOCK
        else (f"Provider: {settings.llm_provider.value}", f"Model: {settings.llm_model}")
    )
    llm_limits = (
        f"Limits: {settings.llm_checks_per_user_per_minute}/min per user, "
        f"{settings.llm_checks_global_per_day}/day overall"
    )
    llm = IntegrationInfo(IntegrationKey.LLM, "LLM provider", llm_mode, (*llm_target, llm_limits))

    analytics = IntegrationInfo(
        IntegrationKey.ANALYTICS,
        "PostHog",
        _mode(settings.analytics_mode),
        (f"Host: {settings.posthog_host}", "Only allowlisted events and properties are sent"),
    )
    error_reporting = IntegrationInfo(
        IntegrationKey.ERROR_REPORTING,
        "Sentry",
        _mode(settings.error_reporting_mode),
        (
            f"Environment: {settings.app_env.value}",
            f"Trace sample rate: {settings.sentry_traces_sample_rate}",
            "User data, headers, bodies, and query strings are not collected",
        ),
    )
    return [database, identity, billing, llm, analytics, error_reporting]


def bootstrap() -> Container:
    """Load configuration, configure telemetry, and build the production container."""
    settings = load_settings()
    configure_logging(settings.log_level)
    init_error_reporting(settings)
    return build_container(settings)
