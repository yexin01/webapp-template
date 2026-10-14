"""Diagnostics service paths that a correctly configured database cannot produce."""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

import pytest

from app.kernel.application.probes import DatabaseProbe
from app.kernel.application.rate_limit import RateLimiter
from app.kernel.diagnostics import IntegrationInfo, IntegrationKey, IntegrationMode, Outcome
from app.kernel.errors import NotFoundError
from app.kernel.ports import ConnectionDescription
from app.kernel.tenancy import ContextKind, Role, TenantContext
from app.modules.diagnostics.domain import OperatorPolicy
from app.modules.diagnostics.service import DiagnosticsService


class _Store:
    def __init__(self, description: ConnectionDescription) -> None:
        self.description = description

    def describe_connection(self) -> ConnectionDescription:
        return self.description

    def increment_counter(self, bucket_key: str, window_start: datetime) -> int:
        return 1


class _Tx:
    def __init__(self, store: _Store) -> None:
        self.repo = store


class _Transactions:
    def __init__(self, description: ConnectionDescription) -> None:
        self.tx = _Tx(_Store(description))

    @contextmanager
    def as_tenant(self, ctx: TenantContext) -> Iterator[Any]:
        yield self.tx

    @contextmanager
    def as_system(self) -> Iterator[Any]:
        yield self.tx


class _Clock:
    def now(self) -> datetime:
        return datetime(2026, 1, 1, tzinfo=UTC)


def service(description: ConnectionDescription, operators: OperatorPolicy | None = None) -> DiagnosticsService:
    tx: Any = _Transactions(description)
    info = IntegrationInfo(IntegrationKey.DATABASE, "PostgreSQL", IntegrationMode.LOCAL)
    return DiagnosticsService(
        enabled=True,
        operators=operators or OperatorPolicy(frozenset({"user_a"})),
        probes=[DatabaseProbe(info, tx)],
        rate_limiter=RateLimiter(tx, _Clock()),
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


def test_organization_context_is_not_found() -> None:
    org = TenantContext(uuid.uuid4(), "user_a", ContextKind.ORGANIZATION, uuid.uuid4(), "org_1", Role.ADMIN)
    with pytest.raises(NotFoundError):
        service(SAFE).integrations(org)


def test_non_operator_is_not_found() -> None:
    outsider = TenantContext(uuid.uuid4(), "user_b", ContextKind.PERSONAL)
    with pytest.raises(NotFoundError):
        service(SAFE).integrations(outsider)
    with pytest.raises(NotFoundError):
        service(SAFE).run(outsider, IntegrationKey.DATABASE)


def test_open_policy_admits_any_signed_in_user() -> None:
    anyone = TenantContext(uuid.uuid4(), "user_z", ContextKind.PERSONAL)
    open_policy = OperatorPolicy(frozenset(), allow_any_signed_in_user=True)
    assert service(SAFE, open_policy).run(anyone, IntegrationKey.DATABASE).outcome is Outcome.PASSED
