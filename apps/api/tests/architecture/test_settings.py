"""Settings composition (ENV-02 to ENV-05): modules add settings without changing each other's.

``Settings`` inherits the kernel's and every module's settings class. A field name two of them
declare is not an error to pydantic: the first class in the MRO wins, with its default and its
bounds, and the other module's silently stops being enforced.
"""

from __future__ import annotations

import ast
import inspect
from collections import defaultdict
from pathlib import Path

from pydantic import AliasChoices, BaseModel, Field

import app.modules
from app.settings import Settings

APP_DIR = Path(app.modules.__file__).parent.parent


def _declared(base: type[BaseModel]) -> set[str]:
    """The fields ``base`` declares itself, as opposed to inherits."""
    return {name for name in inspect.get_annotations(base) if name in base.model_fields}


def _names(base: type[BaseModel], field: str) -> set[str]:
    """The environment names a field reads: its own name, and its aliases."""
    alias = base.model_fields[field].validation_alias
    choices = list(alias.choices) if isinstance(alias, AliasChoices) else [alias]
    return {name.upper() for name in [field, *choices] if isinstance(name, str)}


def collisions(composed: type[BaseModel], package: str = "app.") -> dict[str, list[str]]:
    """Each environment name that more than one settings class (defined in ``package``) declares."""
    owners: dict[str, set[str]] = defaultdict(set)
    for base in composed.__mro__:
        if base is composed or not issubclass(base, BaseModel) or not base.__module__.startswith(package):
            continue
        for field in _declared(base):
            for name in _names(base, field):
                owners[name].add(f"{base.__name__}.{field}")
    return {name: sorted(found) for name, found in sorted(owners.items()) if len(found) > 1}


def test_the_application_composes_several_settings_classes() -> None:
    bases = {c.__name__ for c in Settings.__mro__ if c.__module__.startswith("app.")}
    assert {"CoreSettings", "IdentitySettings", "BillingSettings", "LLMSettings", "DiagnosticsSettings"} <= bases


def test_no_two_settings_classes_read_the_same_environment_variable() -> None:
    assert collisions(Settings) == {}, "a setting is declared by one settings class only"


def test_the_check_finds_a_field_two_classes_declare() -> None:
    class First(BaseModel):
        shared_limit: int = 8

    class Second(BaseModel):
        shared_limit: int = 100

    class Other(BaseModel):
        other_limit: int = 1

    class Composed(First, Second, Other):
        pass

    assert collisions(Composed, package="tests.") == {"SHARED_LIMIT": ["First.shared_limit", "Second.shared_limit"]}


def test_the_check_finds_an_alias_two_fields_read() -> None:
    class First(BaseModel):
        first_key: str | None = Field(default=None, validation_alias=AliasChoices("SECOND_KEY"))

    class Second(BaseModel):
        second_key: str | None = None

    class Composed(First, Second):
        pass

    assert collisions(Composed, package="tests.") == {"SECOND_KEY": ["First.first_key", "Second.second_key"]}


def _calls(tree: ast.AST) -> set[str]:
    return {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id.endswith("_problems")
    }


def test_every_modules_problems_function_runs_when_settings_load() -> None:
    """A ``*_problems`` function that ``Settings._validate`` does not call validates nothing."""
    run_by_settings = _calls(ast.parse((APP_DIR / "settings.py").read_text()))
    entry_points = {}
    for path in sorted((APP_DIR / "modules").glob("*/settings.py")):
        tree = ast.parse(path.read_text())
        defined = {n.name for n in tree.body if isinstance(n, ast.FunctionDef) and n.name.endswith("_problems")}
        # A helper that another function of the file calls is not an entry point.
        entry_points[path.parent.name] = defined - _calls(tree)
    assert any(entry_points.values()), "expected module settings with a problems function"
    missing = {
        module: sorted(names - run_by_settings) for module, names in entry_points.items() if names - run_by_settings
    }
    assert missing == {}, "add each to Settings._validate in app/settings.py"
