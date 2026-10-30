"""The two validators give one verdict on the variables both apps read (ENV-03).

The cases are shared with apps/web/src/lib/env-cases.test.ts. Each names the variables both apps must
report (none: both must accept the configuration). A fixture, not a runtime manifest: it catches the
day one validator learns a rule the other does not.
"""

from __future__ import annotations

import json
import re

import pytest

from app.kernel.infrastructure.config import REPO_ROOT, ConfigError
from app.settings import load_settings

assert REPO_ROOT is not None
DOCUMENT = json.loads((REPO_ROOT / "packages" / "contracts" / "config-cases.json").read_text())
NAME = re.compile(r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+")


@pytest.mark.parametrize("case", DOCUMENT["cases"], ids=lambda case: case["name"])
def test_the_api_names_what_the_web_app_names(case: dict[str, object], monkeypatch: pytest.MonkeyPatch) -> None:
    env = {**DOCUMENT["base"], **case["env"]}  # type: ignore[dict-item]
    for name, value in env.items():
        if value is not None:
            monkeypatch.setenv(name, value)
    expected = set(case["names"])  # type: ignore[call-overload]
    named: set[str] | None = None
    try:
        load_settings(_env_file=None, database_url="postgresql://unused:unused@127.0.0.1:1/unused")
    except ConfigError as error:
        named = set(NAME.findall(str(error)))
    if not expected:
        assert named is None, f"accepted by the web app, refused by the API: {sorted(named or [])}"
    else:
        assert named is not None, f"refused by the web app, accepted by the API: {sorted(expected)}"
        assert expected <= named, f"the API does not name {sorted(expected - named)}"
