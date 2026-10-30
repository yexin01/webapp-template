"""The workflow files are part of the security model (CI-SEC-03, CI-SEC-04, MIG-05, SEC-05).

Actions are pinned to commits, pull request checks receive no secrets, the secret scan needs no license and
fails on a planted secret, and the migration workflow can only be started by a person and only touches the
environment that person chose.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[4]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
SHA_PIN = re.compile(r"^[\w.-]+/[\w.-]+(?:/[\w./-]+)?@[0-9a-f]{40}$")


def load(name: str) -> dict[str, Any]:
    parsed: dict[str, Any] = yaml.safe_load((WORKFLOWS / name).read_text())
    return parsed


def triggers(workflow: dict[str, Any]) -> dict[str, Any]:
    """YAML 1.1 reads the key `on` as the boolean True."""
    found = workflow.get("on", workflow.get(True))
    return found if isinstance(found, dict) else {}


def steps(workflow: dict[str, Any], job: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = workflow["jobs"][job]["steps"]
    return found


@pytest.mark.parametrize("path", sorted(WORKFLOWS.glob("*.yml")), ids=lambda p: p.name)
def test_every_action_is_pinned_to_a_commit(path: Path) -> None:
    workflow = yaml.safe_load(path.read_text())
    unpinned = [
        step["uses"]
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
        if "uses" in step and not SHA_PIN.match(step["uses"])
    ]
    assert unpinned == []


# -- CI (pull requests, including forks, receive no secrets) --------------------------------------------------


def test_ci_receives_no_secrets_and_never_runs_on_pull_request_target() -> None:
    text = (WORKFLOWS / "ci.yml").read_text()
    assert not re.search(r"\$\{\{\s*secrets\.", text)
    assert "GITHUB_TOKEN" not in text
    assert "pull_request_target" not in text
    assert load("ci.yml")["permissions"] == {"contents": "read"}


def test_deploy_config_runs_without_credentials_or_telemetry() -> None:
    job = load("ci.yml")["jobs"]["deploy-config"]
    assert job["env"]["VERCEL_TELEMETRY_DISABLED"] == "1"
    assert "permissions" not in job
    assert "VERCEL_TOKEN" not in json.dumps(job)
    commands = " ".join(step.get("run", "") for step in job["steps"])
    assert "pnpm test:vercel-build" in commands
    assert "--no-dev" in commands  # the production dependency set, as Vercel installs it


def test_the_vercel_cli_is_pinned_exactly() -> None:
    version = json.loads((REPO_ROOT / "package.json").read_text())["devDependencies"]["vercel"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", version), version


def test_the_secret_scan_is_the_pinned_licence_free_cli() -> None:
    ci = load("ci.yml")
    job = ci["jobs"]["secrets"]
    assert not [s for s in job["steps"] if "gitleaks-action" in s.get("uses", "")]
    assert re.fullmatch(r"\d+\.\d+\.\d+", job["env"]["GITLEAKS_VERSION"])
    assert re.fullmatch(r"[0-9a-f]{64}", job["env"]["GITLEAKS_SHA256"])
    install = next(s["run"] for s in job["steps"] if s.get("name", "").startswith("Install gitleaks"))
    assert "sha256sum --check --strict" in install
    names = [s.get("name", "") for s in job["steps"]]
    assert any("self-test" in n for n in names), "the scanner must be shown to flag a planted secret"
    scan = next(s["run"] for s in job["steps"] if s.get("name", "").startswith("Scan the full history"))
    assert "gitleaks git" in scan
    assert "--config .gitleaks.toml" in scan
    assert next(s for s in job["steps"] if "actions/checkout" in s.get("uses", ""))["with"]["fetch-depth"] == 0


def test_the_hook_pins_the_same_gitleaks_version_by_digest() -> None:
    hook = (REPO_ROOT / "scripts" / "git-hooks" / "pre-commit").read_text()
    pinned = re.search(r'GITLEAKS_IMAGE="[\w./-]+:v(\d+\.\d+\.\d+)@sha256:[0-9a-f]{64}"', hook)
    assert pinned, "the container fallback must be pinned by digest"
    assert pinned.group(1) == load("ci.yml")["jobs"]["secrets"]["env"]["GITLEAKS_VERSION"]
    assert "neither gitleaks nor Docker is installed" in hook


# -- the migration workflow -----------------------------------------------------------------------------------


def test_migrations_run_only_when_a_person_starts_the_workflow() -> None:
    workflow = load("db-migrate.yml")
    assert set(triggers(workflow)) == {"workflow_dispatch"}


def test_the_person_chooses_the_environment_and_the_job_uses_it() -> None:
    workflow = load("db-migrate.yml")
    choice = triggers(workflow)["workflow_dispatch"]["inputs"]["environment"]
    assert choice["type"] == "choice"
    assert choice["options"] == ["preview", "production"]
    job = workflow["jobs"]["migrate"]
    assert job["environment"] == "${{ inputs.environment }}"  # required reviewers gate it (an owner setting)
    assert job["if"] == "github.ref == format('refs/heads/{0}', github.event.repository.default_branch)"


def test_the_migration_job_has_least_privilege_and_one_secret() -> None:
    workflow = load("db-migrate.yml")
    assert workflow["permissions"] == {"contents": "read"}
    assert "permissions" not in workflow["jobs"]["migrate"]
    text = (WORKFLOWS / "db-migrate.yml").read_text()
    assert set(re.findall(r"\$\{\{\s*secrets\.(\w+)", text)) == {"MIGRATION_DATABASE_URL"}
    checkout = next(s for s in steps(workflow, "migrate") if "actions/checkout" in s.get("uses", ""))
    assert checkout["with"]["persist-credentials"] is False


def test_the_migration_job_applies_through_the_project_cli_and_never_echoes_the_secret() -> None:
    workflow = load("db-migrate.yml")
    run_steps = [s for s in steps(workflow, "migrate") if "run" in s]
    assert [s["run"] for s in run_steps if "db:migrate" in s["run"]] == ["pnpm db:migrate --allow-remote"]
    for step in run_steps:
        assert "${{" not in step["run"], "expressions go through env:, never into the script text"
        assert "set -x" not in step["run"]
        assert not re.search(r"(echo|printf|cat)[^\n]*\$\{?MIGRATION_DATABASE_URL", step["run"])
    concurrency = workflow["concurrency"]
    assert concurrency["cancel-in-progress"] is False
