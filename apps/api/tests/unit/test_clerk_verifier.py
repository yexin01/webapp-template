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

from app.domain.errors import AuthenticationError, DependencyUnavailableError
from app.infrastructure.clerk import ClerkTokenVerifier

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
