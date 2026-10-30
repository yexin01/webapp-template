"""Wiring for the identity module."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from app.kernel.diagnostics import IntegrationInfo, IntegrationKey, IntegrationMode
from app.kernel.http.auth import AuthDeps
from app.kernel.infrastructure.config import CoreSettings
from app.kernel.jobs import Job
from app.kernel.wiring import Contribution, Kernel, pick
from app.modules.identity.adapters.clerk import ClerkMembershipDirectory, ClerkTokenVerifier, ClerkWebhookVerifier
from app.modules.identity.adapters.fakes import RejectingVerifier, StaticMembershipDirectory
from app.modules.identity.adapters.repository import PgIdentityRepository
from app.modules.identity.ports import IdentityVerifier, IdentityWebhookVerifier, MembershipDirectory
from app.modules.identity.probe import IdentityProbe
from app.modules.identity.routes import build_account_router, build_auth, build_webhook_router
from app.modules.identity.service import ContextResolver, IdentitySyncService, MembershipReconciler
from app.modules.identity.settings import IdentitySettings, normalize_pem


@dataclass
class IdentityModule:
    auth: AuthDeps
    resolver: ContextResolver
    sync: IdentitySyncService
    reconciler: MembershipReconciler
    contribution: Contribution


def build_identity(
    core: CoreSettings, settings: IdentitySettings, kernel: Kernel, overrides: Mapping[str, Any]
) -> IdentityModule:
    contribution = Contribution()
    tx = kernel.transactions(PgIdentityRepository)
    staleness = timedelta(seconds=settings.membership_max_staleness_seconds)

    verifier: IdentityVerifier = pick(overrides, "verifier", lambda: _verifier(settings))
    directory: MembershipDirectory = pick(overrides, "directory", lambda: _directory(settings, contribution))
    webhooks: IdentityWebhookVerifier | None = pick(
        overrides,
        "identity_webhook_verifier",
        lambda: (
            # Trimmed as validated: svix would read a leading space into the key.
            ClerkWebhookVerifier(settings.clerk_webhook_signing_secret.get_secret_value().strip())
            if settings.clerk_webhook_signing_secret
            else None
        ),
    )

    resolver = ContextResolver(
        tx, directory, kernel.clock, staleness, kernel.rate_limiter, settings.clerk_max_concurrent_reads
    )
    sync = IdentitySyncService(tx, webhooks, resolver, kernel.clock)
    reconciler = MembershipReconciler(tx, resolver, kernel.clock, staleness, settings.membership_reconcile_batch_size)
    auth = build_auth(verifier, resolver, allow_actor_sessions=settings.clerk_allow_impersonation)

    kernel.jobs.register(
        Job(
            "reconcile-memberships",
            "Re-verify stale organization-membership mirrors against Clerk, least recently tried or read first.",
            "30 3 * * *",
            reconciler.run,
        )
    )
    contribution.routers += [build_account_router(auth, kernel.request_transaction), build_webhook_router(sync)]
    contribution.probes.append(IdentityProbe(describe(settings), directory))
    return IdentityModule(auth, resolver, sync, reconciler, contribution)


def _verifier(settings: IdentitySettings) -> IdentityVerifier:
    issuer = settings.clerk_issuer
    if issuer is None or not settings.clerk_authorized_parties:
        # Only reachable with APP_ENV=test, where Settings allows missing Clerk config.
        return RejectingVerifier()
    return ClerkTokenVerifier(
        issuer=issuer,
        authorized_parties=settings.clerk_authorized_parties,
        jwt_public_key=normalize_pem(settings.clerk_jwt_key.get_secret_value()) if settings.clerk_jwt_key else None,
    )


def _directory(settings: IdentitySettings, contribution: Contribution) -> MembershipDirectory:
    if settings.clerk_secret_key is None:
        return StaticMembershipDirectory()
    directory = ClerkMembershipDirectory(
        secret_key=settings.clerk_secret_key.get_secret_value(), api_url=settings.clerk_api_url
    )
    contribution.closers.append(directory.close)
    return directory


def describe(settings: IdentitySettings) -> IntegrationInfo:
    """Non-secret configuration facts for the diagnostics page."""
    publishable = settings.clerk_publishable_key or ""
    if publishable.startswith("pk_live_"):
        mode, instance = IntegrationMode.LIVE, "Production instance"
    elif publishable.startswith("pk_test_"):
        mode, instance = IntegrationMode.SANDBOX, "Development instance"
    else:
        mode, instance = IntegrationMode.MOCK, "Not configured"
    return IntegrationInfo(
        IntegrationKey.IDENTITY,
        "Clerk",
        mode,
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
