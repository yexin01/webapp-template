"""Email rules: what a message may hold, who may receive it, and what Resend reports of sending domains (DEC-015).

Standard library only. The limits are Resend's own
(https://resend.com/docs/api-reference/emails/send-email, read 2026-11-04).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

# Resend's test inbox for a delivered email: it reaches no person and costs the sender no reputation
# (https://resend.com/docs/dashboard/emails/send-test-emails). A label after "+" is allowed.
TEST_RECIPIENT = "delivered@resend.dev"
# Resend's own domain, for testing only: its test inboxes, and the sender a new account may use
# (onboarding@resend.dev), which delivers only to the account's own address
# (https://resend.com/docs/knowledge-base/403-error-resend-dev-domain, read 2026-11-04).
RESEND_TEST_DOMAIN = "resend.dev"

MAX_RECIPIENTS = 50
MAX_IDEMPOTENCY_KEY_LENGTH = 256
MAX_SUBJECT_LENGTH = 998  # one header line (RFC 5322)

# What Resend calls a domain that may send.
VERIFIED = "verified"

# One address: a local part without spaces, brackets, quotes, commas or semicolons, "@", and a dotted domain of
# letters, digits and hyphens. Deliberately narrower than RFC 5322: what passes here, Resend accepts.
_ADDRESS = re.compile(
    r"[^\s@<>,;\"()\[\]\\]{1,64}@(?P<domain>(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63})"
)
# "Name <address>": the form Resend accepts for a sender with a display name.
_NAMED = re.compile(r"(?P<name>[^<>\"\r\n]*)<(?P<address>[^<>\s]+)>")
# Resend's rule for a tag's name and value: ASCII letters, digits, underscores and dashes, at most 256 of them.
_TAG_PART = re.compile(r"[A-Za-z0-9_-]{1,256}")


def is_address(value: str) -> bool:
    return _ADDRESS.fullmatch(value) is not None


def sender_address(sender: str) -> str | None:
    """The address of ``noreply@example.com`` or ``Example <noreply@example.com>``; ``None`` for anything else."""
    text = sender.strip()
    named = _NAMED.fullmatch(text)
    candidate = named["address"] if named else text
    return candidate if is_address(candidate) else None


def domain_of(address: str) -> str:
    return address.rpartition("@")[2].lower()


@dataclass(frozen=True, slots=True)
class EmailMessage:
    """One email. ``text`` is required, so every email has a part that any client can show."""

    to: tuple[str, ...]
    subject: str
    text: str
    html: str | None = None
    reply_to: str | None = None
    tags: tuple[tuple[str, str], ...] = ()


def message_problems(message: EmailMessage, idempotency_key: str) -> list[str]:
    """What makes ``message`` unsendable, without repeating any address or text of it."""
    problems: list[str] = []
    if not 1 <= len(message.to) <= MAX_RECIPIENTS:
        problems.append(f"an email has 1 to {MAX_RECIPIENTS} recipients")
    if any(not is_address(address) for address in message.to):
        problems.append("a recipient is not an email address")
    if message.reply_to is not None and not is_address(message.reply_to):
        problems.append("the reply-to address is not an email address")
    if (
        not message.subject.strip()
        or len(message.subject) > MAX_SUBJECT_LENGTH
        or any(c in message.subject for c in "\r\n")
    ):
        problems.append(f"the subject is one line of 1 to {MAX_SUBJECT_LENGTH} characters")
    if not message.text.strip():
        problems.append("the plain-text part is empty")
    if any(not (_TAG_PART.fullmatch(name) and _TAG_PART.fullmatch(value)) for name, value in message.tags):
        problems.append("a tag's name and value are 1 to 256 ASCII letters, digits, underscores or dashes")
    if not 1 <= len(idempotency_key) <= MAX_IDEMPOTENCY_KEY_LENGTH:
        problems.append(f"the idempotency key is 1 to {MAX_IDEMPOTENCY_KEY_LENGTH} characters")
    return problems


@dataclass(frozen=True, slots=True)
class RecipientPolicy:
    """Who may receive email in this mode.

    ``allowed`` is ``None`` when anyone may (live and mock). In sandbox mode it holds the addresses and the
    ``@domain`` entries of EMAIL_SANDBOX_RECIPIENTS and, always, Resend's test domain: a preview or a laptop
    that holds a copy of real data never emails the people in it by mistake.
    """

    allowed: frozenset[str] | None = None

    @classmethod
    def sandbox(cls, entries: Iterable[str]) -> RecipientPolicy:
        listed = {entry.strip().lower() for entry in entries if entry.strip()}
        return cls(frozenset({f"@{RESEND_TEST_DOMAIN}", *listed}))

    def permits(self, address: str) -> bool:
        if self.allowed is None:
            return True
        value = address.strip().lower()
        return value in self.allowed or f"@{domain_of(value)}" in self.allowed


@dataclass(frozen=True, slots=True)
class SendingDomain:
    """One domain as Resend lists it. ``status`` is Resend's word (``verified``, ``pending``, ``failed``, ...)."""

    name: str
    status: str
    # Resend's ``capabilities.sending``: True when "enabled", False when "disabled", None when not reported.
    sending: bool | None = None


@dataclass(frozen=True, slots=True)
class DomainReport:
    """The account's domains, or, for a key that may only send, nothing: such a key cannot list them."""

    domains: tuple[SendingDomain, ...] = ()
    sending_only_key: bool = False

    def find(self, name: str) -> SendingDomain | None:
        wanted = name.lower()
        return next((d for d in self.domains if d.name.lower() == wanted), None)
