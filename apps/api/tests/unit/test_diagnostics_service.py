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
from app.kernel.diagnostics import IntegrationInfo, IntegrationKey, IntegrationMode, Outcome, Verdict
from app.kernel.errors import NotFoundError
from app.kernel.ports import AuditEntry, ConnectionDescription
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
        self.audits: list[AuditEntry] = []

    def audit(self, entry: AuditEntry) -> None:
        self.audits.append(entry)


class _Transactions:
    def __init__(self, description: ConnectionDescription) -> None:
        self.tx = _Tx(_Store(description))
        self.open_blocks = 0

    @contextmanager
    def _block(self) -> Iterator[Any]:
        self.open_blocks += 1
        try:
            yield self.tx
        finally:
            self.open_blocks -= 1

    def as_tenant(self, ctx: TenantContext) -> Any:
        return self._block()

    def as_system(self) -> Any:
        return self._block()


class _Clock:
    def now(self) -> datetime:
        return datetime(2026, 1, 1, tzinfo=UTC)


class _ProviderProbe:
    """A provider check that records the transactions open, and the audits written, when it runs."""

    def __init__(
        self,
        tx: _Transactions,
        outcome: Outcome = Outcome.PASSED,
        *,
        error: Exception | None = None,
        mode: IntegrationMode = IntegrationMode.SANDBOX,
    ) -> None:
        self.info = IntegrationInfo(IntegrationKey.LLM, "LLM provider", mode)
        self.seen: list[tuple[int, int]] = []
        self._tx, self._outcome, self._error = tx, outcome, error

    def check(self, ctx: TenantContext) -> Verdict:
        self.seen.append((self._tx.open_blocks, len(self._tx.tx.audits)))
        if self._error is not None:
            raise self._error
        return Verdict(self._outcome, "Checked.")


def service(
    description: ConnectionDescription | None = None,
    operators: OperatorPolicy | None = None,
    *,
    tx: _Transactions | None = None,
    probes: tuple[Any, ...] = (),
    enabled: bool = True,
) -> DiagnosticsService:
    transactions: Any = tx or _Transactions(description or SAFE)
    info = IntegrationInfo(IntegrationKey.DATABASE, "PostgreSQL", IntegrationMode.LOCAL)
    return DiagnosticsService(
        enabled=enabled,
        operators=operators or OperatorPolicy(frozenset({"user_a"})),
        probes=[DatabaseProbe(info, transactions), *probes],
        rate_limiter=RateLimiter(transactions, _Clock()),
        transactions=transactions,
        runs_per_user_per_minute=10,
        runs_global_per_day=10,
    )


def audited(tx: _Transactions) -> list[tuple[Any, ...]]:
    return [
        (a.actor_user_id, a.org_id, a.action, a.target_type, a.target_id, a.outcome, a.request_id) for a in tx.tx.audits
    ]


PERSONAL = TenantContext(uuid.uuid4(), "user_a", ContextKind.PERSONAL)
OUTSIDER = TenantContext(uuid.uuid4(), "user_b", ContextKind.PERSONAL)
SAFE = ConnectionDescription(
    role="app_api",
    server_version="17.6",
    row_security=True,
    tenant_context_set=True,
    superuser=False,
    bypass_rls=False,
    unprotected_tables=(),
    owned_tables=(),
)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"role": "postgres"}, "queries run as postgres, expected app_api"),
        ({"row_security": False}, "row-level security is switched off"),
        ({"tenant_context_set": False}, "tenant context was not applied"),
        # DB-06: PostgreSQL skips row-level security for these, whatever row_security says.
        ({"superuser": True}, "app_api is a superuser"),
        ({"bypass_rls": True}, "app_api has BYPASSRLS"),
        ({"unprotected_tables": ("outbox", "projects")}, "not enabled and forced on outbox, projects"),
        ({"owned_tables": ("projects",)}, "app_api owns projects"),
    ],
)
def test_unsafe_database_session_fails(change: dict[str, Any], message: str) -> None:
    result = service(dataclasses.replace(SAFE, **change)).run(PERSONAL, IntegrationKey.DATABASE, None)
    assert result.outcome is Outcome.FAILED
    assert result.error_code == "unsafe_session"
    assert message in result.summary


def test_safe_session_passes() -> None:
    assert service(SAFE).run(PERSONAL, IntegrationKey.DATABASE, None).outcome is Outcome.PASSED


def test_organization_context_is_not_found() -> None:
    org = TenantContext(uuid.uuid4(), "user_a", ContextKind.ORGANIZATION, uuid.uuid4(), "org_1", Role.ADMIN)
    with pytest.raises(NotFoundError):
        service(SAFE).integrations(org)


def test_non_operator_is_not_found() -> None:
    with pytest.raises(NotFoundError):
        service(SAFE).integrations(OUTSIDER)
    with pytest.raises(NotFoundError):
        service(SAFE).run(OUTSIDER, IntegrationKey.DATABASE, None)


def test_open_policy_admits_any_signed_in_user() -> None:
    anyone = TenantContext(uuid.uuid4(), "user_z", ContextKind.PERSONAL)
    open_policy = OperatorPolicy(frozenset(), allow_any_signed_in_user=True)
    assert service(SAFE, open_policy).run(anyone, IntegrationKey.DATABASE, None).outcome is Outcome.PASSED


# -- Audit (ADM-01) -------------------------------------------------------------


@pytest.mark.parametrize(
    ("outcome", "audited_as"),
    [(Outcome.PASSED, "succeeded"), (Outcome.SENT, "succeeded"), (Outcome.FAILED, "failed")],
)
def test_a_run_is_audited_once_its_check_is_over(outcome: Outcome, audited_as: str) -> None:
    """In its own transaction afterwards: none is open while the provider is called."""
    tx = _Transactions(SAFE)
    probe = _ProviderProbe(tx, outcome)
    assert service(tx=tx, probes=(probe,)).run(PERSONAL, IntegrationKey.LLM, "req-1").outcome is outcome
    assert probe.seen == [(0, 0)]
    assert audited(tx) == [(PERSONAL.user_id, None, "diagnostics:run", "integration", "llm", audited_as, "req-1")]


def test_a_skipped_run_is_audited() -> None:
    tx = _Transactions(SAFE)
    probe = _ProviderProbe(tx, mode=IntegrationMode.MOCK)
    assert service(tx=tx, probes=(probe,)).run(PERSONAL, IntegrationKey.LLM, "req-1").outcome is Outcome.SKIPPED
    assert probe.seen == []
    assert [a.outcome for a in tx.tx.audits] == ["succeeded"]


def test_a_check_that_crashes_is_audited_as_failed() -> None:
    tx = _Transactions(SAFE)
    probe = _ProviderProbe(tx, error=ValueError("unexpected"))
    with pytest.raises(ValueError, match="unexpected"):
        service(tx=tx, probes=(probe,)).run(PERSONAL, IntegrationKey.LLM, "req-1")
    assert [a.outcome for a in tx.tx.audits] == ["failed"]


def test_a_refused_run_is_audited_as_denied() -> None:
    tx = _Transactions(SAFE)
    probe = _ProviderProbe(tx)
    with pytest.raises(NotFoundError):
        service(tx=tx, probes=(probe,)).run(OUTSIDER, IntegrationKey.LLM, "req-2")
    assert probe.seen == []
    assert audited(tx) == [(OUTSIDER.user_id, None, "diagnostics:run", "integration", "llm", "denied", "req-2")]


def test_nothing_is_audited_where_diagnostics_do_not_exist() -> None:
    """Disabled diagnostics and other contexts are a missing route, and reading the report runs nothing."""
    tx = _Transactions(SAFE)
    org = TenantContext(uuid.uuid4(), "user_a", ContextKind.ORGANIZATION, uuid.uuid4(), "org_1", Role.ADMIN)
    with pytest.raises(NotFoundError):
        service(tx=tx, enabled=False).run(PERSONAL, IntegrationKey.DATABASE, None)
    with pytest.raises(NotFoundError):
        service(tx=tx).run(org, IntegrationKey.DATABASE, None)
    with pytest.raises(NotFoundError):
        service(tx=tx).integrations(OUTSIDER)
    assert service(tx=tx).integrations(PERSONAL)
    assert tx.tx.audits == []


def test_two_probes_for_one_integration_are_refused_not_merged() -> None:
    """A dict keyed by integration used to keep the last probe and silently drop the other."""
    tx = _Transactions(SAFE)
    with pytest.raises(ValueError, match="Duplicate integration probe keys: llm"):
        service(tx=tx, probes=(_ProviderProbe(tx), _ProviderProbe(tx)))
