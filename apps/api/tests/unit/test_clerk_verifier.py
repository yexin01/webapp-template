"""Server-side session-token verification (AUTH-01 to AUTH-04, NT-11).

Tokens are signed with a throwaway RSA key generated per test session; the
verifier is the production ``ClerkTokenVerifier`` configured with that key.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
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
from app.kernel.infrastructure.db import REQUEST_THREADS
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


@pytest.mark.parametrize("claim", ["exp", "iat", "iss"])
def test_a_token_without_a_required_claim_is_refused(
    verifier: ClerkTokenVerifier, keys: tuple[Any, str], claim: str
) -> None:
    """AUTH-01: a token says when it expires, when it was issued and by whom. One without an expiry would never end."""
    with pytest.raises(AuthenticationError):
        verifier.verify(token(keys[0], **{claim: None}))


@pytest.mark.parametrize(
    "timing",
    [
        pytest.param(lambda now: {"exp": now - 30}, id="expired 30 s ago"),
        pytest.param(lambda now: {"nbf": now + 30}, id="valid from 30 s ahead"),
        pytest.param(lambda now: {"iat": now + 30, "nbf": None}, id="issued 30 s ahead"),
    ],
)
def test_a_token_outside_the_clock_skew_is_refused(
    verifier: ClerkTokenVerifier, keys: tuple[Any, str], timing: Callable[[int], dict[str, Any]]
) -> None:
    """AUTH-01: the skew is a few seconds (5), not the half minute a wider tolerance would accept."""
    with pytest.raises(AuthenticationError):
        verifier.verify(token(keys[0], **timing(int(time.time()))))


def test_a_token_a_second_off_is_within_the_clock_skew(verifier: ClerkTokenVerifier, keys: tuple[Any, str]) -> None:
    """Instances' clocks differ by a little: an expiry a second ago, or a start a second ahead, still verifies."""
    now = int(time.time())
    assert verifier.verify(token(keys[0], exp=now - 1, nbf=now + 1, iat=now + 1)).external_user_id == "user_123"


def azp_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.message == "clerk_azp_refused"]


def test_a_refused_origin_is_logged_with_its_cause_and_never_the_token(
    verifier: ClerkTokenVerifier, keys: tuple[Any, str], caplog: pytest.LogCaptureFixture
) -> None:
    """A preview's origin that CLERK_AUTHORIZED_PARTIES does not list is otherwise an anonymous 401."""
    jwt_value = token(keys[0], azp="https://my-app-git-feature-team.vercel.app")
    with caplog.at_level(logging.WARNING, logger=clerk.logger.name), pytest.raises(AuthenticationError):
        verifier.verify(jwt_value)
    [record] = azp_records(caplog)
    assert record.levelno == logging.WARNING
    assert record.cause == "azp_not_authorized"  # type: ignore[attr-defined]
    assert record.azp == "https://my-app-git-feature-team.vercel.app"  # type: ignore[attr-defined]
    for part in jwt_value.split("."):
        assert part not in caplog.text


def test_a_missing_origin_is_logged_with_its_cause(
    verifier: ClerkTokenVerifier, keys: tuple[Any, str], caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger=clerk.logger.name), pytest.raises(AuthenticationError):
        verifier.verify(token(keys[0], azp=None))
    [record] = azp_records(caplog)
    assert record.cause == "azp_missing"  # type: ignore[attr-defined]
    assert record.azp is None  # type: ignore[attr-defined]


def test_a_claim_that_is_no_origin_is_not_echoed(
    verifier: ClerkTokenVerifier, keys: tuple[Any, str], caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger=clerk.logger.name), pytest.raises(AuthenticationError):
        verifier.verify(token(keys[0], azp="https://x.example/\nforged log line"))
    [record] = azp_records(caplog)
    assert record.cause == "azp_not_authorized"  # type: ignore[attr-defined]
    assert record.azp is None  # type: ignore[attr-defined]


def test_refused_origins_are_logged_at_most_once_a_minute(
    verifier: ClerkTokenVerifier,
    keys: tuple[Any, str],
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [1_000.0]
    monkeypatch.setattr(clerk, "time", type("T", (), {"monotonic": staticmethod(lambda: now[0])}))
    with caplog.at_level(logging.WARNING, logger=clerk.logger.name):
        for _ in range(3):
            with pytest.raises(AuthenticationError):
                verifier.verify(token(keys[0], azp="https://evil.example"))
        assert len(azp_records(caplog)) == 1
        now[0] += 61
        with pytest.raises(AuthenticationError):
            verifier.verify(token(keys[0], azp="https://evil.example"))
        assert len(azp_records(caplog)) == 2


def test_an_accepted_origin_logs_nothing(
    verifier: ClerkTokenVerifier, keys: tuple[Any, str], caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger=clerk.logger.name):
        verifier.verify(token(keys[0]))
    assert azp_records(caplog) == []


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


def hmac_signed_with(public_pem: str, algorithm: str, kid: str | None = None) -> str:
    """The token of an algorithm-confusion attack: valid claims, and a MAC keyed with the public key's PEM text, which
    anyone can read. Built by hand, because PyJWT refuses to sign with a PEM as a secret."""

    def part(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    now = int(time.time())
    header = {"alg": algorithm, "typ": "JWT", **({"kid": kid} if kid else {})}
    claims = {"sub": "user_123", "iss": ISSUER, "azp": ORIGIN, "iat": now, "nbf": now - 5, "exp": now + 60}
    signing_input = f"{part(json.dumps(header).encode())}.{part(json.dumps(claims).encode())}"
    digest = {"HS256": hashlib.sha256, "HS384": hashlib.sha384, "HS512": hashlib.sha512}[algorithm]
    mac = hmac.new(public_pem.encode(), signing_input.encode(), digest).digest()
    return f"{signing_input}.{part(mac)}"


@pytest.mark.parametrize("algorithm", ["HS256", "HS384", "HS512"])
def test_a_token_macd_with_the_configured_public_key_is_refused(
    verifier: ClerkTokenVerifier, keys: tuple[Any, str], algorithm: str
) -> None:
    """AUTH-02: the classic algorithm confusion, against the verifier that holds the key as a PEM."""
    with pytest.raises(AuthenticationError):
        verifier.verify(hmac_signed_with(keys[1], algorithm))


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


def refused_wait_limits(caplog: pytest.LogCaptureFixture) -> list[Any]:
    """The logged bound of each request refused a wait for a key-set fetch in flight."""
    return [vars(r)["limit"] for r in caplog.records if r.getMessage() == "clerk_jwks_waiters_exhausted"]


def verify_timed(verifier: ClerkTokenVerifier, session_token: str) -> tuple[str, float]:
    """The outcome of verifying a token, and how many seconds it took."""
    started = time.monotonic()
    try:
        verifier.verify(session_token)
        outcome = "verified"
    except (AuthenticationError, DependencyUnavailableError) as exc:
        outcome = type(exc).__name__
    return outcome, time.monotonic() - started


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


@pytest.mark.parametrize("last_attempt", ["succeeded", "failed"])
def test_only_a_few_requests_wait_for_a_key_set_fetch_in_flight(
    keys: tuple[Any, str], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, last_attempt: str
) -> None:
    """Tokens with made-up key IDs cannot park request threads behind a slow fetch (§16.4).

    While the key set is usable, a few wait for the fetch in flight, even if the
    last attempt failed; the rest get the answer for a key set that cannot be
    read (503) at once, and each refusal is logged. A cached key is never delayed.
    """
    monkeypatch.setattr(clerk, "_JWKS_TIMEOUT_SECONDS", 30.0)  # the waiters stay parked until the fetch ends
    caplog.set_level(logging.WARNING, logger=clerk.__name__)
    published = Published(jwk(keys[0], "kid1"))
    clock = FakeMonotonic()
    verifier = jwks_verifier(published, clock)
    good = token(keys[0], kid="kid1")
    verifier.verify(good)
    if last_attempt == "failed":
        clock.now += clerk._JWKS_MIN_REFRESH_INTERVAL_SECONDS
        published.error = httpx.ConnectError("down")
        with pytest.raises(DependencyUnavailableError):
            verifier.verify(token(keys[0], kid="unknown-before"))
        published.error = None

    clock.now += clerk._JWKS_MIN_REFRESH_INTERVAL_SECONDS
    fetches = published.fetches
    published.gate = threading.Event()  # the endpoint hangs
    outcomes: list[str] = []
    refusal_seconds: list[float] = []

    def unknown_kid(n: int) -> None:
        outcome, seconds = verify_timed(verifier, token(keys[0], kid=f"unknown-{n}"))
        if outcome == "DependencyUnavailableError":
            refusal_seconds.append(seconds)
        outcomes.append(outcome)

    fetcher = threading.Thread(target=unknown_kid, args=(0,))
    callers = [threading.Thread(target=unknown_kid, args=(n,)) for n in range(1, 13)]
    waiters = 4  # the documented bound, not read from the code under test
    refused = len(callers) - waiters
    try:
        fetcher.start()
        deadline = time.monotonic() + 5
        while published.fetches == fetches and time.monotonic() < deadline:
            time.sleep(0.01)
        assert published.fetches == fetches + 1  # one fetch in flight
        for caller in callers:
            caller.start()
        deadline = time.monotonic() + 5
        while len(outcomes) < refused and time.monotonic() < deadline:
            time.sleep(0.01)
        assert outcomes == ["DependencyUnavailableError"] * refused  # while the fetch is in flight
        assert max(refusal_seconds) < 0.5  # each at once, not after a wait
        deadline = time.monotonic() + 5
        while sum(caller.is_alive() for caller in callers) > waiters and time.monotonic() < deadline:
            time.sleep(0.01)  # a refused caller may not have exited yet
        assert sum(caller.is_alive() for caller in callers) == waiters

        started = time.monotonic()
        assert verifier.verify(good).external_user_id == "user_123"
        assert time.monotonic() - started < 0.5
    finally:
        published.gate.set()
        for thread in (fetcher, *callers):
            thread.join(10)
    assert published.fetches == fetches + 1
    assert verifier._key_set._waiting == 0  # every waiter gave its place back
    # The fetch succeeded without the made-up keys: the fetcher and the waiters are signed out.
    assert sorted(outcomes) == ["AuthenticationError"] * (1 + waiters) + ["DependencyUnavailableError"] * refused
    assert refused_wait_limits(caplog) == [waiters] * refused


@pytest.mark.parametrize("key_set", ["none yet", "expired"])
def test_a_flood_without_a_usable_key_set_parks_only_a_few_request_threads(
    keys: tuple[Any, str], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, key_set: str
) -> None:
    """A fresh instance, or one whose key set expired while Clerk was answering, can verify no
    token without the fetch in flight, so valid tokens wait for it instead of getting 503. Only a
    bounded number wait: a flood as large as the request threadpool, made-up key IDs or not,
    cannot park every request thread (§16.4). The rest get 503 at once, each refusal logged."""
    monkeypatch.setattr(clerk, "_JWKS_TIMEOUT_SECONDS", 30.0)  # the waiters stay parked until the fetch ends
    caplog.set_level(logging.WARNING, logger=clerk.__name__)
    published = Published(jwk(keys[0], "kid1"))
    clock = FakeMonotonic()
    verifier = jwks_verifier(published, clock)
    good = token(keys[0], kid="kid1")
    if key_set == "expired":
        verifier.verify(good)
        clock.now += clerk._JWKS_LIFESPAN_SECONDS
    fetches = published.fetches
    published.gate = threading.Event()  # the fetch takes a moment, as a network round trip does
    outcomes: list[str] = []
    refusal_seconds: list[float] = []
    lock = threading.Lock()

    def request() -> None:
        outcome, seconds = verify_timed(verifier, good)
        with lock:
            if outcome == "DependencyUnavailableError":
                refusal_seconds.append(seconds)
            outcomes.append(outcome)

    requests = [threading.Thread(target=request) for _ in range(REQUEST_THREADS)]
    waiters = 16  # the documented bound, not read from the code under test
    refused = len(requests) - 1 - waiters
    assert 1 + waiters <= REQUEST_THREADS // 2  # a cold fetch holds at most half the request threads
    try:
        for thread in requests:
            thread.start()
        deadline = time.monotonic() + 5
        # One request fetches; each of the others waits for that fetch, or was refused at once.
        while (
            published.fetches == fetches or verifier._key_set._waiting + len(outcomes) < len(requests) - 1
        ) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert published.fetches == fetches + 1  # one fetch in flight
        assert verifier._key_set._waiting == waiters
        assert outcomes == ["DependencyUnavailableError"] * refused  # while the fetch is in flight
        assert max(refusal_seconds) < 0.5  # each at once, not after a wait
        deadline = time.monotonic() + 5
        while sum(thread.is_alive() for thread in requests) > 1 + waiters and time.monotonic() < deadline:
            time.sleep(0.01)  # a refused request may not have exited yet
        assert sum(thread.is_alive() for thread in requests) == 1 + waiters  # the fetcher and its waiters
    finally:
        published.gate.set()
        for thread in requests:
            thread.join(10)
    assert published.fetches == fetches + 1  # the waiters use its result instead of fetching again
    assert verifier._key_set._waiting == 0  # every waiter gave its place back
    assert sorted(outcomes) == ["DependencyUnavailableError"] * refused + ["verified"] * (1 + waiters)
    assert refused_wait_limits(caplog) == [waiters] * refused


@pytest.mark.parametrize("key_set", ["none yet", "expired"])
@pytest.mark.parametrize("since_failure", [clerk._JWKS_MIN_REFRESH_INTERVAL_SECONDS, 29.0])  # the next retry; 29 s on
def test_during_an_outage_no_request_waits_for_a_key_set_fetch_in_flight(
    keys: tuple[Any, str],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    key_set: str,
    since_failure: float,
) -> None:
    """With no usable key set and an attempt failed in the last 30 s, a request arriving while the
    next attempt is in flight gets 503 at once instead of parking a request thread for it, on every
    throttled retry of a Clerk outage (§16.4). The refusal is logged."""
    monkeypatch.setattr(clerk, "_JWKS_TIMEOUT_SECONDS", 30.0)  # a waiter would stay parked until the fetch ends
    caplog.set_level(logging.WARNING, logger=clerk.__name__)
    published = Published(jwk(keys[0], "kid1"))
    clock = FakeMonotonic()
    verifier = jwks_verifier(published, clock)
    good = token(keys[0], kid="kid1")
    if key_set == "expired":
        verifier.verify(good)
        clock.now += clerk._JWKS_LIFESPAN_SECONDS
    published.error = httpx.ConnectError("down")
    with pytest.raises(DependencyUnavailableError):
        verifier.verify(good)  # the last attempt failed

    clock.now += since_failure
    published.error = None  # the retry succeeds ...
    published.gate = threading.Event()  # ... once its round trip ends
    fetches = published.fetches
    outcomes: dict[str, str] = {}
    seconds: dict[str, float] = {}

    def request(name: str) -> None:
        outcomes[name], seconds[name] = verify_timed(verifier, good)

    retry = threading.Thread(target=request, args=("retry",))
    arrival = threading.Thread(target=request, args=("arrival",))
    try:
        retry.start()
        deadline = time.monotonic() + 5
        while published.fetches == fetches and time.monotonic() < deadline:
            time.sleep(0.01)
        assert published.fetches == fetches + 1  # the retry is in flight
        arrival.start()
        arrival.join(5)
        assert not arrival.is_alive()  # answered while the retry is still in flight
        assert outcomes == {"arrival": "DependencyUnavailableError"}
        assert seconds["arrival"] < 0.5  # at once, not after a wait
    finally:
        published.gate.set()
        for thread in (retry, arrival):
            thread.join(10)
    assert outcomes == {"arrival": "DependencyUnavailableError", "retry": "verified"}
    assert published.fetches == fetches + 1
    assert verifier._key_set._waiting == 0
    assert refused_wait_limits(caplog) == [0]


@pytest.mark.parametrize("idle", [30.0, 600.0])
def test_a_failed_attempt_30_s_old_is_not_an_outage(
    keys: tuple[Any, str], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, idle: float
) -> None:
    """Only a failure in the last 30 s means an outage. When a refresh ahead of expiry fails once and
    nothing arrives until the key set has expired, the next requests wait for the fetch in flight, as
    on a fresh instance, instead of getting 503 while Clerk answers (§16.4)."""
    monkeypatch.setattr(clerk, "_JWKS_TIMEOUT_SECONDS", 30.0)  # the waiters stay parked until the fetch ends
    caplog.set_level(logging.WARNING, logger=clerk.__name__)
    published = Published(jwk(keys[0], "kid1"))
    clock = FakeMonotonic()
    verifier = jwks_verifier(published, clock)
    good = token(keys[0], kid="kid1")
    verifier.verify(good)
    clock.now += clerk._JWKS_LIFESPAN_SECONDS - idle
    published.error = httpx.ConnectError("a blip")
    assert verifier.verify(good).external_user_id == "user_123"  # its refresh ahead of expiry fails
    assert published.fetches == 2
    published.error = None
    clock.now += idle  # nothing arrives until the key set has expired
    published.gate = threading.Event()  # the fetch takes a moment, as a network round trip does
    outcomes: list[str] = []
    lock = threading.Lock()

    def request() -> None:
        outcome, _ = verify_timed(verifier, good)
        with lock:
            outcomes.append(outcome)

    waiters = 16  # the documented bound with no usable set
    fetcher = threading.Thread(target=request)
    callers = [threading.Thread(target=request) for _ in range(waiters)]
    try:
        fetcher.start()
        deadline = time.monotonic() + 5
        while published.fetches == 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert published.fetches == 3  # one fetch in flight
        for caller in callers:
            caller.start()
        deadline = time.monotonic() + 5
        while verifier._key_set._waiting + len(outcomes) < waiters and time.monotonic() < deadline:
            time.sleep(0.01)
        assert verifier._key_set._waiting == waiters  # each waits for the fetch; none is refused
    finally:
        published.gate.set()
        for thread in (fetcher, *callers):
            thread.join(10)
    assert outcomes == ["verified"] * (1 + waiters)
    assert published.fetches == 3
    assert verifier._key_set._waiting == 0
    assert refused_wait_limits(caplog) == []


def test_a_slow_failed_attempt_counts_from_when_it_ended(
    keys: tuple[Any, str], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The 30 s run from when a failed attempt ends, so an attempt that hangs before it fails (a
    brownout) is still an outage to the requests arriving during the next one."""
    monkeypatch.setattr(clerk, "_JWKS_TIMEOUT_SECONDS", 0.05)  # a wait, if any, ends quickly
    caplog.set_level(logging.WARNING, logger=clerk.__name__)
    clock = FakeMonotonic()
    published = Published(jwk(keys[0], "kid1"))
    published.error = httpx.ReadTimeout("timed out")

    def slow_fetch() -> object:
        clock.now += 20.0  # the attempt hangs this long before it fails
        return published()

    verifier = jwks_verifier(slow_fetch, clock)
    good = token(keys[0], kid="kid1")
    with pytest.raises(DependencyUnavailableError):
        verifier.verify(good)
    clock.now += 15.0  # 35 s after that attempt started, 15 s after it failed
    with verifier._key_set._lock, pytest.raises(DependencyUnavailableError):  # the next attempt is in flight
        verifier.verify(good)
    assert refused_wait_limits(caplog) == [0]  # refused at once, not let wait


def test_a_wait_that_times_out_gives_its_place_back(
    keys: tuple[Any, str], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A request that waits out the timeout for a fetch that hangs (a brownout) gets 503 and gives
    its place back, so a slow fetch never shrinks the bound for the requests after it (§16.4)."""
    monkeypatch.setattr(clerk, "_JWKS_TIMEOUT_SECONDS", 0.3)  # each wait ends while the fetch still hangs
    caplog.set_level(logging.WARNING, logger=clerk.__name__)
    published = Published(jwk(keys[0], "kid1"))
    verifier = jwks_verifier(published, FakeMonotonic())
    good = token(keys[0], kid="kid1")
    published.gate = threading.Event()  # the endpoint hangs
    outcomes: list[str] = []
    lock = threading.Lock()

    def request() -> None:
        try:
            verifier.verify(good)
            outcome = "verified"
        except (AuthenticationError, DependencyUnavailableError) as exc:
            outcome = type(exc).__name__
        with lock:
            outcomes.append(outcome)

    fetcher = threading.Thread(target=request)
    waiters = [threading.Thread(target=request) for _ in range(16)]  # the documented bound with no usable set
    try:
        fetcher.start()
        deadline = time.monotonic() + 5
        while published.fetches == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert published.fetches == 1  # one fetch in flight
        for waiter in waiters:
            waiter.start()
        for waiter in waiters:
            waiter.join(10)
        assert outcomes == ["DependencyUnavailableError"] * len(waiters)  # each waited out the timeout ...
        assert verifier._key_set._waiting == 0  # ... and gave its place back while the fetch still hangs
    finally:
        published.gate.set()
        fetcher.join(10)
    assert sorted(outcomes) == ["DependencyUnavailableError"] * len(waiters) + ["verified"]
    assert refused_wait_limits(caplog) == []  # every one of them was let wait


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


@pytest.mark.parametrize("algorithm", ["HS256", "HS384", "HS512"])
def test_a_token_macd_with_a_published_key_is_refused_before_any_key_is_looked_up(
    keys: tuple[Any, str], algorithm: str
) -> None:
    """AUTH-02: the pinned algorithm decides, not the JWT library's own guards. With a key set, an HMAC algorithm
    that got past the pin would reach the library with the key as an object: a TypeError, so a 500."""
    published = Published(jwk(keys[0], "kid1"))
    verifier = jwks_verifier(published, FakeMonotonic())
    with pytest.raises(AuthenticationError):
        verifier.verify(hmac_signed_with(keys[1], algorithm, kid="kid1"))
    assert published.fetches == 0  # no fetch for a token no key can verify


@pytest.mark.parametrize(("age", "verifies"), [(3_599, True), (3_600, False)])
def test_a_cached_key_set_is_trusted_for_an_hour_and_not_a_second_longer(
    keys: tuple[Any, str], age: int, verifies: bool
) -> None:
    """AUTH-04: the documented bound (docs/operations.md: "up to an hour"). The seconds are written out here, not
    read from the code, so lengthening the lifetime fails this test instead of passing every other one."""
    published = Published(jwk(keys[0], "kid1"))
    clock = FakeMonotonic()
    verifier = jwks_verifier(published, clock)
    good = token(keys[0], kid="kid1")
    verifier.verify(good)

    published.error = httpx.ConnectError("down")  # only the cached key set can verify now
    clock.now += age
    if verifies:
        assert verifier.verify(good).external_user_id == "user_123"
    else:
        with pytest.raises(DependencyUnavailableError):
            verifier.verify(good)


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


def test_an_own_session_has_no_actor(verifier: ClerkTokenVerifier, keys: tuple[Any, str]) -> None:
    assert verifier.verify(token(keys[0])).actor_external_id is None
    assert verifier.verify(token(keys[0], act=None)).actor_external_id is None


@pytest.mark.parametrize(
    "act",
    [
        {"iss": "https://dashboard.clerk.com", "sid": "sess_imp", "sub": "user_staff"},  # dashboard impersonation
        {"sub": "user_staff"},  # a Backend API actor token
        {"type": "agent", "sub": "agent_1"},  # an agent task
    ],
)
def test_the_act_claim_names_the_actor(
    verifier: ClerkTokenVerifier, keys: tuple[Any, str], act: dict[str, str]
) -> None:
    identity = verifier.verify(token(keys[0], act=act))
    assert identity.external_user_id == "user_123"
    assert identity.actor_external_id == act["sub"]


@pytest.mark.parametrize(
    "act",
    [
        "garbage",
        7,
        True,
        [],
        ["user_staff"],
        {},
        {"sub": ""},
        {"sub": 5},
        {"sub": None},
        {"sub": "x" * 256},
        {"iss": "x"},
    ],
)
def test_a_malformed_act_claim_is_an_invalid_token(
    verifier: ClerkTokenVerifier, keys: tuple[Any, str], act: Any
) -> None:
    """A token the verifier cannot interpret is not signed in; the response gives no reason (AUTH-03)."""
    with pytest.raises(AuthenticationError) as raised:
        verifier.verify(token(keys[0], act=act))
    assert raised.value.public_message == AuthenticationError.default_message
