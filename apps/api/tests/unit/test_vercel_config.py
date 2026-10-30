"""The root vercel.json is the deployment baseline: one Vercel project with Services (DEC-001).

Vercel reads routing, crons, and per-service function settings from this one file. The CI job
`deploy-config` checks what `vercel build` makes of it; these tests check the file itself, fast.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
CONFIG_PATH = REPO_ROOT / "vercel.json"

# https://vercel.com/docs/regions (19 compute regions).
VERCEL_REGIONS = {
    "arn1", "bom1", "cdg1", "cle1", "cpt1", "dub1", "fra1", "gru1", "hkg1", "hnd1",
    "iad1", "icn1", "kix1", "lhr1", "pdx1", "sfo1", "sin1", "syd1", "yul1",
}  # fmt: skip
# With `services` present, Vercel rejects these at the top level: each belongs to the service that owns it.
BUILD_AND_RUNTIME_KEYS = {
    "functions", "framework", "buildCommand", "installCommand", "devCommand", "ignoreCommand", "outputDirectory",
}  # fmt: skip


@pytest.fixture(scope="module")
def config() -> dict[str, Any]:
    parsed: dict[str, Any] = json.loads(CONFIG_PATH.read_text())
    return parsed


def test_the_baseline_is_one_project_with_a_web_and_an_api_service(config: dict[str, Any]) -> None:
    services = config["services"]
    assert set(services) == {"web", "api"}
    assert services["web"]["root"] == "apps/web"
    assert services["api"]["root"] == "apps/api"
    for service in services.values():
        assert (REPO_ROOT / service["root"]).is_dir()
    assert (REPO_ROOT / "apps" / "api" / services["api"]["entrypoint"]).is_file()


def test_api_requests_reach_the_api_before_the_web_catch_all(config: dict[str, Any]) -> None:
    rewrites = [(r["source"], r["destination"]) for r in config["rewrites"]]
    assert rewrites == [
        ("/api/v1/(.*)", {"service": "api"}),
        ("/(.*)", {"service": "web"}),
    ]


def test_build_and_runtime_keys_live_in_the_services_not_at_the_top_level(config: dict[str, Any]) -> None:
    assert not BUILD_AND_RUNTIME_KEYS & set(config)


def test_only_the_root_file_configures_vercel(config: dict[str, Any]) -> None:
    """A service's own vercel.json would be a second place for crons that Vercel does not read (DEC-001 proof)."""
    found = [
        p.relative_to(REPO_ROOT).as_posix()
        for p in (REPO_ROOT / "apps").glob("*/vercel.json")
        if "node_modules" not in p.parts
    ]
    assert found == []


def test_the_api_function_runs_in_exactly_one_known_region(config: dict[str, Any]) -> None:
    """One region: the free plan allows one. It must match the database region (docs-notes/deploy.md)."""
    function = config["services"]["api"]["functions"]["index.py"]
    assert len(function["regions"]) == 1
    assert set(function["regions"]) <= VERCEL_REGIONS


def test_a_top_level_regions_key_is_not_used(config: dict[str, Any]) -> None:
    """With Services, `vercel build` leaves a top-level `regions` out of the service function's configuration."""
    assert "regions" not in config


def test_the_api_bundle_leaves_out_tests_and_tool_caches(config: dict[str, Any]) -> None:
    exclude = config["services"]["api"]["functions"]["index.py"]["excludeFiles"]
    for pattern in ("tests/**", ".venv/**", ".pytest_cache/**", ".mypy_cache/**", ".ruff_cache/**"):
        assert pattern in exclude
