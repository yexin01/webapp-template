"""Migrations and shell scripts keep LF line endings in a checkout on any platform (MIG-01).

The migration runner records a checksum of each file's bytes. A Windows checkout with ``core.autocrlf=true`` turns LF
into CRLF unless ``.gitattributes`` says otherwise, and the next run on Linux (CI, the migrate workflow) refuses every
applied migration as modified. These tests make such a checkout with the repository's own ``.gitattributes``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from app.kernel.infrastructure import migrations

GIT = shutil.which("git")
CONTENT = b"SELECT 1;\n"

pytestmark = pytest.mark.skipif(GIT is None, reason="git is not installed")


def git(cwd: Path, *args: str) -> None:
    subprocess.run(  # noqa: S603 - the installed git, with fixed arguments, in a temporary repository
        [str(GIT), "-c", f"core.hooksPath={os.devnull}", *args], cwd=cwd, check=True, capture_output=True
    )


def checked_out(tmp_path: Path, path: str) -> bytes:
    """What ``path`` holds after it is committed with LF and checked out again where ``core.autocrlf`` is true."""
    shutil.copy(migrations.repository_file(".gitattributes"), tmp_path / ".gitattributes")
    git(tmp_path, "init", "--quiet")
    for key, value in (("core.autocrlf", "true"), ("user.name", "test"), ("user.email", "test@example.invalid")):
        git(tmp_path, "config", key, value)
    git(tmp_path, "config", "commit.gpgsign", "false")
    file = tmp_path / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_bytes(CONTENT)
    git(tmp_path, "add", "--all")
    git(tmp_path, "commit", "--quiet", "--message", "probe")
    file.unlink()
    git(tmp_path, "checkout", "--", path)
    return file.read_bytes()


def test_a_checkout_that_converts_line_endings_would_change_a_file_without_a_rule(tmp_path: Path) -> None:
    """The control: with ``core.autocrlf=true`` this setup does turn LF into CRLF, so the cases below mean something."""
    assert checked_out(tmp_path, "notes.txt") == CONTENT.replace(b"\n", b"\r\n")


@pytest.mark.parametrize(
    "path",
    [
        "supabase/migrations/20990101000000_probe.sql",
        "supabase/seed.sql",
        "scripts/probe.sh",
        "scripts/git-hooks/pre-commit",
    ],
)
def test_a_checkout_that_converts_line_endings_leaves_these_files_on_lf(tmp_path: Path, path: str) -> None:
    assert checked_out(tmp_path, path) == CONTENT
