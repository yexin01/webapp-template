#!/usr/bin/env python3
"""Checks what `vercel build` made of the root vercel.json (CI job `deploy-config`, `pnpm test:vercel-build`).

Local packaging evidence only: it says nothing about a deployment, the platform router, or the platform's own build
machines. It asserts that the build output declares both services, routes /api/v1/* to the API before the web
catch-all, schedules exactly the registered jobs, puts the API function in the configured region, and that the API
bundle is a Python 3.12 x86-64 Linux package without tests, within the documented size.

    python scripts/check_vercel_build.py check --root <export> --output <export>/.vercel/output [--api-dir <dir>]
    python scripts/check_vercel_build.py self-test      # fails on planted trees: a tests/ file, a Mach-O library, ...

Run it with an interpreter that has the API's runtime dependencies (the job registry is read from the API package).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
BUNDLE_LIMIT_MB = 500  # https://vercel.com/docs/functions/limitations (uncompressed Python bundle)
API_RUNTIME = "python3.12"
VERCEL_REGIONS = {
    "arn1", "bom1", "cdg1", "cle1", "cpt1", "dub1", "fra1", "gru1", "hkg1", "hnd1",
    "iad1", "icn1", "kix1", "lhr1", "pdx1", "sfo1", "sin1", "syd1", "yul1",
}  # fmt: skip
# Request paths and the service that must answer them.
ROUTING_CASES = {
    "/api/v1/health": "api",
    "/api/v1/jobs/drain-outbox": "api",
    "/": "web",
    "/dashboard": "web",
    "/api/other": "web",
}

_ELF_MACHINES = {3: "x86", 40: "arm", 62: "x86_64", 183: "aarch64"}
_MACHO_MAGICS = {
    b"\xfe\xed\xfa\xce",
    b"\xfe\xed\xfa\xcf",
    b"\xce\xfa\xed\xfe",
    b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe",
}


@dataclass
class Checks:
    items: list[dict[str, Any]] = field(default_factory=list)

    def add(self, name: str, ok: bool, **detail: Any) -> None:
        self.items.append({"name": name, "ok": bool(ok), **detail})

    @property
    def failed(self) -> list[dict[str, Any]]:
        return [c for c in self.items if not c["ok"]]


def header_platform(head: bytes) -> str:
    """Classify a native binary by its first 20 bytes: ``linux-x86_64``, ``elf-<arch>``, ``macos``, or ``unknown``."""
    if head[:4] == b"\x7fELF" and len(head) >= 20:
        machine = int.from_bytes(head[18:20], "little" if head[5] == 1 else "big")
        if machine == 62 and head[4] == 2:  # EM_X86_64, ELFCLASS64
            return "linux-x86_64"
        return f"elf-{_ELF_MACHINES.get(machine, f'machine-{machine}')}-{32 if head[4] == 1 else 64}bit"
    if head[:4] in _MACHO_MAGICS:
        return "macos"
    return "unknown"


def binary_platform(path: Path) -> str:
    with path.open("rb") as f:
        return header_platform(f.read(20))


def registered_jobs(api_dir: Path) -> dict[str, str]:
    """The API's job registry as Vercel Cron paths and schedules. Builds the app without contacting any service."""
    sys.path.insert(0, str(api_dir))
    from app.bootstrap import build_container
    from app.modules.billing.adapters.fakes import InMemoryBillingProvider
    from app.settings import load_settings

    settings = load_settings(
        _env_file=None,
        app_env="test",
        database_url="postgresql://unused:unused@127.0.0.1:1/unused",
        billing_mode="mock",
        stripe_price_pro_monthly="price_placeholder_monthly",
        stripe_price_pro_yearly="price_placeholder_yearly",
        llm_mode="mock",
    )
    container = build_container(settings, billing_provider=InMemoryBillingProvider())
    try:
        return {f"/api/v1/jobs/{job.name}": job.schedule for job in container.kernel.jobs.all()}
    finally:
        container.close()


def _first_service(routes: list[dict[str, Any]], path: str) -> str | None:
    """The service the first matching top-level route sends ``path`` to (the table is evaluated in order)."""
    for route in routes:
        destination = route.get("destination")
        if "src" in route and isinstance(destination, dict) and re.match(route["src"], path):
            return str(destination.get("service"))
    return None


def check(root: Path, output: Path, registered: dict[str, str]) -> Checks:
    checks = Checks()
    committed = json.loads((root / "vercel.json").read_text())
    config = json.loads((output / "config.json").read_text())

    services = {s.get("name"): s for s in config.get("services", [])}
    checks.add("both_services_declared", set(services) == {"web", "api"}, services=sorted(map(str, services)))
    api = services.get("api", {})
    checks.add("api_service_is_python", api.get("runtime") == "python" and api.get("entrypoint") == "index.py")

    routing = {path: _first_service(config.get("routes", []), path) for path in ROUTING_CASES}
    checks.add("routing_order", routing == ROUTING_CASES, got=routing, expected=ROUTING_CASES)

    scheduled = {c.get("path"): c.get("schedule") for c in config.get("crons", [])}
    committed_crons = {c["path"]: c["schedule"] for c in committed.get("crons", [])}
    checks.add(
        "crons_declared_as_committed",
        bool(scheduled) and len(scheduled) == len(config.get("crons", [])) and scheduled == committed_crons,
        built=sorted(map(str, scheduled)),
        committed=sorted(committed_crons),
    )
    checks.add(
        "crons_match_job_registry",
        bool(registered) and scheduled == registered,
        missing=sorted(registered.keys() - scheduled.keys()),
        unknown=sorted(map(str, scheduled.keys() - registered.keys())),
        schedule_differs=sorted(k for k in scheduled.keys() & registered.keys() if scheduled[k] != registered[k]),
    )

    funcs = [p for p in (output / "services" / "api" / "functions").glob("*.func") if p.is_dir()]
    checks.add("one_api_function", len(funcs) == 1, found=sorted(p.name for p in funcs))
    checks.add("web_service_built", (output / "services" / "web" / "config.json").is_file())
    if len(funcs) != 1:
        return checks
    vc = json.loads((funcs[0] / ".vc-config.json").read_text())
    mapping: dict[str, str] = vc.get("filePathMap", {})

    wanted = committed.get("services", {}).get("api", {}).get("functions", {}).get("index.py", {}).get("regions")
    got = vc.get("regions")
    checks.add(
        "api_function_region",
        isinstance(wanted, list) and len(wanted) == 1 and wanted == got and set(wanted) <= VERCEL_REGIONS,
        committed=wanted,
        built=got,
    )
    checks.add("api_runtime", vc.get("runtime") == API_RUNTIME, runtime=vc.get("runtime"), expected=API_RUNTIME)
    checks.add("api_architecture", vc.get("architecture") == "x86_64", architecture=vc.get("architecture"))

    unwanted = ("tests/", ".venv/", ".pytest_cache/", ".mypy_cache/", ".ruff_cache/")
    included = sorted(k for k in mapping if k.startswith(unwanted))
    checks.add("no_test_files_or_caches", not included, files=len(included), examples=included[:5])

    total = 0
    platforms: dict[str, str] = {}
    for dest, src in mapping.items():
        source = root / src
        total += source.stat().st_size
        if dest.endswith(".so") or ".so." in dest:
            platforms[dest] = binary_platform(source)
    for extra in funcs[0].iterdir():
        if extra.is_file() and extra.name != ".vc-config.json":
            total += extra.stat().st_size
    offending = sorted(k for k, v in platforms.items() if v != "linux-x86_64")
    checks.add(
        "native_libraries_linux_x86_64",
        not offending and not [k for k in mapping if "darwin" in k or "macosx" in k],
        libraries=len(platforms),
        offending=offending[:20],
    )
    checks.add(
        "within_bundle_limit",
        total < BUNDLE_LIMIT_MB * 1_000_000,
        uncompressed_mib=round(total / 1_048_576, 1),
        limit_mb=BUNDLE_LIMIT_MB,
    )
    return checks


# -- self-test ----------------------------------------------------------------

_ELF_X86_64 = b"\x7fELF\x02\x01\x01" + bytes(11) + (62).to_bytes(2, "little") + bytes(4)
_MACHO_ARM64 = b"\xcf\xfa\xed\xfe" + bytes(16)
_CRONS = {"/api/v1/jobs/a": "0 1 * * *", "/api/v1/jobs/b": "0 2 * * *"}


def _write(path: Path, content: bytes | str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content if isinstance(content, bytes) else content.encode())


def _plant(tmp: Path, mutation: str | None) -> tuple[Path, Path]:
    """A minimal export and build output; ``mutation`` plants one defect."""
    root, output = tmp / "root", tmp / "root" / ".vercel" / "output"
    crons = dict(_CRONS)
    if mutation == "missing_cron":
        crons.pop("/api/v1/jobs/b")
    if mutation == "extra_cron":
        crons["/api/v1/jobs/c"] = "0 3 * * *"
    region = "fra1" if mutation == "wrong_region" else "iad1"
    _write(
        root / "vercel.json",
        json.dumps(
            {
                "services": {"api": {"functions": {"index.py": {"regions": ["iad1"]}}}},
                "crons": [{"path": p, "schedule": s} for p, s in _CRONS.items()],
            }
        ),
    )
    api_route = {"src": "^/api/v1(?:/(.*))$", "destination": {"service": "api", "type": "service"}}
    web_route = {"src": "^(?:/(.*))$", "destination": {"service": "web", "type": "service"}}
    routes = (
        [{"handle": "filesystem"}, web_route, api_route] if mutation == "swapped_routes" else [api_route, web_route]
    )
    names = ["web"] if mutation == "one_service" else ["web", "api"]
    _write(
        output / "config.json",
        json.dumps(
            {
                "routes": routes,
                "crons": [{"path": p, "schedule": s} for p, s in crons.items()],
                "services": [
                    {"name": n, "runtime": "python", "entrypoint": "index.py"} if n == "api" else {"name": n}
                    for n in names
                ],
            }
        ),
    )
    _write(output / "services" / "web" / "config.json", "{}")
    files = {"index.py": b"app = None\n", "app/__init__.py": b"", "_vendor/fastapi/__init__.py": b""}
    files["_vendor/pydantic_core/_core.cpython-312-x86_64-linux-gnu.so"] = (
        _MACHO_ARM64 if mutation == "macho" else _ELF_X86_64
    )
    if mutation == "tests_file":
        files["tests/test_x.py"] = b"def test_x(): ...\n"
    if mutation == "cache_file":
        files[".pytest_cache/README.md"] = b"cache\n"
    mapping = {}
    for dest, content in files.items():
        _write(root / "src" / dest, content)
        mapping[dest] = f"src/{dest}"
    func = output / "services" / "api" / "functions" / "python.func"
    vc: dict[str, Any] = {
        "runtime": "python3.11" if mutation == "python311" else "python3.12",
        "architecture": "arm64" if mutation == "arm64" else "x86_64",
        "regions": [region],
        "filePathMap": mapping,
    }
    _write(func / ".vc-config.json", json.dumps(vc))
    return root, output


def self_test() -> None:
    expected = {
        "tests_file": "no_test_files_or_caches",
        "cache_file": "no_test_files_or_caches",
        "macho": "native_libraries_linux_x86_64",
        "missing_cron": "crons_declared_as_committed",
        "extra_cron": "crons_declared_as_committed",
        "wrong_region": "api_function_region",
        "swapped_routes": "routing_order",
        "one_service": "both_services_declared",
        "python311": "api_runtime",
        "arm64": "api_architecture",
    }
    problems: list[str] = []
    tmp = Path(tempfile.mkdtemp(prefix="vercel-build-selftest-"))
    try:
        root, output = _plant(tmp / "clean", None)
        clean = check(root, output, dict(_CRONS))
        if clean.failed:
            problems.append(f"a clean tree failed: {[c['name'] for c in clean.failed]}")
        for mutation, name in expected.items():
            root, output = _plant(tmp / mutation, mutation)
            failed = {c["name"] for c in check(root, output, dict(_CRONS)).failed}
            if name not in failed:
                problems.append(f"missed {mutation}: expected {name} to fail, failed: {sorted(failed)}")
        root, output = _plant(tmp / "registry", None)
        if "crons_match_job_registry" not in {
            c["name"] for c in check(root, output, {**_CRONS, "/x": "0 4 * * *"}).failed
        }:
            problems.append("missed a registered job that is not scheduled")
        if header_platform(_ELF_X86_64) != "linux-x86_64" or header_platform(_MACHO_ARM64) != "macos":
            problems.append("header_platform misclassified a control")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if problems:
        sys.exit("check_vercel_build self-test FAILED:\n  - " + "\n  - ".join(problems))
    print(
        f"check_vercel_build self-test passed: a clean tree passes and {len(expected) + 1} planted defects are caught."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("check", help="check a build output")
    run.add_argument("--root", required=True, type=Path, help="the exported repository that was built")
    run.add_argument("--output", required=True, type=Path, help="its .vercel/output")
    run.add_argument(
        "--api-dir", type=Path, help="the API package providing the job registry (default: <root>/apps/api)"
    )
    sub.add_parser("self-test", help="prove that planted defects are caught")
    args = parser.parse_args()
    if args.command == "self-test":
        self_test()
        return
    root = args.root.resolve()
    checks = check(root, args.output.resolve(), registered_jobs((args.api_dir or root / "apps" / "api").resolve()))
    for item in checks.items:
        detail = {k: v for k, v in item.items() if k not in ("name", "ok")}
        print(f"{'ok  ' if item['ok'] else 'FAIL'} {item['name']}" + (f"  {json.dumps(detail)}" if detail else ""))
    if checks.failed:
        sys.exit(f"vercel build output: {len(checks.failed)} of {len(checks.items)} checks failed")
    print(f"vercel build output: {len(checks.items)} checks passed")


if __name__ == "__main__":
    main()
