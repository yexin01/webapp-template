"""Server-side session-token verification (AUTH-01 to AUTH-04, NT-11).

Tokens are signed with a throwaway RSA key generated per test session; the
verifier is the production ``ClerkTokenVerifier`` configured with that key.
"""

from __future__ import annotations

import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.kernel.errors import AuthenticationError, DependencyUnavailableError
from app.modules.identity.adapters.clerk import ClerkTokenVerifier

ISSUER = "https://example-app.clerk.accounts.dev"
ORIGIN = "http://localhost:3000"


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


def token(private: Any, **overrides: Any) -> str:
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
    return jwt.encode(claims, private, algorithm="RS256")


def test_valid_token(verifier: ClerkTokenVerifier, keys: tuple[Any, str]) -> None:
    identity = verifier.verify(token(keys[0]))
    assert identity.external_user_id == "user_123"
    assert identity.session_id == "sess_1"


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


def test_jwks_unreachable_fails_closed(keys: tuple[Any, str]) -> None:
    class DownJwks:
        def get_signing_key_from_jwt(self, _token: str) -> Any:
            raise jwt.PyJWKClientConnectionError("down")

    verifier = ClerkTokenVerifier(issuer=ISSUER, authorized_parties=[ORIGIN], jwks_client=DownJwks())  # type: ignore[arg-type]
    with pytest.raises(DependencyUnavailableError):
        verifier.verify(token(keys[0]))


def test_a_key_removed_from_the_key_set_stops_verifying(monkeypatch: pytest.MonkeyPatch, keys: tuple[Any, str]) -> None:
    """AUTH-04: rotating or revoking a key takes effect once the cached key set expires."""
    import json

    from jwt import PyJWKClient
    from jwt.algorithms import RSAAlgorithm

    from app.modules.identity.adapters import clerk

    def jwk(private: Any, kid: str) -> dict[str, Any]:
        data: dict[str, Any] = json.loads(RSAAlgorithm.to_jwk(private.public_key()))
        return {**data, "kid": kid, "use": "sig", "alg": "RS256"}

    replacement = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    published = {"keys": [jwk(keys[0], "kid1")]}

    def fetch_data(self: PyJWKClient) -> Any:  # no network: serve the published set
        data = json.loads(json.dumps(published))
        if self.jwk_set_cache is not None:
            self.jwk_set_cache.put(data)
        return data

    monkeypatch.setattr(PyJWKClient, "fetch_data", fetch_data)
    monkeypatch.setattr(clerk, "_JWKS_LIFESPAN_SECONDS", 0.05)
    verifier = ClerkTokenVerifier(issuer=ISSUER, authorized_parties=[ORIGIN])
    now = int(time.time())
    claims = {"sub": "user_123", "iss": ISSUER, "azp": ORIGIN, "iat": now, "exp": now + 60}
    old = jwt.encode(claims, keys[0], algorithm="RS256", headers={"kid": "kid1"})
    assert verifier.verify(old).external_user_id == "user_123"

    published["keys"] = [jwk(replacement, "kid2")]
    time.sleep(0.1)
    with pytest.raises(AuthenticationError):
        verifier.verify(old)
    new = jwt.encode(claims, replacement, algorithm="RS256", headers={"kid": "kid2"})
    assert verifier.verify(new).external_user_id == "user_123"
