"""Ordered, checksummed SQL migrations and production-targeting guards (MIG-01, ENV-05).

Applied migrations are recorded with a checksum; editing a migration after it
has been applied is detected and refused.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import psycopg

from app.infrastructure.config import REPO_ROOT

MIGRATIONS_DIR = REPO_ROOT / "supabase" / "migrations"
SEED_FILE = REPO_ROOT / "supabase" / "seed.sql"
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "host.docker.internal"})

_LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS public.app_schema_migrations (
  version    text PRIMARY KEY,
  checksum   text NOT NULL,
  applied_at timestamptz NOT NULL DEFAULT now()
)
"""


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
    return (urlparse(url).hostname or "") in LOCAL_HOSTS


def require_local_database(url: str, operation: str) -> None:
    """Refuse destructive or development-only operations on non-local databases."""
    if not is_local_database(url):
        raise UnsafeTargetError(
            f"Refusing to {operation}: the database host is not local. "
            "This operation is only permitted against a local development or test database."
        )


def discover(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    migrations: list[Migration] = []
    for path in sorted(directory.glob("*.sql")):
        version = path.name.split("_", 1)[0]
        if not version.isdigit():
            raise MigrationError(f"Migration file name must start with a numeric version: {path.name}")
        checksum = hashlib.sha256(path.read_bytes()).hexdigest()
        migrations.append(Migration(version, path, checksum))
    versions = [m.version for m in migrations]
    if len(versions) != len(set(versions)):
        raise MigrationError("Duplicate migration versions found.")
    return migrations


def migrate(url: str, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply pending migrations in order, each in its own transaction."""
    applied_now: list[str] = []
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(_LEDGER_DDL)
        applied = {
            row[0]: row[1]
            for row in conn.execute("SELECT version, checksum FROM public.app_schema_migrations").fetchall()
        }
        for migration in discover(directory):
            known = applied.get(migration.version)
            if known is not None:
                if known != migration.checksum:
                    raise MigrationError(
                        f"Migration {migration.path.name} was modified after being applied. "
                        "Create a new migration instead of editing an applied one."
                    )
                continue
            sql = migration.path.read_text(encoding="utf-8")
            with conn.transaction():
                conn.execute(sql)
                conn.execute(
                    "INSERT INTO public.app_schema_migrations (version, checksum) VALUES (%s, %s)",
                    (migration.version, migration.checksum),
                )
            applied_now.append(migration.path.name)
    return applied_now


def seed(url: str, seed_file: Path = SEED_FILE) -> None:
    require_local_database(url, "seed synthetic data")
    # Runs as the local connecting role: seeding inserts rows into every
    # table, which neither runtime role is permitted to do.
    with psycopg.connect(url) as conn, conn.transaction():
        conn.execute(seed_file.read_text(encoding="utf-8"))


def reset(url: str) -> None:
    """Drop all application objects. Local databases only."""
    require_local_database(url, "reset the database")
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS app CASCADE")
        conn.execute("DROP TABLE IF EXISTS public.app_schema_migrations")
