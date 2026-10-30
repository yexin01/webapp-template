"""Exported configuration never reaches the tests: tests/conftest.py removes it for the whole session."""

from __future__ import annotations

import pytest
from pydantic import AliasChoices, AliasPath
from pydantic_settings import BaseSettings

from app.kernel.infrastructure.config import DatabaseSettings
from app.settings import Settings
from tests.conftest import _is_settings_variable


def environment_names(settings: type[BaseSettings]) -> set[str]:
    """Every environment variable ``settings`` may read: each field's name and each name its alias accepts."""
    assert not settings.model_config.get("env_prefix"), "a prefix would change every name read"
    names: set[str] = set()
    for name, field in settings.model_fields.items():
        alias = field.validation_alias
        choices = alias.choices if isinstance(alias, AliasChoices) else [alias]
        names.add(name)
        names.update(str(c.path[0]) if isinstance(c, AliasPath) else c for c in choices if c)
    return names


@pytest.mark.parametrize("settings", [Settings, DatabaseSettings])
def test_every_variable_settings_read_is_kept_out_of_the_tests(settings: type[BaseSettings]) -> None:
    # The settings classes match environment names in any case, so a lower-case export counts too.
    reached = sorted(
        variant
        for name in environment_names(settings)
        for variant in (name.upper(), name.lower())
        if not _is_settings_variable(variant)
    )
    assert not reached, f"tests/conftest.py lets these exported variables reach the tests: {reached}"
