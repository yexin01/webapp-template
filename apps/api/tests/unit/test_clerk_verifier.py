"""Server-side session-token verification (AUTH-01 to AUTH-04, NT-11).

Tokens are signed with a throwaway RSA key generated per test session; the
verifier is the production ``ClerkTokenVerifier`` configured with that key.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from functools import partial
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from jwt.algorithms import ECAlgorithm, RSAAlgorithm

from app.kernel.errors import AuthenticationError, DependencyUnavailableError
from app.modules.identity.adapters import clerk
from app.modules.identity.adapters.clerk import ClerkTokenVerifier, JwksKeySet

ISSUER = "https://example-app.clerk.accounts.dev"
ORIGIN = "http://localhost:3000"
JWKS_URL = f"{ISSUER}/.well-known/jwks.json"


@pytest.fixture(scope="module")
def keys() -> tuple[Any, str]:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = (
        private.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    return private, public_pem


@pytest.fixture
def verifier(keys: tuple[Any, str]) -> ClerkTokenVerifier:
    return ClerkTokenVerifier(issuer=ISSUER, authorized_parties=[ORIGIN], jwt_public_key=keys[1])


def token(private: Any, kid: str | None = None, **overrides: Any) -> str:
    now = int(time.time())
    claims = {
        "sub": "user_123",
        "iss": ISSUER,
        "azp": ORIGIN,
        "iat": now,
        "nbf": now - 5,
        "exp": now + 60,
        "sid": "sess_1",
    }
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, private, algorithm="RS256", headers={"kid": kid} if kid else None)


def test_valid_token(verifier: ClerkTokenVerifier, keys: tuple[Any, str]) -> None:
    identity = verifier.verify(token(keys[0]))
    assert identity.external_user_id == "user_123"
    assert identity.session_id == "sess_1"
    assert identity.active_org_id is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"exp": int(time.time()) - 120},  # expired
        {"iss": "https://evil.example"},  # wrong issuer
        {"azp": "https://evil.example"},  # wrong authorized party
        {"azp": None},  # missing authorized party
        {"sub": None},  # missing subject
        {"nbf": int(time.time()) + 600},  # not yet valid
    ],
)
def test_rejected_claims(verifier: ClerkTokenVerifier, keys: tuple[Any, str], overrides: dict[str, Any]) -> None:
    with pytest.raises(AuthenticationError):
        verifier.verify(token(keys[0], **overrides))


def test_wrong_signing_key(verifier: ClerkTokenVerifier) -> None:
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(AuthenticationError):
        verifier.verify(token(other))


def test_alg_none_rejected(verifier: ClerkTokenVerifier) -> None:
    unsigned = jwt.encode(
        {"sub": "user_123", "iss": ISSUER, "azp": ORIGIN, "exp": int(time.time()) + 60}, None, algorithm="none"
    )  # type: ignore[arg-type]
    with pytest.raises(AuthenticationError):
        verifier.verify(unsigned)


def test_hmac_confusion_rejected(verifier: ClerkTokenVerifier, keys: tuple[Any, str]) -> None:
    forged = jwt.encode(
        {"sub": "user_123", "iss": ISSUER, "azp": ORIGIN, "iat": int(time.time()), "exp": int(time.time()) + 60},
        "attacker-chosen-secret-of-sufficient-length-0123456789",
        algorithm="HS256",
    )
    with pytest.raises(AuthenticationError):
        verifier.verify(forged)


@pytest.mark.parametrize("raw", ["", "not.a.jwt", "x" * 9000])
def test_malformed_tokens(verifier: ClerkTokenVerifier, raw: str) -> None:
    with pytest.raises(AuthenticationError):
        verifier.verify(raw)


def test_pending_sessions_are_signed_out(verifier: ClerkTokenVerifier, keys: tuple[Any, str]) -> None:
    """A pending session (a required step such as choosing an organization is
    unfinished) is treated as signed out, as Clerk's SDKs do by default."""
    with pytest.raises(AuthenticationError):
        verifier.verify(token(keys[0], v=2, sts="pending"))
    assert verifier.verify(token(keys[0], v=2, sts="active")).external_user_id == "user_123"


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"v": 2, "o": {"id": "org_v2", "rol": "admin", "slg": "acme"}}, "org_v2"),  # session token v2
        ({"org_id": "org_v1", "org_role": "org:admin"}, "org_v1"),  # session token v1
        ({"v": 2, "o": "org_not_an_object"}, None),
        ({"v": 2, "o": {"id": 42}}, None),
        ({}, None),
    ],
)
def test_active_organization_claim(
    verifier: ClerkTokenVerifier, keys: tuple[Any, str], overrides: dict[str, Any], expected: str | None
) -> None:
    assert verifier.verify(token(keys[0], **overrides)).active_org_id == expected


# -- JWKS key set (AUTH-04, §16.4) ------------------------------------------------


def jwk(private: Any, kid: str, **extra: Any) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(RSAAlgorithm.to_jwk(private.public_key()))
    return {**data, "kid": kid, "use": "sig", "alg": "RS256", **extra}


class FakeMonotonic:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


class Published:
    """The issuer's JWKS endpoint: serves ``document``, or fails, and counts fetches."""

    def __init__(self, *jwks: dict[str, Any]) -> None:
        self.document: object = {"keys": list(jwks)}
        self.error: Exception | None = None
        self.fetches = 0
        self.gate: threading.Event | None = None  # when set, a fetch waits for it (a slow endpoint)

    def __call__(self) -> object:
        self.fetches += 1
        if self.gate is not None:
            self.gate.wait(10)
        if self.error is not None:
            raise self.error
        return json.loads(json.dumps(self.document))


def run_now(work: Callable[[], None]) -> None:
    work()


def jwks_verifier(
    fetch: Callable[[], object],
    clock: FakeMonotonic,
    in_background: Callable[[Callable[[], None]], None] = run_now,  # deterministic unless a test needs a thread
) -> ClerkTokenVerifier:
    key_set = JwksKeySet(JWKS_URL, fetch=fetch, clock=clock, in_background=in_background)
    return ClerkTokenVerifier(issuer=ISSUER, authorized_parties=[ORIGIN], key_set=key_set)


def test_jwks_unreachable_fails_closed(keys: tuple[Any, str]) -> None:
    published = Published(jwk(keys[0], "kid1"))
    published.error = httpx.ConnectError("down")
    verifier = jwks_verifier(published, FakeMonotonic())
    with pytest.raises(DependencyUnavailableError):
        verifier.verify(token(keys[0], kid="kid1"))


@pytest.mark.parametrize("kid", [None, ""])
def test_a_token_without_a_key_id_is_rejected(keys: tuple[Any, str], kid: Any) -> None:
    published = Published(jwk(keys[0], "kid1"))
    verifier = jwks_verifier(published, FakeMonotonic())
    forged = jwt.encode(
        {"sub": "user_123", "iss": ISSUER, "azp": ORIGIN, "iat": int(time.time()), "exp": int(time.time()) + 60},
        keys[0],
        algorithm="RS256",
        headers={"kid": kid} if kid is not None else None,
    )
    with pytest.raises(AuthenticationError):
        verifier.verify(forged)
    assert published.fetches == 0


def test_a_key_removed_from_the_key_set_stops_verifying(keys: tuple[Any, str]) -> None:
    """AUTH-04: rotating or revoking a key takes effect once the cached key set expires."""
    replacement = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    published = Published(jwk(keys[0], "kid1"))
    clock = FakeMonotonic()
    verifier = jwks_verifier(published, clock)
    old = token(keys[0], kid="kid1")
    assert verifier.verify(old).external_user_id == "user_123"

    published.document = {"keys": [jwk(replacement, "kid2")]}
    clock.now += clerk._JWKS_LIFESPAN_SECONDS
    with pytest.raises(AuthenticationError):
        verifier.verify(old)
    assert verifier.verify(token(replacement, kid="kid2")).external_user_id == "user_123"


def test_an_unknown_key_id_refreshes_the_key_set_at_most_once_per_interval(keys: tuple[Any, str]) -> None:
    """A new key is picked up at once; a flood of unknown key IDs fetches at most once per interval."""
    added = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    published = Published(jwk(keys[0], "kid1"))
    clock = FakeMonotonic()
    verifier = jwks_verifier(published, clock)
    verifier.verify(token(keys[0], kid="kid1"))
    assert published.fetches == 1

    clock.now += clerk._JWKS_MIN_REFRESH_INTERVAL_SECONDS
    published.document = {"keys": [jwk(keys[0], "kid1"), jwk(added, "kid2")]}
    assert verifier.verify(token(added, kid="kid2")).external_user_id == "user_123"
    assert published.fetches == 2

    for n in range(20):
        with pytest.raises(AuthenticationError):
            verifier.verify(token(keys[0], kid=f"unknown-{n}"))
    assert published.fetches == 2  # within the interval: answered from the cached set, no fetch
    clock.now += clerk._JWKS_MIN_REFRESH_INTERVAL_SECONDS
    with pytest.raises(AuthenticationError):
        verifier.verify(token(keys[0], kid="unknown-late"))
    assert published.fetches == 3


def test_a_jwks_brownout_never_blocks_a_cached_key(keys: tuple[Any, str]) -> None:
    """While a fetch hangs, verification with a cached key does not wait, and
    tokens with unknown key IDs cause no further fetches (§16.4)."""
    published = Published(jwk(keys[0], "kid1"))
    clock = FakeMonotonic()
    verifier = jwks_verifier(published, clock)
    good = token(keys[0], kid="kid1")
    verifier.verify(good)

    clock.now += clerk._JWKS_MIN_REFRESH_INTERVAL_SECONDS
    published.gate = threading.Event()  # the endpoint hangs ...
    published.error = httpx.ReadTimeout("timed out")  # ... and then times out
    outcomes: list[str] = []

    def unknown_kid(n: int) -> None:
        try:
            verifier.verify(token(keys[0], kid=f"unknown-{n}"))
            outcomes.append("verified")
        except (AuthenticationError, DependencyUnavailableError) as exc:
            outcomes.append(type(exc).__name__)

    attackers = [threading.Thread(target=unknown_kid, args=(n,)) for n in range(5)]
    for attacker in attackers:
        attacker.start()
    deadline = time.monotonic() + 5
    while published.fetches < 1 + 1 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert published.fetches == 2  # one fetch in flight

    started = time.monotonic()
    assert verifier.verify(good).external_user_id == "user_123"
    assert time.monotonic() - started < 0.5  # not queued behind the hanging fetch

    published.gate.set()
    for attacker in attackers:
        attacker.join(10)
    assert published.fetches == 2  # the waiters re-read the snapshot instead of fetching again
    # The key set could not be read, so an unknown key ID may be a key Clerk just
    # rotated in: the outage is reported (503), not a signed-out session (401).
    assert outcomes == ["DependencyUnavailableError"] * 5


def test_a_failed_refresh_keeps_the_previous_key_set(keys: tuple[Any, str]) -> None:
    """Past three quarters of its lifespan, one request refreshes the set; a
    failed or unusable answer never replaces it."""
    published = Published(jwk(keys[0], "kid1"))
    clock = FakeMonotonic()
    verifier = jwks_verifier(published, clock)
    good = token(keys[0], kid="kid1")
    verifier.verify(good)

    clock.now += clerk._JWKS_REFRESH_AHEAD_SECONDS
    published.error = httpx.ConnectError("down")
    assert verifier.verify(good).external_user_id == "user_123"  # served while the refresh fails
    assert published.fetches == 2
    assert verifier.verify(good).external_user_id == "user_123"
    assert published.fetches == 2  # throttled

    published.error = None
    unusable_answers: list[object] = [
        {"keys": []},
        {"keys": [jwk(keys[0], "kid1", use="enc")]},
        ["not", "an", "object"],
    ]
    for unusable in unusable_answers:
        clock.now += clerk._JWKS_MIN_REFRESH_INTERVAL_SECONDS
        published.document = unusable
        assert verifier.verify(good).external_user_id == "user_123"
    assert published.fetches == 5


def test_the_refresh_ahead_of_expiry_never_makes_a_request_wait(keys: tuple[Any, str]) -> None:
    """The refresh past three quarters of the lifespan runs on its own thread (§16.4)."""
    added = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    published = Published(jwk(keys[0], "kid1"))
    clock = FakeMonotonic()
    verifier = jwks_verifier(published, clock, in_background=clerk._start_daemon_thread)
    good = token(keys[0], kid="kid1")
    verifier.verify(good)

    clock.now += clerk._JWKS_REFRESH_AHEAD_SECONDS
    published.document = {"keys": [jwk(keys[0], "kid1"), jwk(added, "kid2")]}
    published.gate = threading.Event()  # the endpoint is slow
    started = time.monotonic()
    assert verifier.verify(good).external_user_id == "user_123"
    assert time.monotonic() - started < 0.5  # served from the cached set while the refresh runs
    deadline = time.monotonic() + 5
    while published.fetches < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert published.fetches == 2

    published.gate.set()
    deadline = time.monotonic() + 5
    while verifier._key_set._lock.locked() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert verifier.verify(token(added, kid="kid2")).external_user_id == "user_123"  # the refreshed set
    assert published.fetches == 2


def test_a_key_that_cannot_verify_rs256_is_ignored(keys: tuple[Any, str]) -> None:
    """Only RSA keys can verify RS256 (AUTH-02); another key type is never handed to the decoder."""
    ec_key = json.loads(ECAlgorithm.to_jwk(ec.generate_private_key(ec.SECP256R1()).public_key()))
    published = Published(jwk(keys[0], "kid1"), {**ec_key, "kid": "ec1", "use": "sig"})
    verifier = jwks_verifier(published, FakeMonotonic())
    with pytest.raises(AuthenticationError):
        verifier.verify(token(keys[0], kid="ec1"))
    assert verifier.verify(token(keys[0], kid="kid1")).external_user_id == "user_123"


def test_an_expired_key_set_fails_closed_when_it_cannot_be_refreshed(keys: tuple[Any, str]) -> None:
    published = Published(jwk(keys[0], "kid1"))
    clock = FakeMonotonic()
    verifier = jwks_verifier(published, clock)
    good = token(keys[0], kid="kid1")
    verifier.verify(good)

    published.error = httpx.ConnectError("down")
    clock.now += clerk._JWKS_LIFESPAN_SECONDS - 1
    assert verifier.verify(good).external_user_id == "user_123"  # still within the lifespan
    clock.now += 1
    with pytest.raises(DependencyUnavailableError):
        verifier.verify(good)
    fetches = published.fetches
    with pytest.raises(DependencyUnavailableError):
        verifier.verify(good)
    assert published.fetches == fetches  # fails fast within the interval

    published.error = None
    clock.now += clerk._JWKS_MIN_REFRESH_INTERVAL_SECONDS
    assert verifier.verify(good).external_user_id == "user_123"  # recovers with the next fetch


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="<html>maintenance</html>"),  # not JSON
        httpx.Response(200, json=["not", "an", "object"]),
        httpx.Response(200, json={"keys": "not-a-list"}),
        httpx.Response(200, json={"keys": [{"kty": "RSA", "n": "AQAB", "e": "AQAB"}]}),  # no usable key
        httpx.Response(302, headers={"location": "https://elsewhere.example/jwks.json"}),  # never followed
        httpx.Response(503, text="unavailable"),
    ],
)
def test_a_bad_key_set_answer_is_a_failed_refresh(keys: tuple[Any, str], response: httpx.Response) -> None:
    """Whatever the endpoint answers, verification fails closed with a 503, never a 500."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return response

    fetch = partial(clerk.fetch_jwks, JWKS_URL, transport=httpx.MockTransport(handler))
    verifier = jwks_verifier(fetch, FakeMonotonic())
    with pytest.raises(DependencyUnavailableError):
        verifier.verify(token(keys[0], kid="kid1"))
    assert [str(r.url) for r in requests] == [JWKS_URL]


def test_the_default_fetch_reads_the_key_set(keys: tuple[Any, str]) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"keys": [jwk(keys[0], "kid1")]}))
    verifier = jwks_verifier(partial(clerk.fetch_jwks, JWKS_URL, transport=transport), FakeMonotonic())
    assert verifier.verify(token(keys[0], kid="kid1")).external_user_id == "user_123"
