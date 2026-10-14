"""Integration probe contract (spec §5.2, DEC-006).

Each module that owns an external integration contributes an
``IntegrationProbe``. The diagnostics module collects the probes at composition
without importing their modules. A probe describes its configuration without
secrets, and runs a minimal live check.

A pass in ``sandbox`` mode is evidence of connectivity with test credentials
only. ``mock`` and ``disabled`` integrations are reported as skipped, never as
passed (TEST-04).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from app.kernel.tenancy import TenantContext


# The integration catalog. Add a key here when a module adds an integration.
class IntegrationKey(StrEnum):
    """An external service that diagnostics can describe and check."""

    DATABASE = "database"
    IDENTITY = "identity"
    BILLING = "billing"
    LLM = "llm"
    ANALYTICS = "analytics"
    ERROR_REPORTING = "error_reporting"


class IntegrationMode(StrEnum):
    LOCAL = "local"
    MOCK = "mock"
    SANDBOX = "sandbox"
    LIVE = "live"
    DISABLED = "disabled"


class Outcome(StrEnum):
    PASSED = "passed"  # the provider confirmed the request
    SENT = "sent"  # handed to the provider's SDK, but delivery is not confirmed
    FAILED = "failed"
    SKIPPED = "skipped"  # mock or disabled: nothing was contacted


@dataclass(frozen=True, slots=True)
class IntegrationInfo:
    """Static, non-secret description of one integration's configuration."""

    key: IntegrationKey
    name: str
    mode: IntegrationMode
    details: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Verdict:
    outcome: Outcome
    summary: str
    error_code: str | None = None
    reference: str | None = None


@dataclass(frozen=True, slots=True)
class CheckResult:
    key: IntegrationKey
    mode: IntegrationMode
    outcome: Outcome
    summary: str
    latency_ms: int = 0
    error_code: str | None = None
    reference: str | None = None


class IntegrationProbe(Protocol):
    @property
    def info(self) -> IntegrationInfo: ...

    def check(self, ctx: TenantContext) -> Verdict:
        """Run a minimal live check. Raise a ``DomainError`` or return a FAILED verdict on failure."""
        ...
