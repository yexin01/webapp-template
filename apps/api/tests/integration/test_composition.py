"""A module's contributed probe reaches the integrations page (spec §5.2, DEC-006).

Probes are collected from every module's contribution at composition. A hand-written list
dropped the probe of any module added later, so its integration was silently absent from
the page that DEC-006 verification relies on.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from app.kernel.diagnostics import IntegrationInfo, IntegrationKey, IntegrationMode, Verdict
from app.kernel.tenancy import TenantContext
from app.modules.llm.module import LLMModule
from app.modules.projects.module import ProjectsModule
from tests.conftest import Harness

pytestmark = pytest.mark.db

BASE = "/api/v1/diagnostics/integrations"


class StubProbe:
    info = IntegrationInfo(IntegrationKey.LLM, "Stub", IntegrationMode.MOCK)

    def check(self, ctx: TenantContext) -> Verdict:  # pragma: no cover - the page only lists it
        raise AssertionError("not run")


def test_a_probe_contributed_by_any_module_is_listed(
    make_harness: Callable[..., Harness], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The LLM module contributes no probe here and the projects module contributes one under the LLM key:
    the page lists what the projects module contributed, so the list is not written out in the composition root."""
    import app.bootstrap as bootstrap

    build_llm, build_projects = bootstrap.build_llm, bootstrap.build_projects

    def llm_without_probe(*args: Any, **kwargs: Any) -> LLMModule:
        module = build_llm(*args, **kwargs)
        module.contribution.probes.clear()
        return module

    def projects_with_probe(*args: Any, **kwargs: Any) -> ProjectsModule:
        module = build_projects(*args, **kwargs)
        module.contribution.probes.append(StubProbe())
        return module

    monkeypatch.setattr(bootstrap, "build_llm", llm_without_probe)
    monkeypatch.setattr(bootstrap, "build_projects", projects_with_probe)
    h = make_harness({"diagnostics_operator_ids": "user_ops"})
    response = h.client.get(BASE, headers=h.login("user_ops"))
    assert response.status_code == 200
    listed = {item["key"]: item["name"] for item in response.json()["integrations"]}
    assert listed["llm"] == "Stub"
    assert set(listed) == {"database", "identity", "billing", "llm", "analytics", "error_reporting"}
