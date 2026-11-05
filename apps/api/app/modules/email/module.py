"""Wiring for the email module."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from app.kernel.diagnostics import IntegrationInfo, IntegrationKey, IntegrationMode
from app.kernel.infrastructure.config import Mode
from app.kernel.wiring import Contribution, pick
from app.modules.email.adapters import MockEmailSender, ResendEmailSender
from app.modules.email.domain import RecipientPolicy, sender_address
from app.modules.email.ports import EmailSender
from app.modules.email.probe import EmailProbe
from app.modules.email.routes import build_router
from app.modules.email.service import EmailService
from app.modules.email.settings import EmailSettings, sandbox_recipient_entries


@dataclass
class EmailModule:
    service: EmailService
    contribution: Contribution


def build_email(settings: EmailSettings, overrides: Mapping[str, Any]) -> EmailModule:
    """``email_test_sends`` (an override) is False for `pnpm check:setup` without --send-test-events: the check
    then reads the sending domain and sends nothing."""
    sender: EmailSender | None = pick(overrides, "email_sender", lambda: _sender(settings))
    closers: list[Callable[[], None]] = []
    if isinstance(sender, ResendEmailSender):
        closers.append(sender.close)
    service = EmailService(
        sender,
        from_address=settings.email_from,
        sender_address=sender_address(settings.email_from) if settings.email_from else None,
        policy=_policy(settings),
        max_concurrent_sends=settings.email_max_concurrent_sends,
    )
    send_test_email: bool = pick(overrides, "email_test_sends", lambda: True)
    contribution = Contribution(
        routers=[build_router()],
        probes=[EmailProbe(describe(settings), service, send_test_email=send_test_email)],
        closers=closers,
    )
    return EmailModule(service, contribution)


def _sender(settings: EmailSettings) -> EmailSender | None:
    if settings.email_mode is Mode.DISABLED:
        return None
    if settings.email_mode is Mode.MOCK or settings.resend_api_key is None:
        return MockEmailSender()
    return ResendEmailSender(
        api_key=settings.resend_api_key.get_secret_value(),
        timeout_seconds=settings.email_timeout_seconds,
    )


def _policy(settings: EmailSettings) -> RecipientPolicy:
    if settings.email_mode is Mode.SANDBOX:
        return RecipientPolicy.sandbox(sandbox_recipient_entries(settings.email_sandbox_recipients))
    return RecipientPolicy()


def describe(settings: EmailSettings) -> IntegrationInfo:
    mode = IntegrationMode(settings.email_mode.value)
    if mode is IntegrationMode.DISABLED:
        return IntegrationInfo(IntegrationKey.EMAIL, "Resend", mode, ("Disabled: no email is sent",))
    if mode is IntegrationMode.MOCK:
        return IntegrationInfo(IntegrationKey.EMAIL, "Resend", mode, ("Mock sender: nothing leaves the process",))
    address = sender_address(settings.email_from) if settings.email_from else None
    details = [f"Sender domain: {address.rpartition('@')[2] if address else 'not set'}"]
    if mode is IntegrationMode.SANDBOX:
        extra = len(sandbox_recipient_entries(settings.email_sandbox_recipients))
        details.append(
            "Recipients: Resend's test addresses only"
            if extra == 0
            else f"Recipients: Resend's test addresses and {extra} EMAIL_SANDBOX_RECIPIENTS entries"
        )
    return IntegrationInfo(IntegrationKey.EMAIL, "Resend", mode, tuple(details))
