"""Connections to a remote server use TLS unless the URL says otherwise (libpq's default, ``prefer``, goes on
in plaintext and sends the password when the server, or someone in between, says it has no TLS)."""

from __future__ import annotations

import socket
import struct
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
import pytest

from app import bootstrap
from app.kernel.errors import DependencyUnavailableError
from app.kernel.infrastructure import migrations
from app.kernel.infrastructure.db import Database
from tests.conftest import make_settings

SSL_REQUEST = struct.pack("!ii", 8, 80877103)
REMOTE = "postgresql://postgres.abcdefgh:p%40ss@aws-0-eu-west-1.pooler.supabase.com:6543/postgres"


@pytest.fixture(autouse=True)
def _no_connection_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("PGSSLMODE", "PGREQUIRESSL", "PGSERVICE", "PGHOST", "PGHOSTADDR"):
        monkeypatch.delenv(name, raising=False)


# -- TLS ---------------------------------------------------------------------------


def sslmode_of(conninfo: str) -> str | None:
    return psycopg.conninfo.conninfo_to_dict(conninfo).get("sslmode")


def test_a_remote_server_gets_sslmode_require_when_nothing_says_otherwise() -> None:
    assert sslmode_of(migrations.with_tls_default(REMOTE)) == "require"
    # The rest of the URL survives, including the percent-encoded password.
    params = psycopg.conninfo.conninfo_to_dict(migrations.with_tls_default(REMOTE))
    assert (params["user"], params["password"], params["host"], params["port"], params["dbname"]) == (
        "postgres.abcdefgh",
        "p@ss",
        "aws-0-eu-west-1.pooler.supabase.com",
        "6543",
        "postgres",
    )


@pytest.mark.parametrize("mode", ["disable", "allow", "prefer", "require", "verify-ca", "verify-full"])
def test_an_explicit_sslmode_is_never_overridden(mode: str) -> None:
    url = f"{REMOTE}?sslmode={mode}"
    assert migrations.with_tls_default(url) == url


@pytest.mark.parametrize(
    "url",
    [
        f"{REMOTE}?requiressl=0",
        f"{REMOTE}?sslrootcert=system",
        f"{REMOTE}?service=production",
        "postgresql://u:p@localhost/app",
        "postgresql://u:p@127.0.0.1:54329/app",
        "postgresql:///app?host=/var/run/postgresql",
        "not a connection string",
    ],
)
def test_other_urls_are_left_alone(url: str) -> None:
    assert migrations.with_tls_default(url) == url


@pytest.mark.parametrize("name", ["PGSSLMODE", "PGREQUIRESSL", "PGSERVICE"])
def test_the_environment_is_respected(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    monkeypatch.setenv(name, "1" if name == "PGREQUIRESSL" else "verify-full")
    assert migrations.with_tls_default(REMOTE) == REMOTE


@contextmanager
def server_without_tls() -> Iterator[tuple[int, list[bytes]]]:
    """A server on this machine that answers a request for TLS with 'N' and records what the client sends next."""
    received: list[bytes] = []
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)

    def serve() -> None:
        while True:
            try:
                conn, _ = listener.accept()
            except OSError:
                return
            with conn:
                conn.settimeout(2)
                try:
                    if conn.recv(8) == SSL_REQUEST:
                        conn.sendall(b"N")
                        if data := conn.recv(4096):  # a plaintext startup message, if the client goes on
                            received.append(data)
                except OSError:
                    continue

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield listener.getsockname()[1], received
    finally:
        listener.close()
        thread.join(5)


def remote_url(port: int, query: str = "") -> str:
    # The name is remote, so TLS is the default; hostaddr sends the connection to the server above.
    return f"postgresql://app_login:a-password-123456@db.example.com:{port}/app?hostaddr=127.0.0.1{query}"


def test_the_runner_refuses_a_server_that_offers_no_tls() -> None:
    with server_without_tls() as (port, received):
        with pytest.raises(psycopg.OperationalError, match="SSL"):
            migrations.migrate(remote_url(port))
        assert received == []  # the client sent no startup message, so no password could be asked for


def test_the_pool_refuses_a_server_that_offers_no_tls() -> None:
    with server_without_tls() as (port, received):
        database = Database(
            migrations.with_tls_default(remote_url(port)),
            api_pool_size=1,
            worker_pool_size=2,
            connect_timeout=1,
            statement_timeout_ms=1000,
        )
        try:
            with pytest.raises(DependencyUnavailableError), database.transaction("app_api", {}):
                pass
        finally:
            database.close()
        assert received == []


def test_an_explicit_prefer_still_goes_on_without_tls() -> None:
    """The control: what the default prevents. An explicit setting is the adopter's to make."""
    with server_without_tls() as (port, received):
        with pytest.raises(psycopg.OperationalError):
            psycopg.connect(migrations.with_tls_default(remote_url(port, "&sslmode=prefer")), connect_timeout=2)
        deadline = time.monotonic() + 2
        while not received and time.monotonic() < deadline:
            time.sleep(0.01)
        assert received
        assert b"app_login" in received[0]


def test_the_composition_root_adds_the_default_to_the_pool() -> None:
    remote = bootstrap._database(make_settings(database_url=REMOTE))
    local = bootstrap._database(make_settings(database_url="postgresql://u:p@127.0.0.1:1/app"))
    try:
        assert sslmode_of(remote._conninfo) == "require"
        assert sslmode_of(local._conninfo) is None
    finally:
        remote.close()
        local.close()
