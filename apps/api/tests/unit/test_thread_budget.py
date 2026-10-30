"""The request-thread budget (ADR-0003): scoped holders + connection waiters + bounded external work
(LLM checks, Stripe calls, Clerk membership reads and key-set lookups, webhook synchronization) never
exceed the request threads."""

from __future__ import annotations

import base64
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.bootstrap import build_container
from app.kernel.infrastructure.config import ConfigError
from app.kernel.infrastructure.db import API_ROLE, REQUEST_THREADS, WORKER_ROLE, Database
from app.modules.identity import module as identity_module
from app.modules.identity.adapters import clerk
from app.modules.identity.adapters.clerk import ClerkTokenVerifier
from app.settings import CLERK_KEY_SET_THREADS, MIN_WAITER_PLACES, reserved_request_threads
from tests.conftest import make_settings

NAMES = (
    "DATABASE_POOL_MAX_SIZE",
    "DATABASE_WORKER_POOL_MAX_SIZE",
    "LLM_MAX_CONCURRENT_CHECKS",
    "BILLING_MAX_CONCURRENT_STRIPE_CALLS",
    "CLERK_MAX_CONCURRENT_READS",
)
SMALLEST_BULKHEADS = {
    "database_worker_pool_max_size": 2,
    "llm_max_concurrent_checks": 1,
    "billing_max_concurrent_stripe_calls": 1,
    "clerk_max_concurrent_reads": 3,
}
# The defaults with a larger request pool: 40 - 14 - (4 + 4 + 8 + (3 - 1)) = 8 places for connection waiters.
AT_THE_LIMIT = {"database_pool_max_size": 14}
PUBLISHABLE = "pk_test_" + base64.b64encode(b"example-app.clerk.accounts.dev$").decode()
# Session tokens verified with keys fetched from Clerk: CLERK_JWT_KEY is empty, as in .env.example.
KEY_SET = {"clerk_publishable_key": PUBLISHABLE, "clerk_authorized_parties": "http://localhost:3000"}
KEY_SET_LOOKUP_MESSAGE = f"with the {CLERK_KEY_SET_THREADS} threads kept for Clerk key-set lookups"


@pytest.fixture(scope="module")
def jwt_key() -> str:
    """A PEM public key for CLERK_JWT_KEY: tokens are then verified without fetching keys."""
    public = rsa.generate_private_key(public_exponent=65537, key_size=2048).public_key()
    return public.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()


def test_the_defaults_leave_seventeen_places_for_connection_waiters() -> None:
    settings = make_settings()
    assert reserved_request_threads(settings) == 4 + 4 + 8 + (3 - 1)
    container = build_container(settings)
    try:
        database = container.kernel.database
        assert database.waiters_limit == REQUEST_THREADS - 5 - 18 == 17
        # Within the budget, each pool still keeps as many places as the other pool has connections.
        assert database.role_waiters_limits == {API_ROLE: 17 - 3, WORKER_ROLE: 17 - 5}
    finally:
        container.close()


def test_with_key_set_lookups_the_defaults_leave_twelve_places_for_connection_waiters() -> None:
    settings = make_settings(**KEY_SET)
    assert reserved_request_threads(settings) == 4 + 4 + 8 + (3 - 1) + CLERK_KEY_SET_THREADS
    container = build_container(settings)
    try:
        database = container.kernel.database
        assert database.waiters_limit == REQUEST_THREADS - 5 - 23 == 12
        assert database.role_waiters_limits == {API_ROLE: 12 - 3, WORKER_ROLE: 12 - 5}
    finally:
        container.close()


@pytest.mark.parametrize(
    ("overrides", "reserved"),
    [
        ({"clerk_max_concurrent_reads": 16}, 4 + 4 + 16 + (3 - 1)),
        ({"database_worker_pool_max_size": 10}, 4 + 4 + 8 + (10 - 1)),
        ({"llm_max_concurrent_checks": 9, "billing_max_concurrent_stripe_calls": 7}, 9 + 7 + 8 + (3 - 1)),
        ({**SMALLEST_BULKHEADS, "database_pool_max_size": 26}, 1 + 1 + 3 + (2 - 1)),
        ({**SMALLEST_BULKHEADS, **KEY_SET, "database_pool_max_size": 21}, 1 + 1 + 3 + (2 - 1) + 5),
    ],
)
def test_the_app_database_keeps_the_threads_that_bounded_external_work_may_hold(
    overrides: dict[str, Any], reserved: int
) -> None:
    settings = make_settings(**overrides)
    assert reserved_request_threads(settings) == reserved
    container = build_container(settings)
    try:
        database = container.kernel.database
        assert database.waiters_limit == REQUEST_THREADS - settings.database_pool_max_size - reserved
        assert database.waiters_limit >= MIN_WAITER_PLACES
    finally:
        container.close()


@pytest.mark.parametrize(
    ("clerk_settings", "with_jwt_key", "fetches_keys"),
    [
        ({}, False, False),  # APP_ENV=test without Clerk: every token is refused
        ({"clerk_publishable_key": PUBLISHABLE}, False, False),  # likewise, without authorized parties
        (KEY_SET, False, True),
        (KEY_SET, True, False),
    ],
)
def test_key_set_lookups_are_kept_threads_exactly_when_the_verifier_fetches_keys(
    clerk_settings: dict[str, str], with_jwt_key: bool, fetches_keys: bool, jwt_key: str
) -> None:
    settings = make_settings(**clerk_settings, **({"clerk_jwt_key": jwt_key} if with_jwt_key else {}))
    verifier = identity_module._verifier(settings)
    assert (isinstance(verifier, ClerkTokenVerifier) and not verifier._public_key) is fetches_keys
    kept = CLERK_KEY_SET_THREADS if fetches_keys else 0
    assert reserved_request_threads(settings) == 4 + 4 + 8 + (3 - 1) + kept


def test_the_threads_kept_for_key_set_lookups_match_the_identity_module_cap() -> None:
    """One fetch, and the requests that may wait for it while a usable key set is cached."""
    assert CLERK_KEY_SET_THREADS == 1 + clerk._JWKS_MAX_WAITERS


def test_reserved_threads_come_out_of_the_shared_waiter_budget() -> None:
    def build(api_pool_size: int = 5, **threads: int) -> Database:
        return Database(
            "postgresql://u:p@127.0.0.1:1/unused",
            api_pool_size=api_pool_size,
            worker_pool_size=3,
            connect_timeout=1,
            statement_timeout_ms=1000,
            **threads,
        )

    assert build().waiters_limit == REQUEST_THREADS - 5  # nothing reserved, as tests that build one directly
    reserved = build(reserved_threads=18)
    assert reserved.waiters_limit == 17
    assert reserved.role_waiters_limits == {API_ROLE: 14, WORKER_ROLE: 12}
    # Each pool keeps at most half of the budget that is left, never half of the threads before reserving.
    larger = build(api_pool_size=14, reserved_threads=18)
    assert larger.waiters_limit == 8
    assert larger.role_waiters_limits == {API_ROLE: 8 - 3, WORKER_ROLE: 8 - 8 // 2}
    assert build(request_threads=10, reserved_threads=9).waiters_limit == 1  # never below one place


def test_settings_that_leave_exactly_the_minimum_are_accepted() -> None:
    settings = make_settings(**AT_THE_LIMIT)
    assert REQUEST_THREADS - settings.database_pool_max_size - reserved_request_threads(settings) == MIN_WAITER_PLACES
    container = build_container(settings)
    try:
        database = container.kernel.database
        assert database.waiters_limit == MIN_WAITER_PLACES
        # The worker pool keeps its 3 places; the request pool (14) keeps half the budget.
        assert database.role_waiters_limits == {API_ROLE: 8 - 3, WORKER_ROLE: 8 - 8 // 2}
    finally:
        container.close()
    assert make_settings(**SMALLEST_BULKHEADS, database_pool_max_size=26).database_pool_max_size == 26


@pytest.mark.parametrize(
    "one_more",
    [
        {"database_pool_max_size": 15},
        {"database_worker_pool_max_size": 4},
        {"llm_max_concurrent_checks": 5},
        {"billing_max_concurrent_stripe_calls": 5},
        {"clerk_max_concurrent_reads": 9},
    ],
)
def test_one_more_thread_for_any_of_them_is_refused_by_name(one_more: dict[str, int]) -> None:
    """Each bound is valid on its own; together they must leave the connection waiters enough threads."""
    with pytest.raises(ConfigError) as excinfo:
        make_settings(**{**AT_THE_LIMIT, **one_more})
    message = str(excinfo.value)
    for name in NAMES:
        assert name in message
    assert f"leave {MIN_WAITER_PLACES - 1} of the {REQUEST_THREADS} request threads" in message
    assert "key-set" not in message


def test_key_set_lookups_leave_less_room_for_the_other_bounds(jwt_key: str) -> None:
    """40 - 9 - (4 + 4 + 8 + (3 - 1) + 5) = 8: while keys are fetched from Clerk, the default bulkheads
    allow a request pool of 9. With CLERK_JWT_KEY set, nothing is fetched, and they allow 14."""
    assert make_settings(**KEY_SET, database_pool_max_size=9).database_pool_max_size == 9
    with pytest.raises(ConfigError) as excinfo:
        make_settings(**KEY_SET, database_pool_max_size=10)
    message = str(excinfo.value)
    for name in NAMES:
        assert name in message
    assert f"{KEY_SET_LOOKUP_MESSAGE} while CLERK_JWT_KEY is empty" in message
    assert f"leave {MIN_WAITER_PLACES - 1} of the {REQUEST_THREADS} request threads" in message
    assert make_settings(**KEY_SET, clerk_jwt_key=jwt_key, database_pool_max_size=14).database_pool_max_size == 14


def test_bulkheads_at_their_maximum_cannot_take_every_request_thread() -> None:
    """External work alone would take more threads than the server has (16 + 16 + 16 + 9 = 57 of 40)."""
    with pytest.raises(ConfigError) as excinfo:
        make_settings(
            llm_max_concurrent_checks=16,
            billing_max_concurrent_stripe_calls=16,
            clerk_max_concurrent_reads=16,
            database_worker_pool_max_size=10,
        )
    message = str(excinfo.value)
    assert f"leave 0 of the {REQUEST_THREADS} request threads" in message
    assert f"lower them by {MIN_WAITER_PLACES + 5 + 57 - REQUEST_THREADS} in total" in message
