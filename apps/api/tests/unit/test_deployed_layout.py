"""The API starts from a deployed bundle, which holds only the API package (DEC-001).

`vercel build` packages ``index.py`` and ``app/`` at the function root
(``/var/task``); the repository (``supabase/``, ``.env``) is not there. The
DEC-001 local proof found that an import-time layout guard made every cold
start fail in that layout.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

API_DIR = Path(__file__).resolve().parents[2]

PROBE = """
import app, index
import app.cli  # maintenance commands such as `job` must load in a bundle
from app.kernel.infrastructure import config, migrations
assert app.__file__.startswith(TASK), app.__file__
assert config.REPO_ROOT is None, config.REPO_ROOT
assert config.ENV_FILE is None, config.ENV_FILE
from app.settings import load_settings
assert "dotenv-was-read" not in load_settings().app_base_url, "a .env file next to the bundle was read"
try:
    migrations.discover()
except RuntimeError as exc:
    assert "repository checkout" in str(exc), exc
else:
    raise AssertionError("migrations must refuse to run without a repository")
print("ok")
"""


@pytest.mark.parametrize(
    "layout",
    [
        "var/task",  # the function root `vercel build` produces
        "srv/apps/api",  # an API copied without the rest of the repository
    ],
)
def test_api_imports_from_a_bundle_without_the_repository(tmp_path: Path, layout: str) -> None:
    task = tmp_path / layout
    shutil.copytree(API_DIR / "app", task / "app", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(API_DIR / "index.py", task / "index.py")
    # Configuration comes from the environment only: a stray .env anywhere above is ignored.
    for directory in (task, *task.parents[: len(task.relative_to(tmp_path).parts)]):
        (directory / ".env").write_text("APP_BASE_URL=http://dotenv-was-read.invalid\n", encoding="utf-8")
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONDONTWRITEBYTECODE": "1",
        "APP_ENV": "test",
        "DATABASE_URL": "postgresql://app:unused@127.0.0.1:1/unused",  # never connected
    }
    result = subprocess.run(  # noqa: S603 (fixed interpreter and script)
        [sys.executable, "-c", f"TASK = {str(task)!r}\n{PROBE}"],
        cwd=task,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-3000:]
    assert result.stdout.strip().endswith("ok")


def test_a_bundle_refuses_to_start_without_an_explicit_app_env(tmp_path: Path) -> None:
    """Without a .env, a missing APP_ENV must not fall back to `local` (mocks, open diagnostics)."""
    task = tmp_path / "var" / "task"
    shutil.copytree(API_DIR / "app", task / "app", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(API_DIR / "index.py", task / "index.py")
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONDONTWRITEBYTECODE": "1",
        "DATABASE_URL": "postgresql://app:unused@127.0.0.1:1/unused",
    }
    result = subprocess.run(
        [sys.executable, "-c", "import index"],
        cwd=task,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode != 0
    assert "APP_ENV is required outside a repository checkout" in result.stderr, result.stderr[-2000:]


def test_repository_checkout_still_finds_its_root() -> None:
    from app.kernel.infrastructure.config import ENV_FILE, REPO_ROOT

    assert REPO_ROOT is not None
    assert (REPO_ROOT / "pnpm-workspace.yaml").is_file()
    assert (REPO_ROOT / "supabase" / "migrations").is_dir()
    assert ENV_FILE == REPO_ROOT / ".env"
