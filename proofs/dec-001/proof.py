"""DEC-001 local proof: host-side steps (spec §3.3). LOCAL USE ONLY; never deployed.

``run.sh`` calls these subcommands in order. Every credential is generated for
the run and is test-only. Nothing contacts Vercel, Clerk, Stripe, or any other
provider: the Clerk Backend API URL points at a closed loopback port, and
billing and LLM are mocked.

    env         write a proof-only .env (RSA key pair, webhook and cron secrets)
    db          create and migrate a dedicated local database
    bundle      measure the packaged Python function and assemble it as /var/task
    runtime     start the packaged function in a linux/amd64 container (emulator.py)
    probe       HTTP checks against a running API
    outage      database pooler hung, then down, then recovered
    ipc         pair the runtime's handler-started and end messages
    session     transaction-local settings across clients of one pooled server connection
    coldstart   cold starts of the packaged function (container) and of the source tree (host)
    report      results/*.json -> results/summary.md

Run it with the API's environment: ``uv run --project apps/api python proofs/dec-001/proof.py``.
"""

# The subprocess calls run fixed docker and uvicorn commands; arguments come from run.sh.
# ruff: noqa: S603, S607

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import hmac
import itertools
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
import jwt
import psycopg
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
API_DIR = REPO / "apps" / "api"
# Strings that must never reach a client: stack traces and file-system paths.
LEAK_MARKERS = ("Traceback", 'File "', "/var/task", "site-packages", "/Users/", "/home/")
# Vercel's documented maximum for a Python function bundle: 500 MB uncompressed.
# Compared in decimal megabytes, the stricter reading.
BUNDLE_LIMIT_MB = 500
API_RUNTIME = "python3.12"
# Vercel Deployment Protection: Protection Bypass for Automation (preview probes only).
BYPASS_ENV = "VERCEL_AUTOMATION_BYPASS_SECRET"


# -- Helpers ------------------------------------------------------------------


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8")


def read_env(path: Path) -> dict[str, str]:
    """Parse the .env this script writes (double-quoted values may hold \\n escapes)."""
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = value[1:-1].replace("\\n", "\n")
        values[key.strip()] = value
    return values


def percentile(samples: list[float], q: float) -> float:
    ordered = sorted(samples)
    return round(ordered[min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))], 1)


def distribution(samples: list[float]) -> dict[str, float | int]:
    if not samples:
        return {"n": 0}
    return {
        "n": len(samples),
        "min": round(min(samples), 1),
        "p50": percentile(samples, 0.5),
        "p95": percentile(samples, 0.95),
        "max": round(max(samples), 1),
    }


class Checks:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def add(self, name: str, ok: bool, **detail: Any) -> None:
        self.items.append({"check": name, "ok": bool(ok), **detail})
        print(f"  [{'PASS' if ok else 'FAIL'}] {name} {json.dumps(detail, default=str)[:300]}")

    @property
    def passed(self) -> bool:
        return all(item["ok"] for item in self.items)


# -- env ----------------------------------------------------------------------


def cmd_env(args: argparse.Namespace) -> None:
    keys = Path(args.keys)
    keys.mkdir(parents=True, exist_ok=True)
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    public_pem = (
        private.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    key_file = keys / "jwt-private.pem"
    key_file.write_text(private_pem, encoding="utf-8")
    key_file.chmod(0o600)
    # A preview's CLERK_JWT_KEY, pasted as multi-line text (README: Probing a preview).
    (keys / "jwt-public.pem").write_text(public_pem, encoding="utf-8")

    origin = f"http://127.0.0.1:{args.port}"
    host = "proof-dec-001.clerk.invalid"  # .invalid never resolves (RFC 2606)
    publishable = "pk_test_" + base64.b64encode(f"{host}$".encode()).decode().rstrip("=")
    proof = {
        "issuer": f"https://{host}",
        "azp": origin,
        "webhook_secret": "whsec_" + secrets.token_hex(24),
        "cron_secret": secrets.token_hex(24),
        "private_key_file": str(key_file),
    }
    overrides = {
        "APP_ENV": "local",
        "APP_BASE_URL": origin,
        "DATABASE_URL": args.database_url,
        "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY": publishable,
        "CLERK_SECRET_KEY": "sk_test_" + secrets.token_hex(16),
        "CLERK_AUTHORIZED_PARTIES": origin,
        "CLERK_JWT_KEY": public_pem,
        "CLERK_API_URL": "http://127.0.0.1:9",  # closed port: organization lookups cannot leave the machine
        "BILLING_MODE": "mock",
        "STRIPE_WEBHOOK_SECRET": proof["webhook_secret"],
        "LLM_MODE": "mock",
        "ANALYTICS_MODE": "disabled",
        "ERROR_REPORTING_MODE": "disabled",
        "CRON_SECRET": proof["cron_secret"],
    }
    base = read_env(REPO / ".env.example")
    base.update(overrides)
    lines = ["# DEC-001 local proof. Generated test-only values; never reuse."]
    for key, value in base.items():
        lines.append(f'{key}="{value.replace(chr(10), chr(92) + "n")}"' if "\n" in value else f"{key}={value}")
    Path(args.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    write_json(keys / "proof.json", proof)
    print(f"wrote {args.out} and {keys / 'proof.json'}")


# -- db -----------------------------------------------------------------------


def cmd_db(args: argparse.Namespace) -> None:
    sys.path.insert(0, str(API_DIR))
    from app.kernel.infrastructure import migrations

    migrations.require_local_database(args.admin_url, "create or drop the proof database")
    with psycopg.connect(args.admin_url, autocommit=True) as conn:
        conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()",
            (args.name,),
        )
        conn.execute(f'DROP DATABASE IF EXISTS "{args.name}"')
        if args.drop:
            print(f"dropped {args.name}")
            return
        conn.execute(f'CREATE DATABASE "{args.name}"')
    url = args.admin_url.rsplit("/", 1)[0] + f"/{args.name}"
    migrations.migrate(url)
    print(f"created and migrated {args.name}")


# -- bundle -------------------------------------------------------------------


def cmd_bundle(args: argparse.Namespace) -> None:
    root, output, task = Path(args.root), Path(args.output), Path(args.task)
    config = json.loads((output / "config.json").read_text())
    funcs = [p for p in (output / "services" / "api" / "functions").glob("*.func") if p.is_dir()]
    if len(funcs) != 1:
        sys.exit(f"expected one API function, found {[p.name for p in funcs]}")
    func = funcs[0]
    vc = json.loads((func / ".vc-config.json").read_text())
    mapping: dict[str, str] = vc.get("filePathMap", {})

    if task.exists():
        shutil.rmtree(task)
    sizes: Counter[str] = Counter()
    total = 0
    count = 0
    for dest, src in mapping.items():
        source = root / src
        target = task / dest
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        size = source.stat().st_size
        total += size
        count += 1
        parts = dest.split("/")
        sizes[parts[1] if parts[0] == "_vendor" and len(parts) > 1 else parts[0]] += size
    for extra in func.iterdir():
        if extra.name != ".vc-config.json" and extra.is_file():
            shutil.copy2(extra, task / extra.name)
            total += extra.stat().st_size
            count += 1

    native = [k for k in mapping if k.endswith(".so") or ".so." in k]
    platforms = {k: binary_platform(root / mapping[k]) for k in native}
    web_funcs = list((output / "services" / "web" / "functions").rglob("*.func"))
    result = {
        "runtime": vc.get("runtime"),
        "architecture": vc.get("architecture"),
        "handler": vc.get("handler"),
        "environment": vc.get("environment"),
        "supports_response_streaming": vc.get("supportsResponseStreaming"),
        "files": count,
        "uncompressed_bytes": total,
        "uncompressed_mib": round(total / 1_048_576, 1),
        "documented_limit_mb": BUNDLE_LIMIT_MB,
        "test_files_included": sum(1 for k in mapping if k.startswith("tests/")),
        "largest": [{"package": k, "mib": round(v / 1_048_576, 1)} for k, v in sizes.most_common(10)],
        "native_libraries": len(native),
        # From each file's header: the filename tag is missing on abi3 and vendored libraries.
        "native_platforms": dict(Counter(platforms.values())),
        "native_not_linux_x86_64": sorted(k for k, v in platforms.items() if v != "linux-x86_64")[:20],
        "native_linux_x86_64_tagged": sum(1 for k in native if "x86_64-linux" in k),
        "native_macos_tagged": sum(1 for k in native if "darwin" in k or "macosx" in k),
        "services": [
            {k: s.get(k) for k in ("name", "root", "framework", "runtime", "entrypoint")} for s in config["services"]
        ],
        "routes": [r for r in config.get("routes", []) if "src" in r],
        "crons": config.get("crons", []),
        "registered_jobs": registered_jobs(),
        "web_functions": len(web_funcs),
        "assembled_task_root": str(task),
    }
    checks = bundle_checks(result, total)
    result["checks"], result["passed"] = checks.items, checks.passed
    write_json(Path(args.results) / "bundle.json", result)
    print(json.dumps({k: result[k] for k in ("runtime", "architecture", "files", "uncompressed_mib")}))
    if not checks.passed:
        sys.exit("bundle: some checks failed")


# ELF e_machine values, and the magic numbers of Mach-O (macOS) binaries, thin and universal.
_ELF_MACHINES = {3: "x86", 40: "arm", 62: "x86_64", 183: "aarch64"}
_MACHO_MAGICS = {
    b"\xfe\xed\xfa\xce",
    b"\xfe\xed\xfa\xcf",
    b"\xce\xfa\xed\xfe",
    b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe",
}


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


def registered_jobs() -> dict[str, str]:
    """The API's job registry as Vercel Cron paths and schedules. Builds the app without contacting any service."""
    sys.path.insert(0, str(API_DIR))
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


def bundle_checks(result: dict[str, Any], total_bytes: int) -> Checks:
    checks = Checks()
    # Controls: the header check must tell a Linux x86-64 library from the binaries it has to reject.
    elf = b"\x7fELF\x02\x01\x01" + bytes(11)
    controls = {
        "x86_64": header_platform(elf + (62).to_bytes(2, "little")),
        "aarch64": header_platform(elf + (183).to_bytes(2, "little")),
        "macos_arm64": header_platform(b"\xcf\xfa\xed\xfe" + bytes(16)),
        "macos_universal": header_platform(b"\xca\xfe\xba\xbe" + bytes(16)),
        "not_a_binary": header_platform(b"#!/usr/bin/env python3\n"),
    }
    checks.add(
        "native_header_controls",
        controls
        == {
            "x86_64": "linux-x86_64",
            "aarch64": "elf-aarch64-64bit",
            "macos_arm64": "macos",
            "macos_universal": "macos",
            "not_a_binary": "unknown",
        },
        **controls,
    )
    checks.add("api_runtime", result["runtime"] == API_RUNTIME, runtime=result["runtime"], expected=API_RUNTIME)
    checks.add("api_architecture", result["architecture"] == "x86_64", architecture=result["architecture"])
    checks.add(
        "within_bundle_limit",
        total_bytes < BUNDLE_LIMIT_MB * 1_000_000,
        uncompressed_mib=result["uncompressed_mib"],
        limit_mb=BUNDLE_LIMIT_MB,
    )
    checks.add("no_test_files", result["test_files_included"] == 0, test_files=result["test_files_included"])
    checks.add(
        "native_libraries_linux_x86_64",
        not result["native_not_linux_x86_64"] and result["native_macos_tagged"] == 0,
        platforms=result["native_platforms"],
        macos_tagged=result["native_macos_tagged"],
        offending=result["native_not_linux_x86_64"],
    )
    scheduled = {c.get("path"): c.get("schedule") for c in result["crons"]}
    registered = result["registered_jobs"]
    checks.add(
        "crons_match_job_registry",
        bool(registered) and len(scheduled) == len(result["crons"]) and scheduled == registered,
        missing=sorted(registered.keys() - scheduled.keys()),
        unknown=sorted(scheduled.keys() - registered.keys()),
        schedule_differs=sorted(k for k in scheduled.keys() & registered.keys() if scheduled[k] != registered[k]),
    )
    return checks


# -- runtime ------------------------------------------------------------------


def runtime_env(env_file: Path, database_url: str) -> dict[str, str]:
    env = read_env(env_file)
    env.update(
        {
            "APP_ENV": "preview",  # what a Vercel preview deployment would set; mock modes stay allowed
            "DATABASE_URL": database_url,
            # The function's .vc-config.json environment, which the platform applies.
            "PYTHONPATH": "_vendor",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    return env


def docker_env(env: dict[str, str]) -> tuple[list[str], dict[str, str]]:
    """``-e NAME`` arguments, and the environment for the docker CLI that holds the values.

    Docker copies each value from its own environment, so none is on a command line.
    """
    return [arg for key in env for arg in ("-e", key)], {**os.environ, **env}


def cmd_runtime(args: argparse.Namespace) -> None:
    env_args, docker_process_env = docker_env(runtime_env(Path(args.env_file), args.database_url))
    subprocess.run(["docker", "rm", "-f", args.name], capture_output=True, check=False)
    command = [
        "docker", "run", "-d", "--name", args.name, "--platform", "linux/amd64",
        "--network", args.network, "-p", f"127.0.0.1:{args.port}:8920",
        "-v", f"{args.task}:/var/task:ro", "-v", f"{HERE / 'emulator.py'}:/opt/emulator.py:ro",
        "-v", f"{args.results}:/results",
        *env_args, "--entrypoint", "python3", args.image, "/opt/emulator.py", "serve",
    ]  # fmt: skip
    subprocess.run(command, check=True, capture_output=True, env=docker_process_env)
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        state = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", args.name], capture_output=True, text=True, check=False
        )
        if state.stdout.strip() != "true":
            break
        try:
            if httpx.get(f"http://127.0.0.1:{args.port}/api/v1/health", timeout=2).status_code == 200:
                print(f"packaged runtime serving on 127.0.0.1:{args.port}")
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    logs = subprocess.run(["docker", "logs", args.name], capture_output=True, text=True, check=False)
    sys.exit(f"packaged runtime did not become healthy:\n{logs.stdout[-4000:]}{logs.stderr[-4000:]}")


# -- probe --------------------------------------------------------------------


class Tokens:
    def __init__(self, proof: dict[str, Any]) -> None:
        self.proof = proof
        self.key = Path(proof["private_key_file"]).read_text(encoding="utf-8")

    def mint(self, sub: str, *, key: Any = None, azp: str | None = None, exp_offset: int = 3600) -> str:
        now = int(time.time())
        claims = {
            "sub": sub,
            "iss": self.proof["issuer"],
            "azp": azp or self.proof["azp"],
            "iat": now - 5,
            "nbf": now - 5,
            "exp": now + exp_offset,
            "sid": f"sess_{sub}",
        }
        return jwt.encode(claims, key or self.key, algorithm="RS256")


def unsigned_token(claims: dict[str, Any]) -> str:
    """An ``alg: none`` token, which the verifier must reject (AUTH-02)."""

    def part(data: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")

    return f"{part({'alg': 'none', 'typ': 'JWT'})}.{part(claims)}."


def bypass_headers() -> dict[str, str]:
    """Vercel's Protection Bypass for Automation header, when the secret is set. Never logged or saved."""
    secret = os.environ.get(BYPASS_ENV)
    return {"x-vercel-protection-bypass": secret} if secret else {}


def stripe_signature(payload: bytes, secret: str, timestamp: int) -> str:
    signed = f"{timestamp}.".encode() + payload
    return f"t={timestamp},v1={hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()}"


class InvocationIds:
    """The per-request IDs Vercel's proxy adds, so the runtime's IPC messages can be paired.

    The runtime shim reads and strips ``x-vercel-internal-*`` headers before
    the app sees the request. IDs are unique per process run.
    """

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled
        self._counter = itertools.count(os.getpid() * 1_000_000 + 1)
        self._lock = threading.Lock()

    def _label(self, request: httpx.Request) -> None:
        with self._lock:
            n = next(self._counter)
        request.headers["x-vercel-internal-invocation-id"] = f"proof-{n}"
        request.headers["x-vercel-internal-request-id"] = str(n)

    def hooks(self) -> dict[str, list[Any]]:
        return {"request": [self._label]} if self.enabled else {}

    def async_hooks(self) -> dict[str, list[Any]]:
        async def label(request: httpx.Request) -> None:
            self._label(request)

        return {"request": [label]} if self.enabled else {}


@contextmanager
def connection_sampler(admin_url: str | None, pooler_admin_url: str | None, database: str) -> Iterator[dict[str, int]]:
    """Sample PostgreSQL backends (and PgBouncer pools) for one database every 20 ms."""
    peaks = {"postgres_backends": 0, "pooler_clients": 0, "pooler_servers": 0, "pooler_waiting": 0}
    stop = threading.Event()

    def run() -> None:
        admin = psycopg.connect(admin_url, autocommit=True) if admin_url else None
        pooler = (
            psycopg.connect(pooler_admin_url, autocommit=True, cursor_factory=psycopg.ClientCursor)
            if pooler_admin_url
            else None
        )
        try:
            while not stop.is_set():
                if admin is not None:
                    # Not its own connection: a hosted admin URL often names the probed database itself.
                    row = admin.execute(
                        "SELECT count(*) FROM pg_stat_activity WHERE datname = %s AND backend_type = 'client backend'"
                        " AND pid <> pg_backend_pid()",
                        (database,),
                    ).fetchone()
                    peaks["postgres_backends"] = max(peaks["postgres_backends"], int(row[0]) if row else 0)
                if pooler is not None:
                    cur = pooler.execute("SHOW POOLS")
                    names = [d.name for d in cur.description or []]
                    for values in cur.fetchall():
                        pool = dict(zip(names, values, strict=False))
                        if pool.get("database") != database:
                            continue
                        clients = int(pool.get("cl_active", 0)) + int(pool.get("cl_waiting", 0))
                        servers = sum(int(pool.get(k, 0)) for k in ("sv_active", "sv_idle", "sv_used"))
                        peaks["pooler_clients"] = max(peaks["pooler_clients"], clients)
                        peaks["pooler_servers"] = max(peaks["pooler_servers"], servers)
                        peaks["pooler_waiting"] = max(peaks["pooler_waiting"], int(pool.get("cl_waiting", 0)))
                time.sleep(0.02)
        finally:
            for conn in (admin, pooler):
                if conn is not None:
                    conn.close()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    try:
        yield peaks
    finally:
        stop.set()
        thread.join(timeout=5)


async def isolation(base: str, tokens: Tokens, users: int, run_id: str, ids: InvocationIds) -> dict[str, Any]:
    """Every user runs at once: two creates in parallel, then three rounds of list and /me in parallel.

    So up to two requests per user are in flight at a time. Each user must see
    exactly their own projects, and distinct users must get distinct user IDs.
    """
    latencies: list[float] = []
    statuses: Counter[int] = Counter()
    violations: list[str] = []
    bodies: list[str] = []
    user_ids: dict[str, str] = {}
    flight = {"now": 0, "peak": 0}
    limits = httpx.Limits(max_connections=users * 3, max_keepalive_connections=users * 3)
    async with httpx.AsyncClient(
        base_url=base, timeout=60, limits=limits, headers=bypass_headers(), event_hooks=ids.async_hooks()
    ) as client:

        async def call(method: str, path: str, token: str, **kwargs: Any) -> httpx.Response:
            started = time.perf_counter()
            flight["now"] += 1
            flight["peak"] = max(flight["peak"], flight["now"])
            try:
                response = await client.request(method, path, headers={"Authorization": f"Bearer {token}"}, **kwargs)
            finally:
                flight["now"] -= 1
            latencies.append((time.perf_counter() - started) * 1000)
            statuses[response.status_code] += 1
            bodies.append(response.text)
            return response

        async def flow(i: int) -> None:
            sub = f"user_proof_{run_id}_{i:03d}"
            token = tokens.mint(sub)
            names = {f"{sub}-p{k}" for k in range(2)}
            created = await asyncio.gather(
                *(call("POST", "/api/v1/projects", token, json={"name": n}) for n in sorted(names))
            )
            if any(r.status_code != 201 for r in created):
                violations.append(f"{sub}: create returned {[r.status_code for r in created]}")
            listed: list[set[str]] = []
            me_ids: set[str] = set()
            for _ in range(3):
                page, me = await asyncio.gather(
                    call("GET", "/api/v1/projects?limit=100", token), call("GET", "/api/v1/me", token)
                )
                if page.status_code == 200:
                    listed.append({item["name"] for item in page.json()["items"]})
                if me.status_code == 200:
                    me_ids.add(me.json()["user_id"])
            for seen in listed:
                if seen != names:
                    violations.append(f"{sub}: expected {sorted(names)}, saw {sorted(seen)[:5]}")
            if len(me_ids) != 1:
                violations.append(f"{sub}: /me returned {len(me_ids)} distinct user ids")
            else:
                user_ids[sub] = next(iter(me_ids))

        started = time.perf_counter()
        await asyncio.gather(*(flow(i) for i in range(users)))
        elapsed = time.perf_counter() - started
    if len(set(user_ids.values())) != len(user_ids):
        violations.append("distinct users were given the same user id")
    leaks = [m for m in LEAK_MARKERS if any(m in b for b in bodies)]
    return {
        "users": users,
        "requests": sum(statuses.values()),
        "peak_in_flight": flight["peak"],
        "statuses": dict(statuses),
        "violations": violations[:20],
        "violation_count": len(violations),
        "latency_ms": distribution(latencies),
        "wall_seconds": round(elapsed, 2),
        "leak_markers": leaks,
    }


def cmd_probe(args: argparse.Namespace) -> None:
    proof = json.loads(Path(args.proof_json).read_text())
    tokens = Tokens(proof)
    ids = InvocationIds(args.vercel_ids)
    base = args.base_url.rstrip("/")
    checks = Checks()
    bodies: list[str] = []
    client = httpx.Client(base_url=base, timeout=60, headers=bypass_headers(), event_hooks=ids.hooks())

    def request(method: str, path: str, **kwargs: Any) -> httpx.Response:
        response = client.request(method, path, **kwargs)
        bodies.append(response.text)
        return response

    print(f"probing {base} ({args.label})")
    r = request("GET", "/api/v1/health")
    if r.status_code == 401:
        # The API never answers /health with 401; Vercel Deployment Protection does.
        action = "It rejected" if bypass_headers() else "Set"
        sys.exit(
            f"probe {args.label}: /api/v1/health returned 401: the deployment is behind Vercel Deployment "
            f"Protection. {action} {BYPASS_ENV} (the project's Protection Bypass for Automation secret)."
        )
    checks.add("health", r.status_code == 200, status=r.status_code)
    r = request("GET", "/api/v1/proof-route-that-does-not-exist")
    checks.add(
        "api_owns_api_v1_namespace",
        r.status_code == 404 and r.headers.get("content-type", "").startswith("application/problem+json"),
        status=r.status_code,
        content_type=r.headers.get("content-type"),
    )
    if args.web:
        r = request("GET", "/")
        checks.add(
            "web_serves_other_paths",
            r.status_code < 500 and "text/html" in r.headers.get("content-type", ""),
            status=r.status_code,
            content_type=r.headers.get("content-type"),
        )
    r = request("GET", "/api/v1/ready")
    checks.add("ready_with_database", r.status_code == 200, status=r.status_code, body=r.text[:120])

    # Scheduled jobs: the platform calls these paths with Authorization: Bearer <CRON_SECRET>.
    unauth = request("GET", "/api/v1/jobs/prune-expired")
    auth = request("GET", "/api/v1/jobs/prune-expired", headers={"Authorization": f"Bearer {proof['cron_secret']}"})
    checks.add(
        "cron_job_route",
        unauth.status_code == 401 and auth.status_code == 200,
        without_secret=unauth.status_code,
        with_secret=auth.status_code,
    )

    # Authentication negatives through the packaged runtime (AUTH-01 to AUTH-04).
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    sub = f"user_proof_auth_{secrets.token_hex(4)}"
    cases = {
        "no_token": None,
        "foreign_key": tokens.mint(sub, key=other_key),
        "expired": tokens.mint(sub, exp_offset=-600),
        "wrong_authorized_party": tokens.mint(sub, azp="https://attacker.invalid"),
        "alg_none": unsigned_token({"sub": sub, "iss": proof["issuer"], "azp": proof["azp"]}),
    }
    results = {}
    for name, token in cases.items():
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        results[name] = request("GET", "/api/v1/me", headers=headers).status_code
    valid = request("GET", "/api/v1/me", headers={"Authorization": f"Bearer {tokens.mint(sub)}"}).status_code
    checks.add(
        "auth_rejections", all(s == 401 for s in results.values()) and valid == 200, rejected=results, valid=valid
    )

    # Stripe webhooks: the handler must see the body byte-for-byte (BILL-08).
    secret = proof["webhook_secret"]
    event_id = f"evt_proof_{secrets.token_hex(8)}"
    # CRLF, a tab, non-ASCII text, a no-break space, and trailing whitespace: any
    # re-encoding or re-serialization on the way would break the signature.
    payload = (
        '{\r\n  "id": "' + event_id + '",  "object": "event",\t"type": "customer.created",\n'
        '  "created": ' + str(int(time.time())) + ', "api_version": "2026-01-01",\n'
        '  "data": {"object": {"id": "cus_proof_' + secrets.token_hex(4) + '", "object": "customer", '
        '"name": "Zo\u00eb \u2713 \u00a0 "}}\n}\n   '
    ).encode("utf-8")
    now = int(time.time())

    def deliver(body: bytes, signature: str | None) -> httpx.Response:
        headers = {"Content-Type": "application/json"}
        if signature:
            headers["Stripe-Signature"] = signature
        return request("POST", "/api/v1/webhooks/stripe", content=body, headers=headers)

    genuine = deliver(payload, stripe_signature(payload, secret, now))
    duplicate = deliver(payload, stripe_signature(payload, secret, now + 1))
    altered_body = payload.replace("Zoë".encode(), b"Zoe")
    statuses = {
        "genuine": genuine.status_code,
        "duplicate": duplicate.status_code,
        "altered_body": deliver(altered_body, stripe_signature(payload, secret, now)).status_code,
        "wrong_secret": deliver(payload, stripe_signature(payload, "whsec_" + "0" * 48, now)).status_code,
        "stale_timestamp": deliver(payload, stripe_signature(payload, secret, now - 3600)).status_code,
        "missing_signature": deliver(payload, None).status_code,
    }
    expected = {
        "genuine": 200,
        "duplicate": 200,
        "altered_body": 400,
        "wrong_secret": 400,
        "stale_timestamp": 400,
        "missing_signature": 400,
    }
    checks.add(
        "stripe_webhook_raw_body",
        statuses == expected,
        statuses=statuses,
        genuine_body=genuine.text[:120],
        duplicate_body=duplicate.text[:120],
        payload_bytes=len(payload),
    )
    # The redelivery must find the first delivery's ledger row, not create a second one.
    with psycopg.connect(args.admin_url.rsplit("/", 1)[0] + f"/{args.database}") as conn:
        row = conn.execute(
            "SELECT count(*), max(attempts), max(status) FROM app.billing_events WHERE stripe_event_id = %s",
            (event_id,),
        ).fetchone()
    ledger = {"rows": row[0], "attempts": row[1], "status": row[2]} if row else {}
    checks.add("stripe_webhook_duplicate_ledger", ledger == {"rows": 1, "attempts": 2, "status": "ignored"}, **ledger)

    # Request body limit enforced by the app (MAX_REQUEST_BODY_BYTES; SEC-09).
    token = tokens.mint(sub)
    big = b'{"name": "' + b"x" * 1_500_000 + b'"}'
    r = request(
        "POST",
        "/api/v1/projects",
        content=big,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    checks.add("body_limit", r.status_code == 413, status=r.status_code, bytes=len(big))
    r = request(
        "POST",
        "/api/v1/projects",
        content=b'{"name": ',
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    checks.add(
        "malformed_json_is_problem",
        400 <= r.status_code < 500 and r.headers.get("content-type", "").startswith("application/problem+json"),
        status=r.status_code,
    )

    # Concurrency on one instance: isolation, pool bounds, and latency.
    run_id = secrets.token_hex(3)
    with connection_sampler(args.admin_url, args.pooler_admin_url, args.database) as peaks:
        iso = asyncio.run(isolation(base, tokens, args.users, run_id, ids))
    iso["peaks"] = dict(peaks)
    checks.add(
        "concurrent_isolation",
        iso["violation_count"] == 0 and set(iso["statuses"]) <= {200, 201} and not iso["leak_markers"],
        users=iso["users"],
        requests=iso["requests"],
        peak_in_flight=iso["peak_in_flight"],
        statuses=iso["statuses"],
        violations=iso["violation_count"],
    )
    # One instance must stay within its pools (DATABASE_POOL_MAX_SIZE + DATABASE_WORKER_POOL_MAX_SIZE).
    observed = peaks["pooler_clients"] if args.pooler_admin_url else peaks["postgres_backends"]
    checks.add("connection_bound", 0 < observed <= args.max_db_clients, observed=observed, bound=args.max_db_clients)
    leaks = [m for m in LEAK_MARKERS if any(m in b for b in bodies)]
    checks.add("no_internal_details_in_responses", not leaks, markers=leaks)
    client.close()
    write_json(
        Path(args.results) / f"probe-{args.label}.json",
        {"label": args.label, "base_url": base, "passed": checks.passed, "checks": checks.items, "isolation": iso},
    )
    if not checks.passed:
        sys.exit(f"probe {args.label}: some checks failed")


# -- outage -------------------------------------------------------------------


def timed(fn: Callable[[], httpx.Response]) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        response = fn()
        result: dict[str, Any] = {
            "status": response.status_code,
            "code": _problem_code(response),
            "leaks": [m for m in LEAK_MARKERS if m in response.text],
        }
    except httpx.TimeoutException:
        result = {"status": None, "code": "client_timeout", "leaks": []}
    result["seconds"] = round(time.perf_counter() - started, 2)
    return result


def _problem_code(response: httpx.Response) -> str | None:
    try:
        body = response.json()
    except ValueError:
        return None
    return body.get("code") or body.get("status") if isinstance(body, dict) else None


def wait_ready(base: str, limit: float) -> dict[str, Any]:
    """Poll /ready until it returns 200; report the time and every status seen on the way."""
    started = time.perf_counter()
    seen: Counter[str] = Counter()
    while time.perf_counter() - started < limit:
        try:
            status: int | str = httpx.get(f"{base}/api/v1/ready", timeout=5).status_code
        except httpx.HTTPError as exc:
            status = type(exc).__name__
        seen[str(status)] += 1
        if status == 200:
            return {"seconds": round(time.perf_counter() - started, 2), "polls": dict(seen)}
        time.sleep(0.25)
    return {"seconds": None, "polls": dict(seen)}


def cmd_outage(args: argparse.Namespace) -> None:
    proof = json.loads(Path(args.proof_json).read_text())
    token = Tokens(proof).mint(f"user_proof_outage_{secrets.token_hex(4)}")
    env = read_env(Path(args.env_file))
    connect_timeout = float(env.get("DATABASE_CONNECT_TIMEOUT_SECONDS", "5"))
    round_trip = int(env.get("DATABASE_STATEMENT_TIMEOUT_MS", "10000")) / 1000 + connect_timeout
    slack = 3.0
    base = args.base_url.rstrip("/")
    client = httpx.Client(
        base_url=base, timeout=args.client_timeout, event_hooks=InvocationIds(args.vercel_ids).hooks()
    )
    auth = {"Authorization": f"Bearer {token}"}

    def ready() -> httpx.Response:
        return client.get("/api/v1/ready")

    def projects() -> httpx.Response:
        return client.get("/api/v1/projects", headers=auth)

    def docker(*command: str) -> None:
        subprocess.run(["docker", *command, args.pooler], check=True, capture_output=True)

    result: dict[str, Any] = {"client_timeout_seconds": args.client_timeout}
    result["baseline"] = {"ready": timed(ready), "projects": timed(projects)}
    print("pooler paused (connections hang)")
    docker("pause")
    result["hung"] = {"ready": timed(ready), "projects": timed(projects)}
    docker("unpause")
    result["hung"]["recovery"] = wait_ready(base, 60)
    result["hung"]["next_requests"] = dict(Counter(str(timed(projects)["status"]) for _ in range(10)))
    # A stopped container's name no longer resolves, so no new connection can open: the request
    # fails when the pool's checkout timeout (DATABASE_CONNECT_TIMEOUT_SECONDS) expires.
    print("pooler stopped (its connections close and its name stops resolving)")
    docker("stop")
    result["down"] = {"ready": timed(ready), "projects": timed(projects)}
    docker("start")
    result["down"]["recovery"] = wait_ready(base, 60)
    result["down"]["next_requests"] = dict(Counter(str(timed(projects)["status"]) for _ in range(10)))
    client.close()

    def unavailable_within(case: str, bound: float) -> bool:
        return all(
            result[case][k]["status"] == 503 and result[case][k]["seconds"] <= bound and not result[case][k]["leaks"]
            for k in ("ready", "projects")
        )

    def recovered(case: str) -> bool:
        return result[case]["recovery"]["seconds"] is not None and result[case]["next_requests"] == {"200": 10}

    checks = Checks()
    checks.add("baseline", all(result["baseline"][k]["status"] == 200 for k in ("ready", "projects")))
    checks.add("hung_pooler_fails_within_round_trip_deadline", unavailable_within("hung", round_trip + slack),
               bound_seconds=round_trip + slack)  # fmt: skip
    checks.add("hung_pooler_recovers_without_failed_requests", recovered("hung"))
    checks.add("stopped_pooler_fails_within_checkout_timeout", unavailable_within("down", connect_timeout + slack),
               bound_seconds=connect_timeout + slack)  # fmt: skip
    checks.add("restarted_pooler_recovers_without_failed_requests", recovered("down"))
    result["checks"], result["passed"] = checks.items, checks.passed
    write_json(Path(args.results) / "outage.json", result)
    print(json.dumps(result, indent=2))
    if not checks.passed:
        sys.exit("outage: some checks failed")


# -- ipc ----------------------------------------------------------------------


def cmd_ipc(args: argparse.Namespace) -> None:
    """Every labeled request the runtime started must have ended exactly once."""
    started: Counter[tuple[str, int]] = Counter()
    ended: Counter[tuple[str, int]] = Counter()
    for line in (Path(args.results) / "ipc.jsonl").read_text(encoding="utf-8").splitlines():
        message = json.loads(line)
        if message.get("type") not in ("handler-started", "end"):
            continue
        context = message["payload"]["context"]
        key = (str(context["invocationId"]), int(context["requestId"]))
        (started if message["type"] == "handler-started" else ended)[key] += 1
    unlabeled = ("0", 0)  # requests without platform IDs, such as readiness polls
    labeled = {k: v for k, v in started.items() if k != unlabeled}
    labeled_ended = {k: v for k, v in ended.items() if k != unlabeled}
    checks = Checks()
    checks.add(
        "every_labeled_request_ended_once",
        len(labeled) >= args.min_requests
        and labeled.keys() == labeled_ended.keys()
        and all(v == 1 for v in (*labeled.values(), *labeled_ended.values())),
        labeled_requests=len(labeled),
        missing_end=len(labeled.keys() - labeled_ended.keys()),
        end_without_start=len(labeled_ended.keys() - labeled.keys()),
    )
    checks.add(
        "unlabeled_requests_ended",
        started[unlabeled] == ended[unlabeled],
        started=started[unlabeled],
        ended=ended[unlabeled],
    )
    write_json(Path(args.results) / "ipc.json", {"passed": checks.passed, "checks": checks.items})
    if not checks.passed:
        sys.exit("ipc: some checks failed")


# -- session ------------------------------------------------------------------


def cmd_session(args: argparse.Namespace) -> None:
    """Show that transaction-local settings do not follow a pooled server connection.

    Two separate client connections go through the pooler one after the other,
    so PgBouncer hands both the same server connection (same backend PID). A
    session-level setting is the positive control: it does leak, which shows
    the check can detect leakage.
    """
    deadline = time.monotonic() + 30
    while True:
        try:
            psycopg.connect(args.pooler_url, prepare_threshold=None, connect_timeout=3).close()
            break
        except psycopg.OperationalError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.5)

    def run(is_local: bool) -> dict[str, Any]:
        marker = f"leak-{secrets.token_hex(4)}"
        with psycopg.connect(args.pooler_url, prepare_threshold=None) as first:
            with first.transaction():
                row = first.execute(
                    "SELECT pg_backend_pid(), set_config('app.proof_marker', %s, %s)", (marker, is_local)
                ).fetchone()
            first_pid = row[0] if row else None
        with psycopg.connect(args.pooler_url, prepare_threshold=None) as second:
            row = second.execute("SELECT pg_backend_pid(), current_setting('app.proof_marker', true)").fetchone()
            second_pid, seen = (row[0], row[1]) if row else (None, None)
            if not is_local:
                second.execute("RESET app.proof_marker")
                second.commit()
        return {
            "same_server_connection": first_pid == second_pid,
            "second_client_saw_marker": seen == marker,
        }

    result: dict[str, Any] = {"transaction_local": run(True), "session_level_control": run(False)}
    result["passed"] = (
        result["transaction_local"]["same_server_connection"]
        and not result["transaction_local"]["second_client_saw_marker"]
        and result["session_level_control"]["second_client_saw_marker"]
    )
    write_json(Path(args.results) / "session.json", result)
    print(json.dumps(result, indent=2))
    if not result["passed"]:
        sys.exit("session check failed or was inconclusive")


# -- coldstart ----------------------------------------------------------------


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def cmd_coldstart(args: argparse.Namespace) -> None:
    results = Path(args.results)
    (results / "coldstart.jsonl").unlink(missing_ok=True)
    env_args, docker_process_env = docker_env(runtime_env(Path(args.env_file), args.database_url))
    for i in range(args.runs):
        for path in ("/api/v1/health", "/api/v1/ready"):
            subprocess.run(
                [
                    "docker", "run", "--rm", "--platform", "linux/amd64", "--network", args.network,
                    "-v", f"{args.task}:/var/task:ro", "-v", f"{HERE / 'emulator.py'}:/opt/emulator.py:ro",
                    "-v", f"{results}:/results", *env_args,
                    "--entrypoint", "python3", args.image, "/opt/emulator.py", "once", path,
                ],
                check=True,
                capture_output=True,
                env=docker_process_env,
            )  # fmt: skip
        print(f"  container cold start {i + 1}/{args.runs}")
    container = [json.loads(line) for line in (results / "coldstart.jsonl").read_text().splitlines()]

    native: list[dict[str, Any]] = []
    host_env = dict(os.environ)
    host_env.update(read_env(Path(args.env_file)))
    host_env["DATABASE_URL"] = args.native_database_url
    for i in range(args.runs):
        port = free_port()
        started = time.perf_counter()
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "index:app", "--app-dir", str(API_DIR), "--port", str(port)],
            env=host_env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            health_ms = None
            while time.perf_counter() - started < 60:
                try:
                    if httpx.get(f"http://127.0.0.1:{port}/api/v1/health", timeout=1).status_code == 200:
                        health_ms = (time.perf_counter() - started) * 1000
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.005)
            t = time.perf_counter()
            ready = httpx.get(f"http://127.0.0.1:{port}/api/v1/ready", timeout=10)
            native.append(
                {
                    "spawn_to_first_response_ms": round(health_ms or -1, 1),
                    "first_ready_ms": round((time.perf_counter() - t) * 1000, 1),
                    "ready_status": ready.status_code,
                }
            )
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        print(f"  host cold start {i + 1}/{args.runs}")

    def series(rows: list[dict[str, Any]], key: str, path: str | None = None) -> dict[str, Any]:
        return distribution([float(r[key]) for r in rows if path is None or r.get("path") == path])

    summary: dict[str, Any] = {
        "container_linux_amd64": {
            "note": "packaged bundle, Vercel runtime shim, x86_64 emulated on this host",
            "runtime_init_duration_ms": series(container, "runtime_init_duration_ms", "/api/v1/health"),
            "runtime_user_init_duration_ms": series(container, "runtime_user_init_duration_ms", "/api/v1/health"),
            "spawn_to_first_response_ms": series(container, "spawn_to_first_response_ms", "/api/v1/health"),
            "first_health_request_ms": series(container, "first_request_ms", "/api/v1/health"),
            "first_ready_request_ms": series(container, "first_request_ms", "/api/v1/ready"),
            "statuses": dict(Counter(f"{r['path']} {r['status']}" for r in container)),
        },
        "host_native": {
            "note": "source tree with uvicorn on this host (native architecture), for comparison",
            "spawn_to_first_response_ms": series(native, "spawn_to_first_response_ms"),
            "first_ready_request_ms": series(native, "first_ready_ms"),
            "statuses": dict(Counter(str(r["ready_status"]) for r in native)),
        },
    }
    checks = Checks()
    checks.add(
        "every_container_cold_start_answered",
        len(container) == 2 * args.runs and all(r["status"] == 200 for r in container),
        statuses=summary["container_linux_amd64"]["statuses"],
    )
    checks.add(
        "every_host_cold_start_answered",
        len(native) == args.runs
        and all(r["ready_status"] == 200 and r["spawn_to_first_response_ms"] > 0 for r in native),
        statuses=summary["host_native"]["statuses"],
    )
    summary["checks"], summary["passed"] = checks.items, checks.passed
    write_json(results / "coldstart.json", summary)
    print(json.dumps(summary, indent=2))
    if not checks.passed:
        sys.exit("coldstart: some checks failed")


# -- report -------------------------------------------------------------------


def cmd_report(args: argparse.Namespace) -> None:
    results = Path(args.results)

    def load(name: str) -> Any:
        path = results / name
        return json.loads(path.read_text()) if path.exists() else None

    meta, bundle, session, outage, cold, ipc = (
        load(n) for n in ("meta.json", "bundle.json", "session.json", "outage.json", "coldstart.json", "ipc.json")
    )
    lines = ["# DEC-001 local proof — results", ""]
    if meta:
        lines += [f"- {k}: `{v}`" for k, v in meta.items()] + [""]
    if bundle:
        lines += [
            f"## Packaging (`vercel build`, without Vercel credentials; {'PASS' if bundle['passed'] else 'FAIL'})",
            "",
            f"- Runtime `{bundle['runtime']}`, architecture `{bundle['architecture']}`, handler `{bundle['handler']}`.",
            f"- API function: {bundle['files']} files, **{bundle['uncompressed_mib']} MiB** uncompressed "
            f"(documented Python limit {bundle['documented_limit_mb']} MB). Test files included: "
            f"{bundle['test_files_included']}.",
            f"- Native libraries: {bundle['native_libraries']}, by file header {bundle['native_platforms']} "
            f"({bundle['native_linux_x86_64_tagged']} tagged x86_64-linux in the file name, "
            f"{bundle['native_macos_tagged']} tagged macOS).",
            "- Largest: " + ", ".join(f"{x['package']} {x['mib']} MiB" for x in bundle["largest"][:6]) + ".",
            f"- Services: {', '.join(s['name'] + ' (' + str(s['framework']) + ')' for s in bundle['services'])}; "
            f"crons registered: {len(bundle['crons'])} (job registry: {len(bundle['registered_jobs'])}); "
            f"web functions: {bundle['web_functions']}.",
            "",
            "| Check | Result | Detail |",
            "|---|---|---|",
        ]
        for item in bundle["checks"]:
            detail = {k: v for k, v in item.items() if k not in ("check", "ok")}
            lines.append(f"| {item['check']} | {'PASS' if item['ok'] else 'FAIL'} | `{json.dumps(detail)[:220]}` |")
        lines.append("")
    for probe in sorted(results.glob("probe-*.json")):
        data = json.loads(probe.read_text())
        lines += [f"## Probes: {data['label']} ({'PASS' if data['passed'] else 'FAIL'})", ""]
        lines += ["| Check | Result | Detail |", "|---|---|---|"]
        for item in data["checks"]:
            detail = {k: v for k, v in item.items() if k not in ("check", "ok")}
            lines.append(f"| {item['check']} | {'PASS' if item['ok'] else 'FAIL'} | `{json.dumps(detail)[:220]}` |")
        iso = data["isolation"]
        lines += [
            "",
            f"Concurrency: {iso['users']} users, {iso['requests']} requests in {iso['wall_seconds']} s, "
            f"at most {iso['peak_in_flight']} in flight; "
            f"latency {iso['latency_ms']}; peaks {iso['peaks']}.",
            "",
        ]
    if session:
        lines += ["## Transaction pooling (PgBouncer, transaction mode)", "", f"`{json.dumps(session)}`", ""]
    if outage:
        lines += [
            f"## Database outage (packaged runtime through the pooler; {'PASS' if outage['passed'] else 'FAIL'})",
            "",
            "```json",
            json.dumps(outage, indent=2),
        ]
        lines += ["```", ""]
    if ipc:
        lines += [
            f"## Runtime IPC pairing ({'PASS' if ipc['passed'] else 'FAIL'})",
            "",
            f"`{json.dumps(ipc['checks'])}`",
            "",
        ]
    if cold:
        lines += [f"## Cold starts ({'PASS' if cold['passed'] else 'FAIL'})", "", "```json", json.dumps(cold, indent=2)]
        lines += ["```", ""]
    (results / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {results / 'summary.md'}")


# -- main ---------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("env")
    p.add_argument("--out", required=True)
    p.add_argument("--keys", required=True)
    p.add_argument("--port", type=int, required=True)
    p.add_argument("--database-url", required=True)
    p.set_defaults(fn=cmd_env)

    p = sub.add_parser("db")
    p.add_argument("--admin-url", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--drop", action="store_true", help="drop the database and stop")
    p.set_defaults(fn=cmd_db)

    p = sub.add_parser("bundle")
    p.add_argument("--root", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--task", required=True)
    p.add_argument("--results", required=True)
    p.set_defaults(fn=cmd_bundle)

    for name, fn in (("runtime", cmd_runtime), ("coldstart", cmd_coldstart)):
        p = sub.add_parser(name)
        p.add_argument("--env-file", required=True)
        p.add_argument("--database-url", required=True)
        p.add_argument("--task", required=True)
        p.add_argument("--results", required=True)
        p.add_argument("--image", required=True)
        p.add_argument("--network", required=True)
        p.set_defaults(fn=fn)
        if name == "runtime":
            p.add_argument("--name", required=True)
            p.add_argument("--port", type=int, required=True)
            p.add_argument("--timeout", type=float, default=180)
        else:
            p.add_argument("--runs", type=int, default=10)
            p.add_argument("--native-database-url", required=True)

    p = sub.add_parser("probe")
    p.add_argument("--base-url", required=True)
    p.add_argument("--label", required=True)
    p.add_argument("--proof-json", required=True)
    p.add_argument("--results", required=True)
    p.add_argument("--database", required=True)
    # The ledger and connection checks read the database; without it they would prove nothing.
    p.add_argument("--admin-url", required=True, help="direct (not pooled) connection to the database server")
    p.add_argument("--pooler-admin-url")
    p.add_argument("--users", type=int, default=40)
    p.add_argument("--web", action="store_true")
    p.add_argument("--vercel-ids", action="store_true", help="label requests as Vercel's proxy does")
    p.add_argument("--max-db-clients", type=int, required=True, help="both pool sizes of one instance")
    p.set_defaults(fn=cmd_probe)

    p = sub.add_parser("outage")
    p.add_argument("--base-url", required=True)
    p.add_argument("--proof-json", required=True)
    p.add_argument("--pooler", required=True)
    p.add_argument("--results", required=True)
    p.add_argument("--client-timeout", type=float, default=45)
    p.add_argument("--env-file", required=True)
    p.add_argument("--vercel-ids", action="store_true")
    p.set_defaults(fn=cmd_outage)

    p = sub.add_parser("ipc")
    p.add_argument("--results", required=True)
    p.add_argument("--min-requests", type=int, default=300)
    p.set_defaults(fn=cmd_ipc)

    p = sub.add_parser("session")
    p.add_argument("--pooler-url", required=True)
    p.add_argument("--results", required=True)
    p.set_defaults(fn=cmd_session)

    p = sub.add_parser("report")
    p.add_argument("--results", required=True)
    p.set_defaults(fn=cmd_report)

    args = parser.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
