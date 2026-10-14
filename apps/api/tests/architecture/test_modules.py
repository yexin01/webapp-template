"""Architecture fitness tests for the modular monolith (ADR-0003).

Import-linter (``pyproject.toml``) enforces the layers inside every module, a
framework-free kernel, and acyclic modules. These tests add what it cannot
express: a module reaches another module only through that module's ``api``,
and every module has the same shape.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import pkgutil
from pathlib import Path

import grimp
import pytest

import app.modules
from app.kernel.authorization import Permission
from app.kernel.events import DomainEvent

MODULES_DIR = Path(app.modules.__file__).parent
MODULES = sorted(m.name for m in pkgutil.iter_modules([str(MODULES_DIR)]) if m.ispkg)


def _module_of(name: str) -> str | None:
    parts = name.split(".")
    return parts[2] if len(parts) > 2 and parts[:2] == ["app", "modules"] else None


def test_modules_are_discovered() -> None:
    assert {"billing", "diagnostics", "identity", "llm", "projects"} <= set(MODULES)


def test_modules_use_each_other_only_through_their_api() -> None:
    graph = grimp.build_graph("app")
    violations = []
    for importer in sorted(graph.modules):
        source = _module_of(importer)
        if source is None:
            continue
        for imported in graph.find_modules_directly_imported_by(importer):
            target = _module_of(imported)
            if target is None or target == source:
                continue
            if imported != f"app.modules.{target}.api":
                violations.append(f"{importer} -> {imported}")
    assert violations == [], "import another module only via its api.py"


@pytest.mark.parametrize("module", MODULES)
def test_every_module_has_the_required_shape(module: str) -> None:
    root = MODULES_DIR / module
    for required in ("__init__.py", "domain.py", "service.py", "module.py"):
        assert (root / required).exists(), f"{module} is missing {required}"
    package = importlib.import_module(f"app.modules.{module}")
    assert package.__doc__, f"app.modules.{module} needs a docstring stating its purpose"
    wiring = ast.parse((root / "module.py").read_text())
    builders = [n.name for n in wiring.body if isinstance(n, ast.FunctionDef) and n.name.startswith("build_")]
    assert builders, f"{module}/module.py must define a build_<module>() function"


def _domain_members() -> list[tuple[str, object]]:
    members = []
    for module in MODULES:
        domain = importlib.import_module(f"app.modules.{module}.domain")
        members += [(module, value) for _, value in inspect.getmembers(domain)]
    return members


def test_permission_names_are_unique() -> None:
    names = [value.name for _, value in _domain_members() if isinstance(value, Permission)]
    assert names, "expected module permissions"
    assert len(names) == len(set(names)), "permission names must be unique"


def test_event_types_are_unique_and_prefixed_by_their_module() -> None:
    events = [
        (module, value)
        for module, value in _domain_members()
        if inspect.isclass(value) and issubclass(value, DomainEvent) and value is not DomainEvent
    ]
    assert events, "expected at least one domain event"
    types = [event.event_type for _, event in events]
    assert len(types) == len(set(types))
    for module, event in events:
        assert event.event_type.startswith(f"{module}."), f"{event.__name__} must use the '{module}.' prefix"
