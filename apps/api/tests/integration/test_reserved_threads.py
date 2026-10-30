"""Threads reserved for bounded external work stay out of the connection-waiter budget (ADR-0003)."""

from __future__ import annotations

import base64
import json
import threading
import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import anyio
import jwt
import pytest
from anyio.abc import TaskGroup
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from app.kernel.errors import AuthenticationError, DependencyUnavailableError
from app.kernel.http.request_scope import request_transaction
from app.kernel.infrastructure.db import API_ROLE, WORKER_ROLE, Database
from app.modules.identity.adapters import clerk
from app.modules.identity.adapters.clerk import ClerkTokenVerifier, JwksKeySet
from app.settings import reserved_request_threads
from tests.conftest import make_settings
from tests.integration.test_request_admission import insert, unscoped_call, users, waiting

pytestmark = pytest.mark.db

CONNECT_TIMEOUT = 3.0
BURST = 3  # calls on each pool, more than there are waiter places
ISSUER = "https://example-app.clerk.accounts.dev"
PUBLISHABLE = "pk_test_" + base64.b64encode(b"example-app.clerk.accounts.dev$").decode()
ORIGIN = "http://localhost:3000"

Occupy = Callable[[TaskGroup], Awaitable[None]]


def make_database(url: str, *, api: int, worker: int, threads: int, reserved: int) -> Database:
    return Database(
        url,
        api_pool_size=api,
        worker_pool_size=worker,
        connect_timeout=CONNECT_TIMEOUT,
        statement_timeout_ms=5000,
        request_threads=threads,
        reserved_threads=reserved,
    )


def next_hop_seconds(
    database: Database, threads: int, occupy: Occupy, answer: Callable[[], None]
) -> tuple[float, list[str]]:
    """How long a scoped request's next hop waits for a thread, and the calls refused at once.

    The scoped request holds the only API connection between two hops. Meanwhile ``occupy`` takes
    the threads that bounded external work may hold, every worker connection is taken, and a burst
    on both pools fills the waiter budget. ``answer`` lets the external work finish.
    """
    scope = asynccontextmanager(request_transaction(database))
    workers = database._pool(WORKER_ROLE)
    busy = [workers.getconn() for _ in range(workers.max_size)]  # e.g. webhook synchronizations
    refused: list[str] = []

    async def main() -> float:
        limiter = anyio.to_thread.current_default_thread_limiter()
        limiter.total_tokens = threads  # as the app's lifespan does
        between_hops, next_hop = anyio.Event(), anyio.Event()
        hop: list[float] = []

        async def scoped() -> None:
            try:
                async with scope():
                    await anyio.to_thread.run_sync(insert, database, "user_scoped_1")  # the only API connection
                    between_hops.set()
                    await next_hop.wait()
                    started = time.monotonic()
                    await anyio.to_thread.run_sync(insert, database, "user_scoped_2")  # needs a thread again
                    hop.append(time.monotonic() - started)
            finally:
                for conn in busy:
                    workers.putconn(conn)
                answer()

        def settled() -> bool:
            queued = waiting(database, API_ROLE) + waiting(database, WORKER_ROLE)
            return queued + len(refused) + limiter.statistics().tasks_waiting == 2 * BURST

        async with anyio.create_task_group() as tg:
            tg.start_soon(scoped)
            await between_hops.wait()
            await occupy(tg)
            for role in (API_ROLE, WORKER_ROLE):
                for _ in range(BURST):
                    tg.start_soon(anyio.to_thread.run_sync, unscoped_call, database, role, refused)
            with anyio.fail_after(5):  # every call queued for a connection, refused, or waiting for a thread
                while not settled():
                    await anyio.sleep(0.01)
            next_hop.set()
        return hop[0]

    return anyio.run(main), refused


def test_a_scoped_request_finds_a_thread_while_external_work_and_every_waiter_hold_the_rest(db: str) -> None:
    """Every external slot is busy (LLM checks, Stripe calls, Clerk reads, synchronization: threads
    held until a provider answers), every connection is taken, and a burst on both pools fills the
    waiter budget. Scoped holders + waiters + reserved threads then use every request thread but
    the one the scoped request needs for its next hop, so it finishes at once instead of waiting
    for the waiters to time out."""
    threads, reserved = 6, 2
    database = make_database(db, api=1, worker=1, threads=threads, reserved=reserved)
    assert database.waiters_limit == threads - 1 - reserved
    providers_answer = threading.Event()
    external: list[None] = []

    def external_call() -> None:
        external.append(None)
        providers_answer.wait(10)

    async def occupy(tg: TaskGroup) -> None:
        for _ in range(reserved):
            tg.start_soon(anyio.to_thread.run_sync, external_call)
        with anyio.fail_after(5):
            while len(external) < reserved:
                await anyio.sleep(0.01)

    try:
        elapsed, refused = next_hop_seconds(database, threads, occupy, providers_answer.set)
        assert elapsed < CONNECT_TIMEOUT / 2, f"the scoped request waited {elapsed:.1f} s for a thread"
        assert users(db, "user_scoped_") == 2
        assert len(refused) == 2 * BURST - database.waiters_limit  # the waiters got connections afterwards
    finally:
        providers_answer.set()
        database.close()


def test_clerk_key_set_lookups_are_among_the_reserved_threads(db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """With CLERK_JWT_KEY empty, a token naming a key ID the cached key set lacks (Clerk may have
    rotated its keys, and anyone can send one) fetches the key set, or waits for the fetch in flight,
    on its request thread until Clerk answers. The key set holds every thread it may, every other
    bulkhead is busy, and a burst fills the waiter budget the app's settings leave: the scoped
    request still finds a thread for its next hop at once."""
    monkeypatch.setattr(clerk, "_JWKS_TIMEOUT_SECONDS", 30.0)  # the waiters stay parked until Clerk answers
    settings = make_settings(
        database_pool_max_size=1,
        database_worker_pool_max_size=2,
        llm_max_concurrent_checks=1,
        billing_max_concurrent_stripe_calls=1,
        clerk_max_concurrent_reads=3,
        clerk_publishable_key=PUBLISHABLE,
        clerk_authorized_parties=ORIGIN,
    )
    # LLM checks, checkout and portal Stripe calls, Clerk membership reads, and synchronizations.
    providers = (
        settings.llm_max_concurrent_checks
        + settings.billing_max_concurrent_stripe_calls
        + settings.clerk_max_concurrent_reads
        + settings.database_worker_pool_max_size
        - 1
    )
    key_set_threads = 1 + clerk._JWKS_MAX_WAITERS  # the fetch, and the requests that may wait for it
    refused_lookups = 2
    threads = settings.database_pool_max_size + providers + key_set_threads + 3  # three waiter places
    database = make_database(
        db,
        api=settings.database_pool_max_size,
        worker=settings.database_worker_pool_max_size,
        threads=threads,
        reserved=reserved_request_threads(settings),
    )
    answered = threading.Event()
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    fetches: list[None] = []
    verifier = ClerkTokenVerifier(
        issuer=ISSUER, authorized_parties=[ORIGIN], key_set=slow_key_set(private, answered, fetches)
    )
    external: list[None] = []
    outcomes: list[str] = []

    def external_call() -> None:
        external.append(None)
        answered.wait(10)

    def authenticate(n: int) -> None:  # as the synchronous ``authenticate`` dependency runs, on a request thread
        try:
            verifier.verify(signed_token(private, kid=f"made-up-{n}"))
            outcomes.append("verified")
        except (AuthenticationError, DependencyUnavailableError) as exc:
            outcomes.append(type(exc).__name__)

    async def occupy(tg: TaskGroup) -> None:
        limiter = anyio.to_thread.current_default_thread_limiter()
        for _ in range(providers):
            tg.start_soon(anyio.to_thread.run_sync, external_call)
        tg.start_soon(anyio.to_thread.run_sync, authenticate, 0)
        with anyio.fail_after(5):
            while len(fetches) < 2:  # the second fetch, in flight until Clerk answers
                await anyio.sleep(0.01)
        for n in range(1, key_set_threads + refused_lookups):
            tg.start_soon(anyio.to_thread.run_sync, authenticate, n)
        held = providers + key_set_threads
        give_up = time.monotonic() + 5
        while len(external) < providers or len(outcomes) < refused_lookups or limiter.borrowed_tokens != held:
            assert time.monotonic() < give_up, f"{limiter.borrowed_tokens} threads held, not {held}"
            await anyio.sleep(0.01)

    try:
        elapsed, refused = next_hop_seconds(database, threads, occupy, answered.set)
        assert elapsed < CONNECT_TIMEOUT / 2, f"the scoped request waited {elapsed:.1f} s for a thread"
        assert users(db, "user_scoped_") == 2
        assert len(refused) == 2 * BURST - database.waiters_limit
        # Clerk answered without the made-up keys: the fetch and its waiters are signed out, the rest got 503 at once.
        signed_out = ["AuthenticationError"] * key_set_threads
        assert sorted(outcomes) == signed_out + ["DependencyUnavailableError"] * refused_lookups
    finally:
        answered.set()
        database.close()


def slow_key_set(private: rsa.RSAPrivateKey, answered: threading.Event, fetches: list[None]) -> JwksKeySet:
    """A usable cached key set, whose next fetch waits until Clerk answers."""
    jwk = {**json.loads(RSAAlgorithm.to_jwk(private.public_key())), "kid": "kid1", "use": "sig", "alg": "RS256"}
    now = [0.0]

    def fetch() -> object:
        fetches.append(None)
        if len(fetches) > 1:
            answered.wait(10)
        return {"keys": [jwk]}

    key_set = JwksKeySet(f"{ISSUER}/.well-known/jwks.json", fetch=fetch, clock=lambda: now[0])
    key_set.signing_key("kid1")  # the first fetch caches the key set
    now[0] += clerk._JWKS_MIN_REFRESH_INTERVAL_SECONDS  # a token with an unknown key ID may fetch again
    return key_set


def signed_token(private: rsa.RSAPrivateKey, *, kid: str) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {"sub": "user_1", "iss": ISSUER, "azp": ORIGIN, "iat": now, "exp": now + 60}
    return jwt.encode(claims, private, algorithm="RS256", headers={"kid": kid})
