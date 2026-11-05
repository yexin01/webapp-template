"""Email provider port. The adapters normalize the provider to this shape and its failures to kernel errors."""

from __future__ import annotations

from typing import Protocol

from app.modules.email.domain import DomainReport, EmailMessage


class EmailSender(Protocol):
    @property
    def name(self) -> str: ...

    def send(self, sender: str, message: EmailMessage, *, idempotency_key: str) -> str:
        """Send ``message`` from ``sender`` and return the provider's ID of it.

        A repeat with the same ``idempotency_key`` within a day sends nothing new and returns the same ID, so a
        caller may retry (an outbox handler keys it by its event). Raises ``InvalidRequestError`` for an email the
        provider refused, ``ProviderRejectedError`` for a key or a sending domain it refused, and
        ``DependencyUnavailableError`` for an outage, a rate limit, or a used-up quota.
        """
        ...

    def domains(self) -> DomainReport:
        """The account's sending domains and their status (diagnostics only)."""
        ...
