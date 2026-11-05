"""Sending email (DEC-015): the checks every email passes before any provider sees it.

Call ``send`` from an outbox handler (ARCH-15), with an idempotency key made of the handler's name and the
event's ID (``f"myfeature.welcome_email:{event.id}"``): a handler runs at least once (every handler of an event
runs again when one of them fails), and Resend sends one email per key within 24 hours. Event payloads hold no
address (internal IDs only), so the handler looks the recipient up when it runs. Dispatch runs on threads of
its own, so a slow provider holds no request thread. A request that wants an email records an event instead of
sending inline: a send from a request holds a request thread until Resend answers, and
``reserved_request_threads`` does not count it (the bound below still caps it).
"""

from __future__ import annotations

import threading

from app.kernel.errors import InvalidRequestError, NotConfiguredError, RateLimitedError
from app.modules.email.domain import DomainReport, EmailMessage, RecipientPolicy, domain_of, message_problems
from app.modules.email.ports import EmailSender


class EmailService:
    def __init__(
        self,
        sender: EmailSender | None,
        *,
        from_address: str | None,
        sender_address: str | None,
        policy: RecipientPolicy,
        max_concurrent_sends: int = 2,
    ) -> None:
        self._sender = sender
        self._from = from_address
        self._sender_address = sender_address
        self._policy = policy
        self._in_flight = threading.BoundedSemaphore(max_concurrent_sends)

    @property
    def configured(self) -> bool:
        """False with EMAIL_MODE=disabled: ``send`` refuses, and nothing is called."""
        return self._sender is not None

    @property
    def sender_domain(self) -> str | None:
        return domain_of(self._sender_address) if self._sender_address else None

    def send(self, message: EmailMessage, *, idempotency_key: str) -> str:
        """Send ``message`` from EMAIL_FROM and return the provider's ID of the email.

        Refused before any call: a message Resend would refuse (``message_problems``), and in sandbox mode a
        recipient outside Resend's test addresses and EMAIL_SANDBOX_RECIPIENTS. No error names an address.
        """
        sender = self._require()
        if problems := message_problems(message, idempotency_key):
            raise InvalidRequestError("The email cannot be sent: " + "; ".join(problems) + ".")
        refused = sum(1 for address in message.to if not self._policy.permits(address))
        if refused:
            raise InvalidRequestError(
                f"EMAIL_MODE=sandbox sends only to Resend's test addresses (@resend.dev) and EMAIL_SANDBOX_RECIPIENTS: "
                f"{refused} of {len(message.to)} recipients are outside them."
            )
        # A per-instance bound on sends in flight: past it, the caller gets a short 429 and an outbox handler a retry.
        if not self._in_flight.acquire(blocking=False):
            raise RateLimitedError(retry_after_seconds=1)
        try:
            return sender.send(self._from or "", message, idempotency_key=idempotency_key)
        finally:
            self._in_flight.release()

    def domains(self) -> DomainReport:
        return self._require().domains()

    def _require(self) -> EmailSender:
        if self._sender is None:
            raise NotConfiguredError("Email is off in this environment (EMAIL_MODE=disabled).")
        return self._sender
