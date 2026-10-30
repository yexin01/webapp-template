"""Who may use diagnostics, asked the way GET /diagnostics/access asks it: through the module's own composition,
for every environment, without running anything. The HTTP side is tests/integration/test_diagnostics_access.py."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.kernel.http.auth import AuthDeps
from app.kernel.infrastructure.config import AppEnv
from app.kernel.tenancy import ContextKind, Role, TenantContext
from app.modules.diagnostics.module import build_diagnostics
from app.settings import Settings

OPERATOR = "user_ops"
OTHER = "user_a"


def may_use(
    app_env: AppEnv,
    user: str,
    *,
    enabled: bool | None = None,
    operators: tuple[str, ...] = (OPERATOR,),
    actor: str | None = None,
    organization: bool = False,
) -> bool:
    """The answer of a diagnostics service composed from these settings, as ``build_diagnostics`` composes it."""
    settings = Settings.model_construct(
        app_env=app_env, diagnostics_enabled=enabled, diagnostics_operator_ids=list(operators)
    )
    kernel = SimpleNamespace(rate_limiter=None, store=None)
    auth = AuthDeps(authenticate=lambda: None, personal=lambda: None, organization=lambda: None)
    service = build_diagnostics(settings, settings, kernel, auth, []).service  # type: ignore[arg-type]
    ctx = (
        TenantContext(uuid.uuid4(), user, ContextKind.ORGANIZATION, uuid.uuid4(), "org_1", Role.ADMIN)
        if organization
        else TenantContext(uuid.uuid4(), user, ContextKind.PERSONAL, actor_external_id=actor)
    )
    return service.may_use(ctx)


@pytest.mark.parametrize(
    ("app_env", "enabled", "operators", "user", "expected"),
    [
        # Unset means enabled everywhere except production, and an operator is on the list.
        (AppEnv.TEST, None, (OPERATOR,), OPERATOR, True),
        (AppEnv.PREVIEW, None, (OPERATOR,), OPERATOR, True),
        (AppEnv.LOCAL, None, (OPERATOR,), OPERATOR, True),
        (AppEnv.TEST, None, (OPERATOR,), OTHER, False),
        (AppEnv.PREVIEW, None, (OPERATOR,), OTHER, False),
        (AppEnv.LOCAL, None, (OPERATOR,), OTHER, False),
        # An empty list opens diagnostics to any signed-in user in local development, and nowhere else.
        (AppEnv.LOCAL, None, (), OTHER, True),
        (AppEnv.TEST, None, (), OTHER, False),
        (AppEnv.PREVIEW, None, (), OTHER, False),
        (AppEnv.PRODUCTION, True, (), OTHER, False),
        # Production: off unless DIAGNOSTICS_ENABLED=true, and then for operators only.
        (AppEnv.PRODUCTION, None, (OPERATOR,), OPERATOR, False),
        (AppEnv.PRODUCTION, False, (OPERATOR,), OPERATOR, False),
        (AppEnv.PRODUCTION, True, (OPERATOR,), OPERATOR, True),
        (AppEnv.PRODUCTION, True, (OPERATOR,), OTHER, False),
        # Switched off explicitly, it is off for everyone, whatever else would open it.
        (AppEnv.LOCAL, False, (), OTHER, False),
        (AppEnv.TEST, False, (OPERATOR,), OPERATOR, False),
    ],
)
def test_who_may_use_diagnostics(
    app_env: AppEnv, enabled: bool | None, operators: tuple[str, ...], user: str, expected: bool
) -> None:
    assert may_use(app_env, user, enabled=enabled, operators=operators) is expected


def test_a_session_someone_else_started_is_no_operator() -> None:
    assert may_use(AppEnv.TEST, OPERATOR, actor="user_staff") is False
    assert may_use(AppEnv.LOCAL, OTHER, operators=(), actor="user_staff") is False


def test_only_the_personal_context_may_use_diagnostics() -> None:
    assert may_use(AppEnv.TEST, OPERATOR, organization=True) is False
    assert may_use(AppEnv.LOCAL, OTHER, operators=(), organization=True) is False
