"""Migration lint (MIG-03): squawk runs over supabase/migrations with the repository's .squawk.toml.

The checksummed migrations applied before the lint existed are excluded by name. Everything after them,
including what ``pnpm new:module`` generates, must pass.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from app.kernel.infrastructure import migrations

SQUAWK = Path(sys.executable).parent / "squawk"
CONFIG = migrations.repository_file(".squawk.toml")
LINTED_FROM = 20261030000000  # the first migration written after the lint was added


def lint(*paths: Path | str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - the project's own dev dependency, with fixed arguments
        [str(SQUAWK), "--config", str(CONFIG), *map(str, paths)], capture_output=True, text=True, check=False
    )


@pytest.fixture
def planted(tmp_path: Path) -> Path:
    path = tmp_path / "20301231000000_planted.sql"
    path.write_text("ALTER TABLE app.users DROP COLUMN created_at;\n", encoding="utf-8")
    return path


def test_the_repository_migrations_pass_the_lint() -> None:
    result = lint(migrations.repository_file("supabase", "migrations", "*.sql"))
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_violating_migration_fails_the_lint(planted: Path) -> None:
    result = lint(planted)
    assert result.returncode != 0
    assert "ban-drop-column" in result.stdout + result.stderr


def test_a_reviewed_exception_passes_the_lint(planted: Path) -> None:
    planted.write_text(
        "-- squawk-ignore ban-drop-column\nALTER TABLE app.users DROP COLUMN created_at;\n", encoding="utf-8"
    )
    assert lint(planted).returncode == 0


def test_only_migrations_from_before_the_lint_are_excluded() -> None:
    """A new migration is never skipped by an exclusion pattern: the patterns name the old files exactly."""
    excluded = [line.split('"')[1] for line in CONFIG.read_text(encoding="utf-8").splitlines() if '.sql"' in line]
    older = [m.path.name for m in migrations.discover() if int(m.version) < LINTED_FROM]
    assert sorted(pattern.removeprefix("*") for pattern in excluded) == sorted(older)
