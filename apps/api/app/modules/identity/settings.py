"""Clerk configuration (DEC-009). Composed into ``app.settings.Settings``."""

from __future__ import annotations

import base64
import binascii
from typing import Annotated

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import AliasChoices, BaseModel, Field, SecretStr, field_validator
from pydantic_settings import NoDecode

from app.kernel.infrastructure.config import AppEnv, CoreSettings, secret


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
    # TEN-07: at most the owner-approved bound of 300 s (DEC-009, ADR-0002); lower is allowed.
    membership_max_staleness_seconds: int = Field(default=300, ge=0, le=300)
    membership_reconcile_batch_size: int = Field(default=50, ge=1, le=1_000)

    @field_validator("clerk_authorized_parties", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [p.strip().rstrip("/") for p in value.split(",") if p.strip()]
        return value

    @property
    def clerk_issuer(self) -> str | None:
        if not self.clerk_publishable_key:
            return None
        return clerk_issuer_from_publishable_key(self.clerk_publishable_key)


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
    if core.app_env is AppEnv.TEST:
        return problems
    if not s.clerk_publishable_key:
        problems.append("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY is required")
    elif clerk_issuer_from_publishable_key(s.clerk_publishable_key) is None:
        problems.append("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY is malformed")
    if s.clerk_secret_key is None:
        problems.append("CLERK_SECRET_KEY is required")
    if not s.clerk_authorized_parties:
        problems.append("CLERK_AUTHORIZED_PARTIES is required (comma-separated frontend origins)")
    return problems


def normalize_pem(value: str) -> str:
    """A PEM pasted into a single-line variable often carries literal ``\\n`` escapes."""
    value = value.strip()
    return value.replace("\\n", "\n") if "\n" not in value else value


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
