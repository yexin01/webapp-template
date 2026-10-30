"""Layers import an allowlist of third-party packages and kernel modules (ARCH-01, ARCH-04).

Import-linter (``pyproject.toml``) forbids named SDKs and frameworks in the domain, the use cases
and the ports. A deny-list cannot know the next SDK an adopter adds: ``requests``, ``boto3``,
``redis`` or a new provider's client in a service would pass every contract. This test names what
each layer *may* import instead, so anything else, including a package nobody has heard of yet,
fails CI. It reads the kernel-core list from the import-linter contract, so there is one list.

A violation is fixed by moving the use of the package into the module's ``adapters`` (the layers
there may import anything) behind a port. For a utility that is not a vendor SDK, add it to the
layer's allowlist below, in the same change, and say why in the review.
"""

from __future__ import annotations

import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

import grimp

API_DIR = Path(__file__).resolve().parents[2]
STDLIB = frozenset(sys.stdlib_module_names) | {"typing_extensions"}


def _kernel_core() -> tuple[str, ...]:
    """The framework-free kernel modules, from the import-linter contract that forbids frameworks there."""
    contracts = tomllib.loads((API_DIR / "pyproject.toml").read_text())["tool"]["importlinter"]["contracts"]
    [contract] = [c for c in contracts if c["name"] == "Kernel core is framework-free"]
    return tuple(contract["source_modules"])


KERNEL_CORE = _kernel_core()


def _within(module: str, packages: tuple[str, ...]) -> bool:
    return any(module == p or module.startswith(f"{p}.") for p in packages)


@dataclass(frozen=True)
class Layer:
    name: str
    # Packages beyond the standard library.
    third_party: frozenset[str] = frozenset()
    # Kernel modules beyond the kernel core.
    kernel: tuple[str, ...] = ()


KERNEL = Layer("kernel core")
DOMAIN = Layer("domain")
USE_CASES = Layer("ports, services and probes", third_party=frozenset({"pydantic"}))
ROUTES = Layer(
    "routes",
    third_party=frozenset({"anyio", "fastapi", "pydantic", "starlette"}),
    kernel=("app.kernel.http", "app.kernel.request_context"),
)
MODULE_LAYERS = {"domain": DOMAIN, "ports": USE_CASES, "service": USE_CASES, "probe": USE_CASES, "routes": ROUTES}


def layer_of(module: str) -> Layer | None:
    if _within(module, KERNEL_CORE):
        return KERNEL
    parts = module.split(".")
    if len(parts) >= 4 and parts[:2] == ["app", "modules"]:
        return MODULE_LAYERS.get(parts[3])
    return None


def violations(graph: grimp.ImportGraph) -> list[str]:
    found = []
    for importer in sorted(graph.modules):
        layer = layer_of(importer)
        if layer is None:
            continue
        for imported in sorted(graph.find_modules_directly_imported_by(importer)):
            top = imported.split(".")[0]
            if top == "app":
                # Other modules are reached through their api (test_modules.py); the kernel only as allowed.
                ok = imported.startswith("app.modules.") or _within(imported, (*KERNEL_CORE, *layer.kernel))
                what = "kernel or application module outside this layer's allowance"
            else:
                ok = top in STDLIB or top in layer.third_party
                what = "third-party package outside this layer's allowlist"
            if not ok:
                found.append(f"{importer} ({layer.name}) imports {imported}: {what}")
    return found


def test_layers_import_only_what_they_are_allowed_to() -> None:
    graph = grimp.build_graph("app", include_external_packages=True)
    assert violations(graph) == [], (
        "Move the use of the package into the module's adapters behind a port. For a utility that is "
        "not a vendor SDK, add it to the layer's allowlist in tests/architecture/test_layer_imports.py."
    )


def test_the_real_graph_has_modules_in_every_layer() -> None:
    """Guards the check against matching nothing, for example after a rename of the layer files."""
    graph = grimp.build_graph("app", include_external_packages=True)
    layers = {layer_of(m) for m in graph.modules}
    assert {KERNEL, DOMAIN, USE_CASES, ROUTES} <= layers


def synthetic(*imports: tuple[str, str]) -> grimp.ImportGraph:
    graph = grimp.ImportGraph()
    for importer, imported in imports:
        graph.add_import(importer=importer, imported=imported)
    return graph


def test_the_check_flags_a_package_or_kernel_module_outside_the_allowance() -> None:
    graph = synthetic(
        ("app.modules.notes.service", "requests"),
        ("app.modules.notes.domain", "pydantic"),
        ("app.modules.notes.probe", "boto3"),
        ("app.modules.notes.routes", "redis"),
        ("app.kernel.tenancy", "openai"),
        ("app.kernel.application.outbox", "app.kernel.infrastructure.db"),
        ("app.modules.notes.service", "app.kernel.request_context"),
        ("app.modules.notes.service", "app.settings"),
        ("app.modules.notes.ports", "app.kernel.http.auth"),
        ("app.modules.notes.routes", "app.bootstrap"),
    )
    flagged = {line.split(": ")[0] for line in violations(graph)}
    assert flagged == {
        "app.modules.notes.service (ports, services and probes) imports requests",
        "app.modules.notes.domain (domain) imports pydantic",
        "app.modules.notes.probe (ports, services and probes) imports boto3",
        "app.modules.notes.routes (routes) imports redis",
        "app.kernel.tenancy (kernel core) imports openai",
        "app.kernel.application.outbox (kernel core) imports app.kernel.infrastructure.db",
        "app.modules.notes.service (ports, services and probes) imports app.kernel.request_context",
        "app.modules.notes.service (ports, services and probes) imports app.settings",
        "app.modules.notes.ports (ports, services and probes) imports app.kernel.http.auth",
        "app.modules.notes.routes (routes) imports app.bootstrap",
    }


def test_the_check_allows_what_the_layers_need() -> None:
    graph = synthetic(
        ("app.modules.notes.ports", "pydantic"),
        ("app.modules.notes.service", "app.modules.notes.ports"),
        ("app.modules.notes.service", "app.kernel.errors"),
        ("app.modules.notes.service", "app.modules.billing.api"),
        ("app.modules.notes.service", "dataclasses"),
        ("app.modules.notes.service", "typing_extensions"),
        ("app.modules.notes.domain", "dataclasses"),
        ("app.modules.notes.routes", "fastapi"),
        ("app.modules.notes.routes", "app.kernel.http.auth"),
        ("app.modules.notes.routes", "app.kernel.request_context"),
        ("app.modules.notes.adapters", "requests"),  # adapters may use any SDK
        ("app.kernel.tenancy", "app.kernel.errors"),
        ("app.kernel.application.rate_limit", "uuid"),
        ("app.kernel.infrastructure.db", "psycopg"),  # infrastructure is not kernel core
    )
    assert violations(graph) == []
