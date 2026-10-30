"""Clerk configuration (DEC-009). Composed into ``app.settings.Settings``."""

from __future__ import annotations

import base64
import binascii
import re
from typing import Annotated

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import AliasChoices, BaseModel, Field, SecretStr, field_validator
from pydantic_settings import NoDecode

from app.kernel.infrastructure.config import (
    AppEnv,
    CoreSettings,
    VercelEnv,
    http_origin,
    is_bare_hostname,
    secret,
)

PLACEHOLDER = "replace_me"


class IdentitySettings(BaseModel):
    clerk_publishable_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("CLERK_PUBLISHABLE_KEY", "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY"),
    )
    clerk_secret_key: SecretStr | None = None
    clerk_jwt_key: SecretStr | None = None
    clerk_authorized_parties: Annotated[list[str], NoDecode] = Field(default_factory=list)
    clerk_webhook_signing_secret: SecretStr | None = None
    clerk_api_url: str = "https://api.clerk.com"
    # Sessions started by someone else (Clerk dashboard impersonation, actor tokens, agent tasks) carry
    # an ``act`` claim. They are refused unless this is on; then the actor is recorded in the audit log.
    clerk_allow_impersonation: bool = False
    # Each membership read holds a server thread (40 by default, shared by every synchronous route).
    # At least a read and the two requests that may always wait for it.
    clerk_max_concurrent_reads: int = Field(default=8, ge=3, le=16)
    # TEN-07: at most the owner-approved bound of 300 s (DEC-009, ADR-0002); lower is allowed.
    membership_max_staleness_seconds: int = Field(default=300, ge=0, le=300)
    membership_reconcile_batch_size: int = Field(default=50, ge=1, le=1_000)

    @field_validator("clerk_authorized_parties", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        # The form a browser sends as Origin, which is what the token's azp claim holds. An entry that
        # is no origin stays as written; identity_problems refuses it.
        if isinstance(value, str):
            return [http_origin(p.strip()) or p.strip() for p in value.split(",") if p.strip()]
        return value

    @property
    def clerk_issuer(self) -> str | None:
        if not self.clerk_publishable_key:
            return None
        return clerk_issuer_from_publishable_key(self.clerk_publishable_key)


def authorized_parties(s: IdentitySettings, core: CoreSettings) -> list[str]:
    """The origins whose sessions are accepted: CLERK_AUTHORIZED_PARTIES, or the origin of APP_BASE_URL when unset.

    On a Vercel preview deployment of a preview application, the deployment's own origin and its
    branch's are added to the list (never replacing it): their URLs differ per deployment and cannot
    be listed ahead of time. Only bare hostnames are taken from VERCEL_URL and VERCEL_BRANCH_URL.
    """
    parties = list(s.clerk_authorized_parties)
    # Automated tests (APP_ENV=test) may have no Clerk instance: without listed parties every token is refused.
    if not parties and core.app_env is not AppEnv.TEST and (own := http_origin(core.app_base_url)):
        parties.append(own)
    if core.app_env is AppEnv.PREVIEW and core.vercel_env is VercelEnv.PREVIEW:
        for host in (core.vercel_url, core.vercel_branch_url):
            if host and is_bare_hostname(host) and f"https://{host}" not in parties:
                parties.append(f"https://{host}")
    return parties


def identity_problems(s: IdentitySettings, core: CoreSettings) -> list[str]:
    production = core.app_env is AppEnv.PRODUCTION
    problems: list[str] = []
    # Key/environment consistency holds everywhere, tests included (ENV-02, ENV-04).
    live_prefix = ("pk_live_", "sk_live_")
    keys = {
        "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY": s.clerk_publishable_key or "",
        "CLERK_SECRET_KEY": secret(s.clerk_secret_key),
    }
    for name, value in keys.items():
        if value and value.startswith(live_prefix) and not production:
            problems.append(f"{name} is a live key; live keys are only allowed when APP_ENV=production")
        if value and production and not value.startswith(live_prefix):
            problems.append(f"{name} must be a live key when APP_ENV=production")
    if s.clerk_jwt_key is not None and not is_rsa_public_key_pem(normalize_pem(secret(s.clerk_jwt_key))):
        problems.append("CLERK_JWT_KEY must be a PEM-encoded RSA public key")
    if s.clerk_webhook_signing_secret is not None and not is_svix_signing_secret(
        secret(s.clerk_webhook_signing_secret).strip()
    ):
        problems.append("CLERK_WEBHOOK_SIGNING_SECRET must be the endpoint's signing secret: whsec_ followed by base64")
    if core.app_env is AppEnv.TEST:
        return problems
    publishable = s.clerk_publishable_key
    if not publishable:
        problems.append("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY is required")
    elif PLACEHOLDER in publishable.lower():
        problems.append(
            "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY is still the .env.example placeholder "
            "(copy it from Clerk Dashboard > API keys)"
        )
    elif clerk_issuer_from_publishable_key(publishable) is None:
        problems.append("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY is malformed")
    if s.clerk_secret_key is None:
        problems.append("CLERK_SECRET_KEY is required")
    else:
        value = secret(s.clerk_secret_key)
        if not re.fullmatch(r"sk_(test|live)_\S+", value) or PLACEHOLDER in value.lower():
            problems.append("CLERK_SECRET_KEY is malformed or still the .env.example placeholder")
    if any(http_origin(p) != p for p in s.clerk_authorized_parties):
        problems.append(
            "CLERK_AUTHORIZED_PARTIES must list http(s) origins (scheme, host, optional port; no path or credentials)"
        )
    elif not s.clerk_authorized_parties:
        problems.append("CLERK_AUTHORIZED_PARTIES is required (comma-separated frontend origins)")
    elif (own := http_origin(core.app_base_url)) and own not in s.clerk_authorized_parties:
        problems.append("CLERK_AUTHORIZED_PARTIES must list the origin of APP_BASE_URL")
    return problems


def normalize_pem(value: str) -> str:
    """A PEM pasted into a single-line variable often carries literal ``\\n`` escapes."""
    value = value.strip()
    return value.replace("\\n", "\n") if "\n" not in value else value


def is_svix_signing_secret(value: str) -> bool:
    """``whsec_`` followed by the base64-encoded key; callers trim surrounding whitespace first.

    Checked strictly: svix skips characters outside the base64 alphabet, so a
    stray character (an unquoted ``#comment``, say) would silently verify with
    a different key.
    """
    encoded = value.removeprefix("whsec_")
    if encoded == value:
        return False
    try:
        return bool(base64.b64decode(encoded + "=" * (-len(encoded) % 4), validate=True))
    except binascii.Error:
        return False


def is_rsa_public_key_pem(value: str) -> bool:
    try:
        key = serialization.load_pem_public_key(value.encode())
    except (ValueError, TypeError, UnsupportedAlgorithm):
        return False
    return isinstance(key, rsa.RSAPublicKey)


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
