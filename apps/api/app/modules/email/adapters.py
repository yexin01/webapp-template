"""Resend adapter and the mock (DEC-015). Resend's wire format and errors stay here (ARCH-04).

Facts from Resend's API reference (read 2026-11-04; not exercised against an account here): HTTPS only at
``https://api.resend.com``; ``Authorization: Bearer re_...``; a request without a ``User-Agent`` is refused
with 403; ``POST /emails`` answers ``{"id": ...}`` and takes an ``Idempotency-Key`` of up to 256 characters,
kept for 24 hours (a repeat with the same payload returns the first ID); ``GET /domains`` lists every domain
when no ``limit`` is given; a key with sending access only gets ``401 restricted_api_key`` there; 10 requests a
second per team, with ``retry-after`` on a 429.

Attempts share the timeout. A send is retried at most twice, and only when a repeat cannot send a second email:
the idempotency key makes Resend return the first email for a repeat, so a lost connection, a read timeout,
a rate limit with short guidance, a concurrent request with the same key, and a 5xx are retried; a refusal
never is. Logs carry the HTTP status and Resend's error name, never an address, a subject or a body.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from typing import Any

import httpx

from app.kernel.errors import DependencyUnavailableError, InvalidRequestError, ProviderRejectedError
from app.modules.email.domain import DomainReport, EmailMessage, SendingDomain

logger = logging.getLogger(__name__)

RESEND_API = "https://api.resend.com"
# Resend refuses a request without one (403). httpx's own default, set here so the rule is visible and tested;
# it names no project, so a template renamed with `pnpm init-template` has nothing left to rename here.
USER_AGENT = f"python-httpx/{httpx.__version__}"
_MAX_RETRIES = 2
_MAX_BACKOFF_SECONDS = 5.0
_RETRIED_STATUSES = frozenset({409, 429, 500, 502, 503, 504})
_QUOTA_ERRORS = frozenset({"daily_quota_exceeded", "monthly_quota_exceeded"})
_ERROR_NAME = re.compile(r"[a-z_]{1,64}")
# A domain's status as Resend words it (``verified``, ``not_started``, ...): the check quotes it to the operator.
_DOMAIN_STATUS = re.compile(r"[a-z_]{1,40}")


def _error_name(response: httpx.Response) -> str:
    """Resend's error name (``validation_error``, ``rate_limit_exceeded``, ...), bounded for the logs. Never its
    message, which can quote the account owner's address."""
    try:
        body = response.json()
    except ValueError:
        return "unknown"
    name = body.get("name") if isinstance(body, dict) else None
    return name if isinstance(name, str) and _ERROR_NAME.fullmatch(name) else "unknown"


def _retry_delay(response: httpx.Response, name: str, attempt: int) -> float | None:
    """How long to wait before repeating a request Resend did not complete; ``None`` when a repeat cannot help."""
    status = response.status_code
    if status not in _RETRIED_STATUSES or name in _QUOTA_ERRORS:
        return None
    if status == 409:
        # Only a request still in progress with the same key; another payload under it is the caller's mistake.
        return 0.5 * 2.0**attempt if name == "concurrent_idempotent_requests" else None
    retry_after = response.headers.get("retry-after", "")
    if retry_after.isascii() and retry_after.isdigit():
        return float(retry_after) if float(retry_after) <= _MAX_BACKOFF_SECONDS else None
    return min(0.5 * 2.0**attempt, _MAX_BACKOFF_SECONDS)


def _refusal(action: str, response: httpx.Response, name: str) -> Exception:
    """The kernel error for a request Resend refused or could not serve."""
    status = response.status_code
    logger.warning("resend_request_failed", extra={"action": action, "http_status": status, "error_name": name})
    # Every endpoint but sending answers an unknown key with 400 validation_error (Resend's error reference).
    bad_key = status in (401, 403) or (action == "domains" and status == 400 and name == "validation_error")
    if bad_key:
        return ProviderRejectedError(
            f"Resend refused the request ({name}): check RESEND_API_KEY, and that the domain of EMAIL_FROM is "
            "verified in Resend and allowed for this key."
        )
    if status == 429:
        if name in _QUOTA_ERRORS:
            return DependencyUnavailableError("The Resend sending quota of this account is used up.")
        return DependencyUnavailableError("Resend is rate limiting this account. Try again shortly.")
    if status == 409 and name == "invalid_idempotent_request":
        return InvalidRequestError("The idempotency key was already used for a different email.")
    if 400 <= status < 500 and status not in (404, 405, 409):
        return InvalidRequestError(f"Resend refused the email ({name}).")
    return DependencyUnavailableError("Email is temporarily unavailable.")


class ResendEmailSender:
    name = "resend"

    def __init__(
        self,
        *,
        api_key: str,
        timeout_seconds: float,
        base_url: str = RESEND_API,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._timeout = timeout_seconds
        self._sleep = sleep
        self._client = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}", "User-Agent": USER_AGENT},
            timeout=timeout_seconds,
            follow_redirects=False,
        )

    def close(self) -> None:
        self._client.close()

    def send(self, sender: str, message: EmailMessage, *, idempotency_key: str) -> str:
        body: dict[str, Any] = {
            "from": sender,
            "to": list(message.to),
            "subject": message.subject,
            "text": message.text,
        }
        if message.html is not None:
            body["html"] = message.html
        if message.reply_to is not None:
            body["reply_to"] = message.reply_to
        if message.tags:
            body["tags"] = [{"name": name, "value": value} for name, value in message.tags]
        response = self._request("send", "POST", "/emails", json=body, headers={"Idempotency-Key": idempotency_key})
        email_id = _json(response).get("id")
        if not isinstance(email_id, str) or not email_id:
            raise DependencyUnavailableError("Email is temporarily unavailable.")
        return email_id

    def domains(self) -> DomainReport:
        response = self._request("domains", "GET", "/domains", sending_only_ok=True)
        if response.status_code == 401:  # restricted_api_key: the key is valid and may only send
            return DomainReport(sending_only_key=True)
        rows = _json(response).get("data")
        found: list[SendingDomain] = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict) or not isinstance(row.get("name"), str):
                continue
            capabilities = row.get("capabilities")
            sending = capabilities.get("sending") if isinstance(capabilities, dict) else None
            status = row.get("status")
            found.append(
                SendingDomain(
                    name=row["name"],
                    status=status if isinstance(status, str) and _DOMAIN_STATUS.fullmatch(status) else "unknown",
                    sending=None if sending not in ("enabled", "disabled") else sending == "enabled",
                )
            )
        return DomainReport(tuple(found))

    def _request(
        self, action: str, method: str, path: str, *, sending_only_ok: bool = False, **kwargs: Any
    ) -> httpx.Response:
        deadline = time.monotonic() + self._timeout
        for attempt in range(_MAX_RETRIES + 1):
            delay: float | None
            refused: Exception | None = None
            try:
                remaining = max(deadline - time.monotonic(), 0.001)
                response = self._client.request(method, path, timeout=remaining, **kwargs)
            except httpx.TransportError as exc:
                # Nothing came back. The idempotency key makes a repeated send return the first email, and a
                # read repeats safely, so both are retried within the deadline.
                logger.warning("resend_request_failed", extra={"action": action, "error_type": type(exc).__name__})
                delay = min(0.5 * 2.0**attempt, _MAX_BACKOFF_SECONDS)
            else:
                if response.is_success:
                    return response
                name = _error_name(response)
                # A key was sent, and the other endpoints answer an unknown one with 400: a 401 here is a key that
                # may only send (restricted_api_key), also when the body names no error.
                if sending_only_ok and response.status_code == 401 and name in ("restricted_api_key", "unknown"):
                    return response
                delay = _retry_delay(response, name, attempt)
                if delay is None:
                    raise _refusal(action, response, name)
                refused = _refusal(action, response, name)
            if attempt == _MAX_RETRIES or time.monotonic() + delay >= deadline:
                raise refused or DependencyUnavailableError("Email is temporarily unavailable.")
            self._sleep(delay)
        raise DependencyUnavailableError("Email is temporarily unavailable.")  # pragma: no cover - the loop raises


def _json(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        body = None
    return body if isinstance(body, dict) else {}


class MockEmailSender:
    """In-memory sender for tests and mock mode: keeps the last 100 emails, and a repeated key returns its first ID.

    Nothing leaves the process, and the log line says how many recipients, never who.
    """

    name = "mock"
    _MAX_KEYS = 1000

    def __init__(self) -> None:
        self.sent: deque[tuple[str, EmailMessage, str]] = deque(maxlen=100)
        self._ids: OrderedDict[str, str] = OrderedDict()
        self._count = 0
        self._lock = threading.Lock()

    def send(self, sender: str, message: EmailMessage, *, idempotency_key: str) -> str:
        with self._lock:
            if idempotency_key in self._ids:
                return self._ids[idempotency_key]
            self._count += 1
            email_id = f"mock-email-{self._count}"
            self._ids[idempotency_key] = email_id
            if len(self._ids) > self._MAX_KEYS:
                self._ids.popitem(last=False)
            self.sent.append((sender, message, idempotency_key))
        logger.info("email_mock_sent", extra={"recipients": len(message.to)})
        return email_id

    def domains(self) -> DomainReport:
        return DomainReport()
