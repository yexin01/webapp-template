"""Redaction of secrets and personal data from logs and telemetry (OBS-02, OBS-04).

Used by the log formatter and by the Sentry ``before_send`` hook. Redaction is
a safety net: code should avoid passing sensitive values to telemetry at all.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

REDACTED = "[REDACTED]"

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

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{6,}"), REDACTED),  # Stripe and Clerk keys
    (re.compile(r"\bwhsec_[A-Za-z0-9+/=]{6,}"), REDACTED),  # webhook secrets
    (re.compile(r"\bsk-[A-Za-z0-9_-]{10,}"), REDACTED),  # common LLM API key shape
    (re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}"), REDACTED),  # JWTs
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"), REDACTED),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), REDACTED),  # email addresses
    (re.compile(r"(?i)(postgres(?:ql)?://[^:/\s]+:)[^@\s]+(@)"), rf"\g<1>{REDACTED}\g<2>"),  # DSN passwords
    (
        re.compile(
            r"(?i)([?&](?:token|access_token|id_token|code|session_id|key|api_key|secret|signature)=)[^&\s\"']+"
        ),
        rf"\g<1>{REDACTED}",
    ),
)


def redact_text(value: str) -> str:
    for pattern, replacement in _PATTERNS:
        value = pattern.sub(replacement, value)
    return value


def redact(value: Any, *, depth: int = 0) -> Any:
    """Recursively redact sensitive keys and secret-looking strings."""
    if depth > 8:
        return REDACTED
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return {
            k: (REDACTED if isinstance(k, str) and k.lower() in SENSITIVE_KEYS else redact(v, depth=depth + 1))
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact(v, depth=depth + 1) for v in value]
    return value
