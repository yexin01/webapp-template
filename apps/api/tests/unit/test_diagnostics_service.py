"""Diagnostics service paths that a correctly configured database cannot produce."""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

import pytest

from app.application.diagnostics import (
    DiagnosticsService,
    IntegrationInfo,
    IntegrationKey,
    IntegrationMode,
    Outcome,
)
from app.application.llm import LLMService
from app.application.rate_limit import RateLimiter
from app.domain.catalog import PriceCatalog
from app.domain.errors import ForbiddenError
from app.domain.tenancy import ContextKind, Role, TenantContext
from app.infrastructure.fakes import StaticMembershipDirectory
from app.infrastructure.llm_providers import MockLLMProvider
from app.infrastructure.observability import NoopAnalytics, NoopErrorReporter
from app.ports import ConnectionDescription


class _Repo:
    def __init__(self, description: ConnectionDescription) -> None:
        self.description = description

    def describe_connection(self) -> ConnectionDescription:
        return self.description

    def increment_counter(self, bucket_key: str, window_start: datetime) -> int:
        return 1


class _UoW:
    def __init__(self, description: ConnectionDescription) -> None:
        self.repo = _Repo(description)

    @contextmanager
    def as_tenant(self, ctx: TenantContext) -> Iterator[Any]:
        yield self.repo

    @contextmanager
    def as_system(self) -> Iterator[Any]:
        yield self.repo


class _Clock:
    def now(self) -> datetime:
        return datetime(2026, 1, 1, tzinfo=UTC)


def service(description: ConnectionDescription) -> DiagnosticsService:
    uow: Any = _UoW(description)
    limiter = RateLimiter(uow, _Clock())
    return DiagnosticsService(
        enabled=True,
        integrations=[IntegrationInfo(IntegrationKey.DATABASE, "PostgreSQL", IntegrationMode.LOCAL)],
        uow=uow,
        directory=StaticMembershipDirectory(),
        billing_provider=None,
        catalog=PriceCatalog({}),
        analytics=NoopAnalytics(),
        error_reporter=NoopErrorReporter(),
        llm=LLMService(
            MockLLMProvider(), limiter, NoopAnalytics(), checks_per_user_per_minute=1, checks_global_per_day=1
        ),
        rate_limiter=limiter,
        runs_per_user_per_minute=10,
        runs_global_per_day=10,
    )


PERSONAL = TenantContext(uuid.uuid4(), "user_a", ContextKind.PERSONAL)
SAFE = ConnectionDescription(role="app_api", server_version="17.6", row_security=True, tenant_context_set=True)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"role": "postgres"}, "queries run as postgres, expected app_api"),
        ({"row_security": False}, "row-level security is switched off"),
        ({"tenant_context_set": False}, "tenant context was not applied"),
    ],
)
def test_unsafe_database_session_fails(change: dict[str, Any], message: str) -> None:
    result = service(dataclasses.replace(SAFE, **change)).run(PERSONAL, IntegrationKey.DATABASE)
    assert result.outcome is Outcome.FAILED
    assert result.error_code == "unsafe_session"
    assert message in result.summary


def test_safe_session_passes() -> None:
    assert service(SAFE).run(PERSONAL, IntegrationKey.DATABASE).outcome is Outcome.PASSED


def test_organization_context_is_forbidden() -> None:
    org = TenantContext(uuid.uuid4(), "user_a", ContextKind.ORGANIZATION, uuid.uuid4(), "org_1", Role.ADMIN)
    with pytest.raises(ForbiddenError):
        service(SAFE).integrations(org)
