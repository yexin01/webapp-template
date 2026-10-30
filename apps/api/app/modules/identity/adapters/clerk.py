"""Clerk adapters: session-token verification, membership directory, webhooks.

Clerk authenticates users; authorization stays in this application (spec §7).
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import httpx
import jwt
from jwt import PyJWKSet
from svix.webhooks import Webhook, WebhookVerificationError

from app.kernel.errors import AuthenticationError, DependencyUnavailableError, WebhookSignatureError
from app.kernel.tenancy import VerifiedIdentity
from app.modules.identity.domain import ExternalMembership, role_from_external
from app.modules.identity.ports import IdentityWebhookEvent, MembershipListTooLargeError

logger = logging.getLogger(__name__)

_ALGORITHMS = ["RS256"]  # AUTH-02: pinned; "none" and HMAC algorithms are rejected.
_CLOCK_SKEW_SECONDS = 5
_MAX_TOKEN_LENGTH = 8_192
_MAX_ACTOR_ID_LENGTH = 255
# A refused origin is logged at most this often per process, so a flood of tokens cannot flood the log.
_AZP_LOG_INTERVAL_SECONDS = 60.0
_ORIGIN_LIKE = re.compile(r"https?://[A-Za-z0-9.-]{1,253}(:[0-9]{1,5})?")
# How long a fetched key set is trusted. A key Clerk removes (rotation or
# revocation) stops verifying within this time (AUTH-04); verification also
# keeps working this long through a Clerk outage (§16.4).
_JWKS_LIFESPAN_SECONDS = 3_600.0
# Past this age, one request refreshes the key set while every request keeps
# using it, so a healthy instance never reaches the hard expiry.
_JWKS_REFRESH_AHEAD_SECONDS = _JWKS_LIFESPAN_SECONDS * 3 / 4
# At most one fetch per interval, successful or not: neither a flood of tokens
# with unknown key IDs nor a Clerk outage turns into one fetch per request.
_JWKS_MIN_REFRESH_INTERVAL_SECONDS = 5.0
_JWKS_TIMEOUT_SECONDS = 3.0
# While a usable key set is cached, the requests with an unknown key ID that may
# wait for a fetch in flight. The rest get 503 at once, so made-up key IDs cannot
# park request threads (§16.4).
_JWKS_MAX_WAITERS = 4
# With no usable key set, every request needs the fetch in flight, so more may
# wait for it, still well under the 40 request threads (db.REQUEST_THREADS). After
# a recent failed attempt (an outage), none wait.
_JWKS_MAX_COLD_WAITERS = 16
# A failed attempt this recent means an outage is ongoing: while requests keep
# coming, attempts are _JWKS_MIN_REFRESH_INTERVAL_SECONDS apart. An older one,
# followed by a quiet spell, is not taken for an outage.
_JWKS_RECENT_FAILURE_SECONDS = 30.0
_AUTH_UNAVAILABLE = "Authentication service is unavailable."

_DIRECTORY_UNAVAILABLE = "Organization directory is unavailable."
# After a failed directory read, reads fail fast for this long instead of each
# one waiting out the timeout during an outage (§16.4).
_DIRECTORY_COOLDOWN_SECONDS = 5.0
# Clerk's largest page for this endpoint. The page bound keeps one read bounded,
# and a read whose every page is full may go on, so it is refused: a user's
# memberships are read only if there are at most 4,999.
_MEMBERSHIP_PAGE_SIZE = 500
_MEMBERSHIP_MAX_PAGES = 10


def fetch_jwks(url: str, *, transport: httpx.BaseTransport | None = None) -> object:
    """GET the key set document: short timeout, no redirects."""
    with httpx.Client(timeout=_JWKS_TIMEOUT_SECONDS, follow_redirects=False, transport=transport) as client:
        response = client.get(url)
        response.raise_for_status()
        return response.json()


@dataclass(frozen=True, slots=True)
class _KeySnapshot:
    keys: Mapping[str, Any]  # key ID -> public key
    fetched_at: float  # monotonic; taken before the fetch, so a slow fetch never looks fresher


class JwksKeySet:
    """The issuer's signing keys, cached with a bounded lifetime (AUTH-04).

    The cache is one immutable snapshot, read without a lock: a request whose
    key is cached never waits on Clerk (§16.4). Fetches are single-flight and
    throttled, and a failed or unusable fetch never replaces a good snapshot.
    Waiting for a fetch in flight is bounded, so no flood of tokens can park
    every request thread: while a usable snapshot exists, a few requests wait;
    without one, more may wait, since every request needs the fetch, unless
    an attempt failed in the last 30 s (an outage), when none wait. Past the
    bound, a request gets 503 at once, and the refusal is logged. The refresh
    ahead of expiry runs on its own thread. Once the snapshot expires without
    a successful refresh, verification fails closed.
    """

    def __init__(
        self,
        url: str,
        *,
        fetch: Callable[[], object] | None = None,
        clock: Callable[[], float] = time.monotonic,
        in_background: Callable[[Callable[[], None]], None] | None = None,
    ) -> None:
        self._fetch = fetch or (lambda: fetch_jwks(url))
        self._clock = clock
        self._in_background = in_background or _start_daemon_thread
        self._lock = threading.Lock()
        self._snapshot: _KeySnapshot | None = None
        self._last_attempt: float | None = None  # guarded by _lock
        self._last_attempt_failed = False  # written under _lock
        self._last_failure = float("-inf")  # monotonic, when a failed attempt last ended; written under _lock
        self._waiting = 0  # guarded by _waiting_lock
        self._waiting_lock = threading.Lock()

    def signing_key(self, kid: str) -> Any:
        snapshot = self._snapshot
        if snapshot is not None and self._usable(snapshot):
            key = snapshot.keys.get(kid)
            if key is not None:
                if self._age(snapshot) >= _JWKS_REFRESH_AHEAD_SECONDS:
                    self._refresh_in_background()
                return key
        # An unknown key ID (Clerk may have rotated its keys), or no valid set.
        if self._acquire_for_refresh():
            self._refresh_holding_lock()
        snapshot = self._snapshot
        if snapshot is None or not self._usable(snapshot):
            raise DependencyUnavailableError(_AUTH_UNAVAILABLE)
        key = snapshot.keys.get(kid)
        if key is None:
            if self._last_attempt_failed:
                # The key may be one Clerk rotated in while its key set cannot
                # be read: an outage, not proof that the token is bad.
                raise DependencyUnavailableError(_AUTH_UNAVAILABLE)
            raise AuthenticationError()
        return key

    def _acquire_for_refresh(self) -> bool:
        """Take the fetch lock, or wait for the fetch holding it if fewer than the bound already do."""
        if self._lock.acquire(blocking=False):
            return True
        bound = self._waiter_bound()
        with self._waiting_lock:
            admitted = self._waiting < bound
            if admitted:
                self._waiting += 1
        if not admitted:  # logged outside the lock, so a slow log write never holds up the waiters
            logger.warning("clerk_jwks_waiters_exhausted", extra={"limit": bound})
            raise DependencyUnavailableError(_AUTH_UNAVAILABLE)
        try:
            return self._lock.acquire(timeout=_JWKS_TIMEOUT_SECONDS)
        finally:
            with self._waiting_lock:
                self._waiting -= 1

    def _waiter_bound(self) -> int:
        """How many requests may wait for the fetch in flight."""
        if self._usable(self._snapshot):
            return _JWKS_MAX_WAITERS
        if self._clock() - self._last_failure < _JWKS_RECENT_FAILURE_SECONDS:
            return 0  # an outage: the attempt in flight most likely fails too
        return _JWKS_MAX_COLD_WAITERS

    def _usable(self, snapshot: _KeySnapshot | None) -> bool:
        return snapshot is not None and self._age(snapshot) < _JWKS_LIFESPAN_SECONDS

    def _age(self, snapshot: _KeySnapshot) -> float:
        return self._clock() - snapshot.fetched_at

    def _throttled(self, now: float) -> bool:
        return self._last_attempt is not None and now - self._last_attempt < _JWKS_MIN_REFRESH_INTERVAL_SECONDS

    def _refresh_in_background(self) -> None:
        """Start a refresh on its own thread, unless one is running or was attempted just now."""
        if not self._lock.acquire(blocking=False):
            return
        if self._throttled(self._clock()):
            self._lock.release()
            return
        handed_off = False

        def refresh() -> None:
            nonlocal handed_off
            handed_off = True  # from here on, the refresh releases the lock
            self._refresh_holding_lock()

        try:
            self._in_background(refresh)
        except BaseException:
            if not handed_off:  # the thread never started
                self._lock.release()
            raise

    def _refresh_holding_lock(self) -> None:
        """Fetch the key set unless a fetch was attempted just now, then release the lock.

        A thread that waited for the lock while another fetched finds that
        attempt within the interval, and uses its result instead of fetching.
        """
        try:
            started = self._clock()
            if self._throttled(started):
                return
            self._last_attempt = started
            keys = self._load()
            self._last_attempt_failed = not keys
            if keys:
                self._snapshot = _KeySnapshot(keys, started)
            else:
                self._last_failure = self._clock()
        finally:
            self._lock.release()

    def _load(self) -> dict[str, Any]:
        try:
            document = self._fetch()
            if not isinstance(document, dict):
                raise TypeError("the key set is not a JSON object")
            jwk_set = PyJWKSet.from_dict(document)
        except (httpx.HTTPError, ValueError, TypeError, jwt.PyJWTError) as exc:
            logger.warning("clerk_jwks_refresh_failed", extra={"error": type(exc).__name__})
            return {}
        keys = {
            jwk.key_id: jwk.key
            for jwk in jwk_set.keys
            # RS256 is the only accepted algorithm (AUTH-02), so only RSA keys can verify.
            if isinstance(jwk.key_id, str)
            and jwk.key_id
            and jwk.key_type == "RSA"
            and jwk.public_key_use in ("sig", None)
        }
        if not keys:
            logger.warning("clerk_jwks_refresh_failed", extra={"error": "no signing keys"})
        return keys


def _start_daemon_thread(work: Callable[[], None]) -> None:
    threading.Thread(target=work, name="jwks-refresh", daemon=True).start()


class ClerkTokenVerifier:
    """Verifies Clerk session tokens server-side (AUTH-01 to AUTH-04)."""

    def __init__(
        self,
        *,
        issuer: str,
        authorized_parties: Sequence[str],
        jwt_public_key: str | None = None,
        key_set: JwksKeySet | None = None,
    ) -> None:
        if not authorized_parties:
            raise ValueError("at least one authorized party is required")
        self._issuer = issuer
        self._authorized_parties = frozenset(p.rstrip("/") for p in authorized_parties)
        self._public_key = jwt_public_key
        self._key_set = key_set or JwksKeySet(f"{issuer}/.well-known/jwks.json")
        self._azp_logged_at = -_AZP_LOG_INTERVAL_SECONDS

    def verify(self, token: str) -> VerifiedIdentity:
        if not token or len(token) > _MAX_TOKEN_LENGTH:
            raise AuthenticationError()
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise AuthenticationError() from exc
        if header.get("alg") not in _ALGORITHMS:
            raise AuthenticationError()

        key = self._signing_key(header)
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
            self._log_azp_refused(azp)
            raise AuthenticationError()
        sub = claims.get("sub")
        if not isinstance(sub, str) or not sub:
            raise AuthenticationError()
        if claims.get("sts") == "pending":
            # A session with an unfinished required step (for example, choosing
            # an organization). Clerk's SDKs treat it as signed out by default.
            raise AuthenticationError()
        sid = claims.get("sid")
        return VerifiedIdentity(
            external_user_id=sub,
            session_id=sid if isinstance(sid, str) else None,
            active_org_id=_active_org_id(claims),
            actor_external_id=_actor_id(claims),
        )

    def _log_azp_refused(self, azp: object) -> None:
        """Say why a validly signed token was refused, which is otherwise an anonymous 401: for example
        the origin of a preview deployment that CLERK_AUTHORIZED_PARTIES does not list. The cause class
        and, if it looks like an origin, the claim's origin; never the token."""
        now = time.monotonic()
        if now - self._azp_logged_at < _AZP_LOG_INTERVAL_SECONDS:
            return
        self._azp_logged_at = now
        logger.warning(
            "clerk_azp_refused",
            extra={
                "cause": "azp_not_authorized" if isinstance(azp, str) else "azp_missing",
                "azp": azp if isinstance(azp, str) and _ORIGIN_LIKE.fullmatch(azp) else None,
            },
        )

    def _signing_key(self, header: Mapping[str, Any]) -> Any:
        if self._public_key:
            return self._public_key
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            raise AuthenticationError()
        return self._key_set.signing_key(kid)


def _actor_id(claims: Mapping[str, Any]) -> str | None:
    """Who acts as the user, from the ``act`` claim of an impersonation or an actor token.

    Clerk adds it when the session was started by someone else: a dashboard
    impersonation, a Backend API actor token, or an agent task. Its ``sub`` is
    the actor. A claim in any other shape is a token this verifier does not
    understand, so it is refused as an invalid token.
    """
    act = claims.get("act")
    if act is None:
        return None
    actor = act.get("sub") if isinstance(act, dict) else None
    if not isinstance(actor, str) or not 0 < len(actor) <= _MAX_ACTOR_ID_LENGTH:
        raise AuthenticationError()
    return actor


def _active_org_id(claims: Mapping[str, Any]) -> str | None:
    """The session's active organization: ``o.id`` in Clerk's v2 session token, ``org_id`` in v1."""
    org = claims.get("o")
    value = org.get("id") if isinstance(org, dict) else claims.get("org_id")
    return value if isinstance(value, str) and value else None


class ClerkMembershipDirectory:
    """Reads a user's organization memberships from the Clerk Backend API."""

    def __init__(
        self,
        *,
        secret_key: str,
        api_url: str,
        timeout_seconds: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = httpx.Client(
            base_url=api_url.rstrip("/"),
            headers={"Authorization": f"Bearer {secret_key}"},
            timeout=timeout_seconds,
            transport=transport,
        )
        self._clock = clock
        self._unavailable_until = float("-inf")

    def list_user_memberships(self, external_user_id: str) -> list[ExternalMembership]:
        if self._clock() < self._unavailable_until:
            raise DependencyUnavailableError(_DIRECTORY_UNAVAILABLE)
        try:
            return self._read(external_user_id)
        except MembershipListTooLargeError:
            raise  # one user's list, not an outage: every other read goes on
        except DependencyUnavailableError:
            self._unavailable_until = self._clock() + _DIRECTORY_COOLDOWN_SECONDS
            raise

    def _read(self, external_user_id: str) -> list[ExternalMembership]:
        memberships: list[ExternalMembership] = []
        offset = 0
        limit = _MEMBERSHIP_PAGE_SIZE
        for _ in range(_MEMBERSHIP_MAX_PAGES):
            try:
                response = self._client.get(
                    f"/v1/users/{external_user_id}/organization_memberships",
                    params={"limit": limit, "offset": offset},
                )
            except httpx.HTTPError as exc:
                raise DependencyUnavailableError(_DIRECTORY_UNAVAILABLE) from exc
            if response.status_code == 404:
                return []
            if response.status_code >= 400:
                logger.warning("clerk_directory_error", extra={"status_code": response.status_code})
                raise DependencyUnavailableError(_DIRECTORY_UNAVAILABLE)
            try:
                body = response.json()
                items: Any = body.get("data") if isinstance(body, dict) else body
                page = [_membership(item) for item in items]
            except (ValueError, TypeError, AttributeError) as exc:
                # A malformed answer is an outage: never a 500, and never a
                # partial read that would remove the memberships it lacks.
                logger.warning("clerk_directory_malformed", extra={"status_code": response.status_code})
                raise DependencyUnavailableError(_DIRECTORY_UNAVAILABLE) from exc
            memberships += page
            if len(page) < limit:
                return memberships
            offset += limit
        # Every page was full, so the list may go on. Applying it would remove the memberships it lacks.
        logger.warning("clerk_directory_truncated", extra={"pages": _MEMBERSHIP_MAX_PAGES})
        raise MembershipListTooLargeError()

    def close(self) -> None:
        self._client.close()


def _membership(item: Any) -> ExternalMembership:
    org_id = (
        item["organization"]["id"] if isinstance(item, dict) and isinstance(item.get("organization"), dict) else None
    )
    if not isinstance(org_id, str) or not org_id:
        raise ValueError("a membership without an organization ID")
    return ExternalMembership(org_id, role_from_external(str(item.get("role", ""))))


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
