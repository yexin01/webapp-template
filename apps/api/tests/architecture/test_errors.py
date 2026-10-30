"""Every error the application defines is mapped and distinguishable (ADR-0003).

``DomainError`` refuses a subclass without a status at definition, so a module cannot define an
error that silently answers 500. These tests cover what that cannot: codes are well-formed and
unique, so a client can tell two outcomes apart.
"""

from __future__ import annotations

import importlib
import pkgutil
import re

import app.kernel
import app.modules
from app.kernel.errors import DomainError

CODE = re.compile(r"^[A-Z][A-Z0-9_]+$")


def _application_errors() -> list[type[DomainError]]:
    """Every error class defined in ``app``, found by importing all of its kernel and module packages."""
    for package in (app.kernel, app.modules):
        for found in pkgutil.walk_packages(package.__path__, f"{package.__name__}."):
            importlib.import_module(found.name)
    found_classes: list[type[DomainError]] = []
    pending = DomainError.__subclasses__()
    while pending:
        cls = pending.pop()
        found_classes.append(cls)
        pending += cls.__subclasses__()
    return sorted((c for c in found_classes if c.__module__.startswith("app.")), key=lambda c: c.__qualname__)


def test_the_application_defines_errors() -> None:
    names = {c.__name__ for c in _application_errors()}
    assert {"NotFoundError", "DependencyUnavailableError", "MembershipListTooLargeError"} <= names


def test_every_error_has_a_status_a_client_or_operator_can_act_on() -> None:
    wrong = [
        f"{c.__qualname__}: {c.status}" for c in _application_errors() if not (400 <= c.status < 500 or c.status == 503)
    ]
    assert wrong == []


def test_every_error_code_is_well_formed_and_defined_once() -> None:
    defined: dict[str, list[str]] = {}
    for cls in _application_errors():
        code = cls.__dict__.get("code")
        if code is not None:
            assert CODE.fullmatch(code), f"{cls.__qualname__}: code {code!r} must look like UPPER_SNAKE_CASE"
            defined.setdefault(code, []).append(cls.__qualname__)
    assert {code: names for code, names in defined.items() if len(names) > 1} == {}, "one code, one error class"


def test_no_error_answers_with_the_generic_code() -> None:
    """A subclass that forgot its ``code`` would answer INTERNAL_ERROR with a 4xx or 503 status."""
    assert [c.__qualname__ for c in _application_errors() if c.code == DomainError.code] == []
