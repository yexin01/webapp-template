"""``--require-db`` (``pnpm test``, ``pnpm verify``): an unreachable PostgreSQL ends the run, once, with the
command that fixes it, instead of letting about half of the API tests skip and the run pass."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.conftest import _database_required

API_DIR = Path(__file__).resolve().parents[2]

# Nothing listens on port 1. The password is a canary: it must never reach the output.
PROBE_CONFTEST = """
import tests.conftest as shared
from tests.conftest import database_url, db, pytest_addoption  # noqa: F401

shared.DEFAULT_TEST_DATABASE_URL = "postgresql://postgres:canary-password@127.0.0.1:1/app"
"""
PROBE_TEST = """
def test_needs_the_database(db):
    raise AssertionError("the test body must not run")
"""


def run_probe(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    (tmp_path / "conftest.py").write_text(PROBE_CONFTEST)
    (tmp_path / "test_probe.py").write_text(PROBE_TEST)
    env = {key: value for key, value in os.environ.items() if key not in ("CI", "TEST_DATABASE_URL")}
    return subprocess.run(  # noqa: S603 - fixed arguments, our own interpreter
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "-c",
            str(API_DIR / "pyproject.toml"),
            *args,
            str(tmp_path / "test_probe.py"),
        ],
        cwd=API_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


def test_without_the_option_an_unreachable_database_skips_its_tests_with_the_command_to_start_it(
    tmp_path: Path,
) -> None:
    result = run_probe(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 skipped" in result.stdout
    assert "start it with `pnpm db:up`" in result.stdout


def test_with_the_option_it_ends_the_run_once_and_says_how_to_start_it(tmp_path: Path) -> None:
    result = run_probe(tmp_path, "--require-db")
    output = result.stdout + result.stderr
    assert result.returncode == 2, output
    assert "PostgreSQL is not reachable for tests at 127.0.0.1:1" in output
    assert "pnpm db:up" in output
    assert "TEST_DATABASE_URL" in output
    assert "must not run" not in output
    assert "canary-password" not in output


@pytest.mark.parametrize(
    ("ci", "url", "option", "required"),
    [
        (None, None, False, False),
        (None, None, True, True),
        (None, "postgresql://postgres:x@127.0.0.1:5432/app", False, True),
        ("1", None, False, True),
        ("true", None, False, True),
        ("0", None, False, False),
        ("false", None, False, False),
        ("", None, False, False),
    ],
)
def test_the_database_is_required_in_ci_with_a_configured_url_or_with_the_option(
    monkeypatch: pytest.MonkeyPatch, ci: str | None, url: str | None, option: bool, required: bool
) -> None:
    for name, value in (("CI", ci), ("TEST_DATABASE_URL", url)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    config = SimpleNamespace(getoption=lambda name: option if name == "--require-db" else None)
    assert _database_required(config) is required  # type: ignore[arg-type]
