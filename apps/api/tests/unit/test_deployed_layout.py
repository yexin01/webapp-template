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

API_DIR = Path(__file__).resolve().parents[2]

PROBE = """
import app, index
from app.kernel.infrastructure import config
assert app.__file__.startswith(TASK), app.__file__
assert config.REPO_ROOT is None, config.REPO_ROOT
try:
    from app.kernel.infrastructure import migrations
except RuntimeError as exc:
    assert "repository checkout" in str(exc), exc
else:
    raise AssertionError("migrations must refuse to load without a repository")
print("ok")
"""


def bundle(tmp_path: Path) -> Path:
    task = tmp_path / "var" / "task"
    shutil.copytree(API_DIR / "app", task / "app", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(API_DIR / "index.py", task / "index.py")
    return task


def test_api_imports_from_a_bundle_without_the_repository(tmp_path: Path) -> None:
    task = bundle(tmp_path)
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


def test_repository_checkout_still_finds_its_root() -> None:
    from app.kernel.infrastructure.config import ENV_FILE, REPO_ROOT

    assert REPO_ROOT is not None
    assert (REPO_ROOT / "supabase" / "migrations").is_dir()
    assert ENV_FILE == REPO_ROOT / ".env"
