"""Ordered, checksummed SQL migrations and production-targeting guards (MIG-01, ENV-05).

Applied migrations are recorded with a checksum; editing a migration after it
has been applied is detected and refused, and so is a new migration numbered
before one already applied. Concurrent runs are serialized per migration with
a transaction-level advisory lock, which also works through a transaction-mode
pooler, as does everything else here: no server-side prepared statements, and
only transaction-local settings.

A migration statement that waits for a table lock (held by running traffic)
gives up after ``_LOCK_TIMEOUT`` instead of queueing every later query on that
table behind it. Waiting for a concurrent run's advisory lock is not limited.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app.kernel.infrastructure.config import REPO_ROOT

APP_LOGIN_ROLE = "app_login"
LOGIN_PASSWORD_MIN_LENGTH = 16
# Supabase's shared pooler routes by "<role>.<project ref>", so the runtime login needs the same suffix.
_POOLER_HOST_SUFFIX = ".pooler.supabase.com"
# The pooler's transaction mode, which the API needs. Port 5432 there is the session mode (for migrations): it pins
# a server connection to each client and runs out of them under concurrency.
TRANSACTION_POOLER_PORT = 6543

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "host.docker.internal"})
_LOCAL_ADDRESSES = frozenset({"127.0.0.1", "::1"})
# Directories where a local PostgreSQL server puts its Unix socket. Any other
# socket directory may belong to a proxy for a remote server (the Cloud SQL
# Auth Proxy uses /cloudsql/<instance>), so it does not count as local.
LOCAL_SOCKET_DIRECTORIES = frozenset(
    {"/tmp", "/private/tmp", "/var/run/postgresql", "/run/postgresql"}  # noqa: S108 - socket directories, not temp files
)

# The ledger lives in ``public`` so it exists before the first migration. On
# Supabase, ``public`` is exposed through the Data API, and new tables there
# default to full grants for anon, authenticated, and service_role. Row-level
# security (with no policies) keeps the first two out, but service_role
# bypasses it, so only revoking the grants keeps the ledger private.
_LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS public.app_schema_migrations (
  version    text PRIMARY KEY,
  checksum   text NOT NULL,
  applied_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE public.app_schema_migrations ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.app_schema_migrations FROM PUBLIC;
DO $$
DECLARE r text;
BEGIN
  FOREACH r IN ARRAY ARRAY['anon', 'authenticated', 'service_role'] LOOP
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format('REVOKE ALL ON public.app_schema_migrations FROM %I', r);
    END IF;
  END LOOP;
END $$;
"""
_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('app_schema_migrations', 0))"
_LOCK_TIMEOUT = "5s"
_CONNECT_TIMEOUT_SECONDS = 10


class MigrationError(RuntimeError):
    pass


class UnsafeTargetError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Migration:
    version: str
    path: Path
    checksum: str


def is_local_database(url: str) -> bool:
    """True only if every host libpq could connect to is this machine.

    The URL's host is not enough: ``host``/``hostaddr`` query parameters and the
    ``PGHOST``/``PGHOSTADDR``/``PGSERVICE`` environment variables override it,
    and a service file can name any server. A Unix socket counts only in one of
    ``LOCAL_SOCKET_DIRECTORIES``. A tunnel to a remote server on a local port
    still looks local, which is why destructive commands also need an explicit
    ``APP_ENV`` and a confirmation flag (ENV-05).
    """
    try:
        params = conninfo_to_dict(url)
    except psycopg.Error:
        return False
    if params.get("service") or os.environ.get("PGSERVICE"):
        return False
    hosts = str(params.get("host") or os.environ.get("PGHOST") or "localhost")
    addresses = str(params.get("hostaddr") or os.environ.get("PGHOSTADDR") or "")
    return all(h in LOCAL_HOSTS or _is_local_socket(h) for h in hosts.split(",")) and all(
        a in _LOCAL_ADDRESSES for a in addresses.split(",") if a
    )


def _is_local_socket(host: str) -> bool:
    return host.startswith("/") and os.path.normpath(host) in LOCAL_SOCKET_DIRECTORIES


def with_tls_default(url: str) -> str:
    """``url`` with ``sslmode=require`` added when it names a non-local server and nothing says anything about TLS.

    libpq's default, ``prefer``, goes on without encryption when the server (or someone in between) says it has
    none, and then sends the password. Anything stated explicitly is kept: the URL's ``sslmode`` (``verify-full``
    with ``sslrootcert`` is the setting that also checks who answers), ``requiressl``, ``sslrootcert=system``, the
    ``PGSSLMODE`` and ``PGREQUIRESSL`` variables, and a ``service`` entry, whose file this cannot read.
    """
    try:
        params = conninfo_to_dict(url)
    except psycopg.Error:
        return url
    stated = (
        "sslmode" in params
        or "requiressl" in params
        or "service" in params
        or params.get("sslrootcert") == "system"
        or any(os.environ.get(name) for name in ("PGSSLMODE", "PGREQUIRESSL", "PGSERVICE"))
    )
    if stated or is_local_database(url):
        return url
    return make_conninfo(url, sslmode="require")


def _connect(url: str, *, autocommit: bool = False) -> psycopg.Connection:
    # Transaction-mode poolers do not support server-side prepared statements.
    return psycopg.connect(
        with_tls_default(url), autocommit=autocommit, prepare_threshold=None, connect_timeout=_CONNECT_TIMEOUT_SECONDS
    )


def require_local_database(url: str, operation: str) -> None:
    """Refuse destructive or development-only operations on non-local databases."""
    if not is_local_database(url):
        raise UnsafeTargetError(
            f"Refusing to {operation}: the database host is not local. "
            "This operation is only permitted against a local development or test database."
        )


def repository_file(*parts: str) -> Path:
    """A path in the repository checkout. Migrations ship in supabase/, which a deployed bundle does not contain."""
    if REPO_ROOT is None:
        raise RuntimeError("Migrations run from a repository checkout, not from a deployed bundle.")
    return REPO_ROOT.joinpath(*parts)


def discover(directory: Path | None = None) -> list[Migration]:
    migrations: list[Migration] = []
    for path in sorted((directory or repository_file("supabase", "migrations")).glob("*.sql")):
        version = path.name.split("_", 1)[0]
        if not version.isdigit():
            raise MigrationError(f"Migration file name must start with a numeric version: {path.name}")
        checksum = hashlib.sha256(path.read_bytes()).hexdigest()
        migrations.append(Migration(version, path, checksum))
    versions = [m.version for m in migrations]
    if len(versions) != len(set(versions)):
        raise MigrationError("Duplicate migration versions found.")
    return migrations


def migrate(url: str, directory: Path | None = None) -> list[str]:
    """Apply pending migrations in order, each in its own transaction."""
    applied_now: list[str] = []
    with _connect(url, autocommit=True) as conn:
        with conn.transaction():
            conn.execute(_LOCK)
            conn.execute(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")
            conn.execute(_LEDGER_DDL)
        for migration in discover(directory):
            with conn.transaction():
                # Serializes concurrent runs; the ledger is re-read under the lock.
                conn.execute(_LOCK)
                rows = conn.execute("SELECT version, checksum FROM public.app_schema_migrations").fetchall()
                applied: dict[str, str] = {str(version): str(checksum) for version, checksum in rows}
                known = applied.get(migration.version)
                if known is not None:
                    if known != migration.checksum:
                        raise MigrationError(
                            f"Migration {migration.path.name} was modified after being applied. "
                            "Create a new migration instead of editing an applied one."
                        )
                    continue
                latest = max(applied, key=int, default=None)
                if latest is not None and int(migration.version) < int(latest):
                    raise MigrationError(
                        f"Migration {migration.path.name} is numbered before {latest}, which is already applied. "
                        "Give it a version after the latest applied migration."
                    )
                conn.execute(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")  # after the advisory lock
                conn.execute(migration.path.read_text(encoding="utf-8"))
                conn.execute(
                    "INSERT INTO public.app_schema_migrations (version, checksum) VALUES (%s, %s)",
                    (migration.version, migration.checksum),
                )
            applied_now.append(migration.path.name)
    return applied_now


def seed(url: str, seed_file: Path | None = None) -> None:
    require_local_database(url, "seed synthetic data")
    seed_file = seed_file or repository_file("supabase", "seed.sql")
    # Runs as the local connecting role: seeding inserts rows into every
    # table, which neither runtime role is permitted to do.
    with _connect(url) as conn, conn.transaction():
        conn.execute(seed_file.read_text(encoding="utf-8"))


def reset(url: str) -> None:
    """Drop all application objects. Local databases only."""
    require_local_database(url, "reset the database")
    with _connect(url, autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS app CASCADE")
        conn.execute("DROP TABLE IF EXISTS public.app_schema_migrations")


def enable_api_login(url: str, password: str, role: str = APP_LOGIN_ROLE) -> None:
    """Let ``role`` log in with ``password``, which only a SCRAM-SHA-256 verifier computed here leaves this process.

    The role comes from the migrations, without LOGIN or a password. It is refused here if it has an attribute
    that would let a query skip row-level security, as the migrations refuse it. Run it again to rotate the password.
    """
    if len(password) < LOGIN_PASSWORD_MIN_LENGTH:
        raise MigrationError(f"The password for {role} must be at least {LOGIN_PASSWORD_MIN_LENGTH} characters.")
    with _connect(url) as conn:
        row = conn.execute(
            "SELECT rolsuper, rolbypassrls, rolcreaterole, rolreplication, rolinherit FROM pg_roles WHERE rolname = %s",
            (role,),
        ).fetchone()
        if row is None:
            raise MigrationError(f"The role {role} does not exist. Apply the migrations first: pnpm db:migrate.")
        unsafe = [
            name
            for name, present in zip(
                ("SUPERUSER", "BYPASSRLS", "CREATEROLE", "REPLICATION", "INHERIT"), row, strict=True
            )
            if present
        ]
        if unsafe:
            raise MigrationError(
                f"Refusing to enable {role}: it has {', '.join(unsafe)}. Run the migrations to see the fix."
            )
        verifier = conn.pgconn.encrypt_password(password.encode(), role.encode(), b"scram-sha-256").decode()
        conn.execute(sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(role), sql.Literal(verifier)))


def is_supabase_pooler(url: str) -> bool:
    """True if ``url`` names Supabase's shared pooler (``*.pooler.supabase.com``)."""
    return str(conninfo_to_dict(url).get("host") or "").endswith(_POOLER_HOST_SUFFIX)


def runtime_url_template(url: str, role: str = APP_LOGIN_ROLE) -> str | None:
    """Where the runtime ``DATABASE_URL`` goes: ``url``'s server and database, ``role``, and no password.

    On Supabase's shared pooler the port is the transaction mode's, whatever port ``url`` used: the owner
    connects to the session mode, and a runtime URL copied from it would pin a server connection per client.
    """
    params = conninfo_to_dict(url)
    host = str(params.get("host") or "")
    if not host or "," in host or host.startswith("/"):
        return None
    pooled = host.endswith(_POOLER_HOST_SUFFIX)
    ref = str(params.get("user") or "").partition(".")[2] if pooled else ""
    number = TRANSACTION_POOLER_PORT if pooled else params.get("port")
    port = f":{number}" if number else ""
    return f"postgresql://{role}{'.' + ref if ref else ''}:<password>@{host}{port}/{params.get('dbname') or ''}"
