"""Email configuration (DEC-015: Resend)."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, SecretStr

from app.kernel.infrastructure.config import CoreSettings, Mode, mode_problems, secret

# Resend issues keys as "re_" and letters, digits and underscores (its API reference shows no other form).
_RESEND_KEY = re.compile(r"re_[A-Za-z0-9_]{8,}")
# The address forms EMAIL_FROM and EMAIL_SANDBOX_RECIPIENTS take. app/modules/email/domain.py parses senders at
# run time with the same grammar; tests/unit/test_email.py holds the two to one set of examples.
_DOMAIN = r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}"
_ADDRESS = rf"[^\s@<>,;\"()\[\]\\]{{1,64}}@{_DOMAIN}"
_SENDER = re.compile(rf"(?:{_ADDRESS}|[^<>\"\r\n]*<{_ADDRESS}>)")
_RECIPIENT_ENTRY = re.compile(rf"(?:{_ADDRESS}|@{_DOMAIN})")
# Resend's testing domain (onboarding@resend.dev): it delivers only to the Resend account's own address.
_RESEND_TEST_DOMAIN = "resend.dev"


def sandbox_recipient_entries(value: str) -> list[str]:
    return [entry.strip() for entry in value.split(",") if entry.strip()]


def _sender_domain(sender: str) -> str:
    """The domain of a valid EMAIL_FROM, in either form: the text after the last "@", without the closing ">"."""
    return sender.strip().removesuffix(">").rpartition("@")[2].lower()


class EmailSettings(BaseModel):
    email_mode: Mode = Mode.MOCK
    resend_api_key: SecretStr | None = None
    # "noreply@example.com" or "Example <noreply@example.com>", on a domain verified in Resend.
    email_from: str | None = None
    # Sandbox only: who may receive email besides Resend's test addresses, as addresses or @domains.
    email_sandbox_recipients: str = ""
    email_timeout_seconds: float = Field(default=10.0, gt=0, le=30)
    # Sends in flight at once per API instance. Each holds the thread it runs on until Resend answers.
    email_max_concurrent_sends: int = Field(default=2, ge=1, le=16)


def email_problems(s: EmailSettings, core: CoreSettings) -> list[str]:
    problems = mode_problems("EMAIL_MODE", s.email_mode, core.app_env)
    sending = s.email_mode in (Mode.SANDBOX, Mode.LIVE)
    key = secret(s.resend_api_key)
    if sending and not key:
        problems.append("RESEND_API_KEY is required when EMAIL_MODE is sandbox or live")
    elif key and not _RESEND_KEY.fullmatch(key):
        problems.append("RESEND_API_KEY must be a Resend API key (re_...)")
    if sending and not s.email_from:
        problems.append("EMAIL_FROM is required when EMAIL_MODE is sandbox or live")
    elif s.email_from and not _SENDER.fullmatch(s.email_from.strip()):
        problems.append(
            "EMAIL_FROM must be an address (noreply@example.com) or a name and an address "
            "(Example <noreply@example.com>)"
        )
    elif s.email_mode is Mode.LIVE and _sender_domain(s.email_from or "") == _RESEND_TEST_DOMAIN:
        # Resend sends from its testing domain only to the account's own address: no user would get an email.
        problems.append("EMAIL_FROM must be on a domain of yours when EMAIL_MODE=live, not on resend.dev")
    if any(not _RECIPIENT_ENTRY.fullmatch(e) for e in sandbox_recipient_entries(s.email_sandbox_recipients)):
        problems.append("EMAIL_SANDBOX_RECIPIENTS must list addresses or @domains, separated by commas")
    return problems
