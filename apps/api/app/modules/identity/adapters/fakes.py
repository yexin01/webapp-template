"""In-process identity adapters for mock mode and tests. Never selected in production."""

from __future__ import annotations

import threading

from app.kernel.errors import AuthenticationError, DependencyUnavailableError
from app.kernel.tenancy import VerifiedIdentity
from app.modules.identity.domain import ExternalMembership


class StaticMembershipDirectory:
    """Membership directory backed by a dictionary (tests and mock mode)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._memberships: dict[str, list[ExternalMembership]] = {}
        self.available = True
        self.calls = 0

    def list_user_memberships(self, external_user_id: str) -> list[ExternalMembership]:
        with self._lock:
            self.calls += 1
            if not self.available:
                raise DependencyUnavailableError("Organization directory is unavailable.")
            return list(self._memberships.get(external_user_id, []))

    def set(self, external_user_id: str, memberships: list[ExternalMembership]) -> None:
        with self._lock:
            self._memberships[external_user_id] = list(memberships)


class RejectingVerifier:
    """Used when authentication is not configured: every token is rejected."""

    def verify(self, token: str) -> VerifiedIdentity:
        raise AuthenticationError()
