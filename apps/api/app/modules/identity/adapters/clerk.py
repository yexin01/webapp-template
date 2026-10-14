"""Clerk adapters: session-token verification, membership directory, webhooks.

Clerk authenticates users; authorization stays in this application (spec §7).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from typing import Any

import httpx
import jwt
from jwt import PyJWKClient
from svix.webhooks import Webhook, WebhookVerificationError

from app.kernel.errors import AuthenticationError, DependencyUnavailableError, WebhookSignatureError
from app.kernel.tenancy import VerifiedIdentity
from app.modules.identity.domain import ExternalMembership, role_from_external
from app.modules.identity.ports import IdentityWebhookEvent

logger = logging.getLogger(__name__)

_ALGORITHMS = ["RS256"]  # AUTH-02: pinned; "none" and HMAC algorithms are rejected.
_CLOCK_SKEW_SECONDS = 5
_MAX_TOKEN_LENGTH = 8_192
# How long a fetched key set is trusted. A key Clerk removes (rotation or
# revocation) stops verifying within this time (AUTH-04); verification also
# keeps working this long through a Clerk outage (§16.4).
_JWKS_LIFESPAN_SECONDS = 3_600


class ClerkTokenVerifier:
    """Verifies Clerk session tokens server-side (AUTH-01 to AUTH-04)."""

    def __init__(
        self,
        *,
        issuer: str,
        authorized_parties: Sequence[str],
        jwt_public_key: str | None = None,
        jwks_client: PyJWKClient | None = None,
    ) -> None:
        if not authorized_parties:
            raise ValueError("at least one authorized party is required")
        self._issuer = issuer
        self._authorized_parties = frozenset(p.rstrip("/") for p in authorized_parties)
        self._public_key = jwt_public_key
        # Only the key set is cached, with a lifespan. ``cache_keys`` would keep
        # every key ever seen for the life of the process, even after Clerk
        # removes it from the set.
        self._jwks = jwks_client or PyJWKClient(
            f"{issuer}/.well-known/jwks.json",
            cache_jwk_set=True,
            cache_keys=False,
            lifespan=_JWKS_LIFESPAN_SECONDS,
            timeout=5,
        )

    def verify(self, token: str) -> VerifiedIdentity:
        if not token or len(token) > _MAX_TOKEN_LENGTH:
            raise AuthenticationError()
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise AuthenticationError() from exc
        if header.get("alg") not in _ALGORITHMS:
            raise AuthenticationError()

        key = self._signing_key(token)
        try:
            claims = jwt.decode(
                token,
                key=key,
                algorithms=_ALGORITHMS,
                issuer=self._issuer,
                leeway=_CLOCK_SKEW_SECONDS,
                options={"require": ["exp", "iat", "sub", "iss"], "verify_aud": False},
            )
        except jwt.PyJWTError as exc:
            raise AuthenticationError() from exc

        azp = claims.get("azp")
        if not isinstance(azp, str) or azp.rstrip("/") not in self._authorized_parties:
            raise AuthenticationError()
        sub = claims.get("sub")
        if not isinstance(sub, str) or not sub:
            raise AuthenticationError()
        sid = claims.get("sid")
        return VerifiedIdentity(external_user_id=sub, session_id=sid if isinstance(sid, str) else None)

    def _signing_key(self, token: str) -> Any:
        if self._public_key:
            return self._public_key
        try:
            return self._jwks.get_signing_key_from_jwt(token).key
        except jwt.PyJWKClientConnectionError as exc:
            # AUTH-04: fail closed when keys cannot be fetched.
            raise DependencyUnavailableError("Authentication service is unavailable.") from exc
        except jwt.PyJWTError as exc:
            raise AuthenticationError() from exc


class ClerkMembershipDirectory:
    """Reads a user's organization memberships from the Clerk Backend API."""

    def __init__(self, *, secret_key: str, api_url: str, timeout_seconds: float = 10.0) -> None:
        self._client = httpx.Client(
            base_url=api_url.rstrip("/"),
            headers={"Authorization": f"Bearer {secret_key}"},
            timeout=timeout_seconds,
        )

    def list_user_memberships(self, external_user_id: str) -> list[ExternalMembership]:
        memberships: list[ExternalMembership] = []
        offset = 0
        limit = 100
        for _ in range(10):  # bounded: at most 1,000 memberships per user
            try:
                response = self._client.get(
                    f"/v1/users/{external_user_id}/organization_memberships",
                    params={"limit": limit, "offset": offset},
                )
            except httpx.HTTPError as exc:
                raise DependencyUnavailableError("Organization directory is unavailable.") from exc
            if response.status_code == 404:
                return []
            if response.status_code >= 400:
                logger.warning("clerk_directory_error", extra={"status_code": response.status_code})
                raise DependencyUnavailableError("Organization directory is unavailable.")
            body = response.json()
            items = body.get("data", []) if isinstance(body, dict) else body
            for item in items:
                org = item.get("organization") or {}
                org_id = org.get("id")
                if isinstance(org_id, str) and org_id:
                    memberships.append(ExternalMembership(org_id, role_from_external(str(item.get("role", "")))))
            if len(items) < limit:
                break
            offset += limit
        return memberships

    def close(self) -> None:
        self._client.close()


class ClerkWebhookVerifier:
    """Verifies Svix-signed Clerk webhooks on the raw body (SEC-13)."""

    def __init__(self, signing_secret: str) -> None:
        self._webhook = Webhook(signing_secret)

    def verify(self, payload: bytes, headers: Mapping[str, str]) -> IdentityWebhookEvent:
        lowered = {k.lower(): v for k, v in headers.items()}
        svix_headers: dict[str, str] = {
            k: lowered[k] for k in ("svix-id", "svix-timestamp", "svix-signature") if k in lowered
        }
        if len(svix_headers) != 3:
            raise WebhookSignatureError()
        try:
            self._webhook.verify(payload, svix_headers)
        except (WebhookVerificationError, ValueError) as exc:
            # Malformed signature headers and non-UTF-8 bodies raise ValueError
            # (UnicodeDecodeError is one); unauthenticated input is never a 500.
            raise WebhookSignatureError() from exc
        try:
            body = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise WebhookSignatureError() from exc
        data = body.get("data") if isinstance(body, dict) else None
        event_type = body.get("type") if isinstance(body, dict) else None
        if not isinstance(data, dict) or not isinstance(event_type, str):
            raise WebhookSignatureError()
        return IdentityWebhookEvent(event_id=svix_headers["svix-id"], event_type=event_type, data=data)
