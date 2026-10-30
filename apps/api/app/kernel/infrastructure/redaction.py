"""Redaction of secrets and personal data from logs and telemetry (OBS-02, OBS-04).

Used by the log formatter and by the Sentry ``before_send`` hook. Redaction is
a safety net: code should avoid passing sensitive values to telemetry at all.
Every pattern runs in linear time, and each string is cut to
``MAX_TEXT_LENGTH`` characters first, so the work per string stays bounded
whatever a caller passes (OBS-06).
"""

from __future__ import annotations

import re
import string
from collections.abc import Callable, Mapping
from typing import Any

REDACTED = "[REDACTED]"
TRUNCATED = "[truncated]"
MAX_TEXT_LENGTH = 4096
_TOKEN_CHARACTERS = string.ascii_letters + string.digits + "_.%+/=~@-"

SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "cookie",
        "set-cookie",
        "x-api-key",
        "stripe-signature",
        "svix-signature",
        "webhook-signature",
        "password",
        "secret",
        "token",
        "access_token",
        "refresh_token",
        "api_key",
        "apikey",
        "client_secret",
        "session_id",
        "email",
        "phone",
        "prompt",
        "messages",
        "body",
        "data",
    }
)


_EMAIL_LOCAL = "[A-Za-z0-9._%+-]"
_EMAIL_DOMAIN = r"@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
# An email address, tried only from the start of a local-part run, with any glued on: "a@b.cc+d@e.ff".
_EMAIL = re.compile(rf"(?<!{_EMAIL_LOCAL}){_EMAIL_LOCAL}+{_EMAIL_DOMAIN}(?:[0-9._%+-]{_EMAIL_LOCAL}*{_EMAIL_DOMAIN})*")
# Query parameters holding credentials, one-time tokens, or session IDs: common token names, and every one
# apps/web/src/lib/redact.ts lists (Clerk's tickets and handshakes, redirect targets that can embed them).
_SENSITIVE_QUERY = re.compile(
    r"(?i)([?&](?:token|access_token|id_token|code|session_id|key|api_key|secret|signature|ticket"
    r"|redirect_url|__clerk[a-z_]*)=)[^&\s\"']+"
)


def _dsn_password(match: re.Match[str]) -> str:
    """A DSN and the rest of its word, with the password hidden up to the word's last "@" (a password can hold one).

    That "@" can belong to something written after the DSN, such as an
    address after a DSN without a password. So nothing after it is kept when
    the password holds another "@", and an address or a query value running
    across it goes whole, as the patterns that run after this one take it.
    """
    word = match[2]
    password, at, rest = word.rpartition("@")
    if not (at and password):
        return match[0]
    if "@" in password:
        return f"{match[1]}{REDACTED}"
    at_index = len(password)
    across = [m.end() for p in (_EMAIL, _SENSITIVE_QUERY) for m in p.finditer(word) if m.start() < at_index < m.end()]
    return match[1] + REDACTED + (word[max(across) :] if across else f"@{rest}")


# Each pattern runs in linear time: no long run of characters is rescanned from every position in it.
_PATTERNS: tuple[tuple[re.Pattern[str], str | Callable[[re.Match[str]], str]], ...] = (
    (re.compile(r"\b(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{6,}"), REDACTED),  # Stripe and Clerk keys
    (re.compile(r"\bwhsec_[A-Za-z0-9+/=]{6,}"), REDACTED),  # webhook secrets
    (re.compile(r"\bsk-[A-Za-z0-9_-]{10,}"), REDACTED),  # common LLM API key shape
    # A JWT ([\w-] is [A-Za-z0-9_-]): tried once per run of token characters, from its first "eyJ".
    (
        re.compile(
            r"(?<![\w-])(?=[\w-]+\.[\w-]{5,}\.[\w-]{5,})([\w-]*?)\beyJ[\w-]{5,}\.[\w-]{5,}\.[\w-]{5,}", re.ASCII
        ),
        rf"\g<1>{REDACTED}",
    ),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"), REDACTED),
    # A DSN password goes before email addresses, which would take its tail and leave its start ("Abc$" of
    # "Abc$123xyz@db.example.com"). Each match takes the rest of its word, so none is rescanned.
    (re.compile(r"(?i)(postgres(?:ql)?://[^:/\s]+:)(\S+)"), _dsn_password),
    (_EMAIL, REDACTED),
    (_SENSITIVE_QUERY, rf"\g<1>{REDACTED}"),
)


_DSN_SCHEME = re.compile(r"(?i)postgres(?:ql)?://")


def _without_a_cut_secret(head: str) -> str:
    """``head``, the start of a longer text, without a secret the cut may have split.

    A token cut in half could escape its pattern. A DSN's password can hold any
    character but whitespace, and once the "@" after it is cut off no pattern
    finds it, so the last word goes whole when it holds a DSN.
    """
    last_word = head.rsplit(maxsplit=1)[-1] if head and not head[-1].isspace() else ""
    if _DSN_SCHEME.search(last_word):
        return head[: len(head) - len(last_word)]
    return head.rstrip(_TOKEN_CHARACTERS)


def redact_text(value: str, *, max_length: int = MAX_TEXT_LENGTH) -> str:
    """Redacts secret- and email-shaped text. Text past ``max_length`` characters is dropped and marked."""
    if len(value) > max_length:
        value = _without_a_cut_secret(value[:max_length]) + TRUNCATED
    for pattern, replacement in _PATTERNS:
        value = pattern.sub(replacement, value)
    return value


def redact(value: Any, *, depth: int = 0, text: Callable[[str], str] = redact_text) -> Any:
    """Recursively redact sensitive keys and secret-looking strings, each string with ``text``."""
    if depth > 8:
        return REDACTED
    if isinstance(value, str):
        return text(value)
    if isinstance(value, Mapping):
        return {
            k: (
                REDACTED
                if isinstance(k, str) and k.lower() in SENSITIVE_KEYS
                else redact(v, depth=depth + 1, text=text)
            )
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact(v, depth=depth + 1, text=text) for v in value]
    return value


# The query and fragment of each path or URL that starts a word: "/p?x", "GET /p?x", "https://h/p#x".
_URL_QUERY = re.compile(r"(?<!\S)((?:/|[a-z][a-z0-9+.-]*://)[^\s?#]*)[?#]\S*", re.IGNORECASE)


def strip_query(url: str) -> str:
    """``url`` without its query string and fragment."""
    return url.partition("?")[0].partition("#")[0]


def strip_url_queries(text: str) -> str:
    """``text`` with each path or URL that starts a word cut to its origin and path."""
    return _URL_QUERY.sub(r"\1", text)
