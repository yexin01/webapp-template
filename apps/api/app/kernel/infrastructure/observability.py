"""Structured logging, request correlation, Sentry, and PostHog (spec §11).

Telemetry never blocks or breaks request handling (OBS-06): every exporter is
wrapped so its failures are logged and swallowed.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import threading
import time
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import sentry_sdk
from posthog import Posthog

from app.kernel.infrastructure.config import CoreSettings, Mode
from app.kernel.infrastructure.redaction import (
    MAX_TEXT_LENGTH,
    REDACTED,
    SENSITIVE_KEYS,
    TRUNCATED,
    redact,
    redact_text,
    strip_query,
    strip_url_queries,
)
from app.kernel.request_context import request_id_var

_STANDARD_RECORD_FIELDS = frozenset(vars(logging.LogRecord("", 0, "", 0, "", None, None)).keys()) | {
    "message",
    "asctime",
}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


class JsonFormatter(logging.Formatter):
    """One JSON object per line; message and extra fields are redacted."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact_text(record.getMessage()),
        }
        request_id = request_id_var.get()
        if request_id:
            payload["request_id"] = request_id
        extras = {k: v for k, v in vars(record).items() if k not in _STANDARD_RECORD_FIELDS}
        if extras:
            payload.update(redact(extras))
        if record.exc_info and record.exc_info[0] is not None:
            payload["exc_type"] = record.exc_info[0].__name__
        return json.dumps(payload, default=str)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    # Third-party request logs can include URLs with identifiers; keep them quiet.
    for noisy in ("httpx", "httpcore", "stripe", "posthog", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Sentry
# ---------------------------------------------------------------------------


# The most span text one transaction's scrubbing reads: 32 strings at the cap, about 10 ms of redaction.
MAX_SPAN_TEXT = 32 * MAX_TEXT_LENGTH
# Span IDs and timestamps: the SDK fills them, never with caller text, and they must stay whole.
_SPAN_IDENTITY = frozenset({"trace_id", "span_id", "parent_span_id", "start_timestamp", "timestamp"})
# Span and breadcrumb data holding only a query string or a fragment, or a user field (events drop the user).
_DROPPED_DATA = re.compile(r"(?:http|url)\.(?:query|fragment)|user\..+")
# Span and breadcrumb data holding a URL or a request target.
_URL_DATA = frozenset({"url", "http.url", "http.target", "url.full", "url.path"})


def _scrub_trace_text(value: str, max_length: int = MAX_TEXT_LENGTH) -> str:
    """``value`` redacted, with each path or URL in it cut to its origin and path."""
    return strip_url_queries(redact_text(value, max_length=max_length))


class _SpanText:
    """Scrubs the text of one transaction's spans, reading at most ``MAX_SPAN_TEXT`` characters in all.

    It runs where the transaction ends, on the event loop, and the SDK sends up
    to 1,000 spans with text of any length. Text repeated across spans (an
    operation, a query run in a loop) is scrubbed once; new text past the
    budget becomes ``TRUNCATED``.
    """

    def __init__(self) -> None:
        self._budget = MAX_SPAN_TEXT
        self._scrubbed: dict[str, str] = {}

    def __call__(self, value: str) -> str:
        key = value[: MAX_TEXT_LENGTH + 1]  # all of it that redact_text reads
        if key not in self._scrubbed:
            max_length = min(MAX_TEXT_LENGTH, self._budget)
            self._scrubbed[key] = _scrub_trace_text(value, max_length) if max_length > 0 else TRUNCATED
            self._budget -= min(len(value), max_length)
        return self._scrubbed[key]


def _scrub_data(data: Any, text: Callable[[str], str] = _scrub_trace_text) -> Any:
    """Span or breadcrumb data: no query strings, fragments, or user fields, and all text scrubbed."""
    if isinstance(data, dict):
        data = {
            key: strip_query(value) if key in _URL_DATA and isinstance(value, str) else value
            for key, value in data.items()
            if not (isinstance(key, str) and _DROPPED_DATA.fullmatch(key))
        }
    return redact(data, text=text)


def _scrub_span(span: dict[str, Any], text: Callable[[str], str] = _scrub_trace_text) -> None:
    """Scrubs, in place, a span of a transaction or the trace context (root span) of an event."""
    for key, value in span.items():
        if key not in _SPAN_IDENTITY:
            span[key] = _scrub_data(value, text) if key == "data" else redact(value, text=text)


def _scrub_request(request: dict[str, Any]) -> None:
    headers = request.get("headers")
    if isinstance(headers, dict):
        request["headers"] = {
            k: (REDACTED if k.lower() in SENSITIVE_KEYS else redact_text(str(v))) for k, v in headers.items()
        }
    for key in ("cookies", "data", "env"):
        if key in request:
            request[key] = REDACTED
    # Like data_collection, never send a query string or fragment, which can hold tokens.
    for key in ("query_string", "fragment"):
        request.pop(key, None)
    if isinstance(request.get("url"), str):
        request["url"] = redact_text(strip_query(request["url"]))


def _scrub_common(event: Any, *, keep_trace_data: bool) -> None:
    """Scrubs what error events and transactions share, and tags the request ID.

    The trace context's ``data`` goes, like any other context's, unless
    ``keep_trace_data``: a transaction's is its root span's, scrubbed like a span's.
    """
    request_id = request_id_var.get()
    if request_id:
        # Lets an operator go from a user's X-Request-ID to the event (OBS-05).
        tags = event.get("tags")
        event["tags"] = {**(tags if isinstance(tags, dict) else {}), "request_id": request_id}
    if isinstance(event.get("transaction"), str):
        event["transaction"] = _scrub_trace_text(event["transaction"])
    request = event.get("request")
    if isinstance(request, dict):
        _scrub_request(request)
    event.pop("user", None)
    contexts = event.get("contexts")
    trace = contexts.pop("trace", None) if isinstance(contexts, dict) else None
    for key in ("extra", "contexts", "tags"):
        if key in event:
            event[key] = redact(event[key])
    if isinstance(trace, dict):
        if "data" in trace and not keep_trace_data:
            trace["data"] = REDACTED
        # Its sampling context, which a caller can set, becomes the envelope's trace header.
        _scrub_span(trace)
        event["contexts"]["trace"] = trace


def scrub_event(event: Any, _hint: Any) -> Any:
    """Sentry ``before_send``: remove secrets, bodies, and personal data; tag the request ID."""
    _scrub_common(event, keep_trace_data=False)
    for exc in (event.get("exception") or {}).get("values", []) or []:
        if isinstance(exc.get("value"), str):
            exc["value"] = redact_text(exc["value"])
        for frame in (exc.get("stacktrace") or {}).get("frames", []) or []:
            frame.pop("vars", None)
    if isinstance(event.get("message"), str):
        event["message"] = redact_text(event["message"])
    logentry = event.get("logentry")
    if isinstance(logentry, dict):
        # ``formatted`` is the log message with the call's arguments already filled in.
        for key in ("message", "formatted"):
            if isinstance(logentry.get(key), str):
                logentry[key] = redact_text(logentry[key])
        logentry.pop("params", None)
    return event


def scrub_transaction(event: Any, _hint: Any) -> Any:
    """Sentry ``before_send_transaction``: scrubs a transaction like an error event, and each of its spans.

    Paths and URLs in the transaction name, span descriptions, and span data
    keep only their origin and path; query strings, fragments, and user
    fields are dropped, and all other text is redacted. The spans' text is
    read up to ``MAX_SPAN_TEXT`` characters in all.
    """
    _scrub_common(event, keep_trace_data=True)
    text = _SpanText()
    for span in event.get("spans") or []:
        if isinstance(span, dict):
            _scrub_span(span, text)
    return event


def scrub_breadcrumb(crumb: Any, _hint: Any) -> Any:
    """Sentry ``before_breadcrumb``: redacts the message and data; HTTP data keeps no query string or fragment."""
    if isinstance(crumb.get("message"), str):
        crumb["message"] = redact_text(crumb["message"])
    if "data" in crumb:
        crumb["data"] = _scrub_data(crumb["data"])
    return crumb


def _never_sample(_sampling_context: Any) -> float:
    return 0.0


def sentry_options(settings: CoreSettings) -> dict[str, Any]:
    if settings.sentry_dsn is None:
        raise ValueError("SENTRY_DSN is required")
    return {
        "dsn": settings.sentry_dsn.get_secret_value(),
        "environment": settings.app_env.value,
        "release": settings.sentry_release,  # None: sentry-sdk derives one (SENTRY_RELEASE, git, CI)
        # Collect nothing personal or request-derived by default; the
        # before_send* scrubbers are a second line of defense.
        "data_collection": {
            "user_info": False,
            "cookies": {"mode": "off"},
            "http_headers": {"request": {"mode": "off"}},
            "http_bodies": [],
            "url_query_params": {"mode": "off"},
            "graphql": {"document": False, "variables": False},
            "gen_ai": {"inputs": False, "outputs": False},
            "database_query_data": False,
            "queues": False,
            "stack_frame_variables": False,
        },
        "traces_sample_rate": settings.sentry_traces_sample_rate,
        # At 0 the rate alone still traces each request whose caller sends a sampled trace; a sampler overrides
        # that. Tracing stays on, so each request still continues its caller's trace and its errors link to it.
        "traces_sampler": None if settings.sentry_traces_sample_rate else _never_sample,
        # Spans then travel only inside transactions, which before_send_transaction scrubs.
        "trace_lifecycle": "static",
        "before_send": scrub_event,
        "before_send_transaction": scrub_transaction,
        "before_breadcrumb": scrub_breadcrumb,
        "max_breadcrumbs": 50,
    }


def init_error_reporting(settings: CoreSettings) -> bool:
    if settings.error_reporting_mode not in (Mode.SANDBOX, Mode.LIVE) or settings.sentry_dsn is None:
        return False
    sentry_sdk.init(**sentry_options(settings))
    return True


class IntegrationTestError(Exception):
    """Synthetic error sent by the integrations diagnostics page."""


class SentryErrorReporter:
    """Sends a synthetic error through the configured Sentry client and scrubbers."""

    def __init__(self, flush_timeout_seconds: float = 5.0) -> None:
        self._flush_timeout = flush_timeout_seconds

    def send_test_event(self) -> str | None:
        if not sentry_sdk.is_initialized():
            return None
        try:
            raise IntegrationTestError("Synthetic error from the integrations diagnostics page")
        except IntegrationTestError as exc:
            event_id = sentry_sdk.capture_exception(exc, tags={"integration_test": "true"})
        sentry_sdk.flush(timeout=self._flush_timeout)
        return event_id


class NoopErrorReporter:
    def send_test_event(self) -> str | None:
        return None


# ---------------------------------------------------------------------------
# Product analytics
# ---------------------------------------------------------------------------

# OBS-03: only these events, with only these properties, are ever sent.
ANALYTICS_ALLOWLIST: Mapping[str, frozenset[str]] = {
    "project_created": frozenset({"context"}),
    "plan_changed": frozenset({"plan"}),
    "checkout_started": frozenset({"plan"}),
    "billing_portal_opened": frozenset(),
    "llm_check_run": frozenset({"ok", "provider"}),
    "integration_test_sent": frozenset({"source"}),
}

logger = logging.getLogger(__name__)

# Per process, at most 9 after-response dispatches (DATABASE_WORKER_POOL_MAX_SIZE - 1) and a drain send at once.
_SENDING_THREADS = 10


def _filter(event: str, properties: Mapping[str, str | int | bool]) -> dict[str, str | int | bool] | None:
    allowed = ANALYTICS_ALLOWLIST.get(event)
    if allowed is None:
        logger.warning("analytics_event_not_allowlisted", extra={"analytics_event": event})
        return None
    return {k: v for k, v in properties.items() if k in allowed}


class NoopAnalytics:
    def capture(
        self,
        user_id: UUID,
        event: str,
        properties: Mapping[str, str | int | bool],
        *,
        event_id: UUID | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        _filter(event, properties)

    def flush(self) -> bool:
        return True

    def shutdown(self) -> None:
        return None


@dataclass(frozen=True, slots=True)
class _Pending:
    """What a caller captured since its last flush."""

    # Whether PostHog accepted every event sent with an event ID; None: none was sent.
    accepted: bool | None = None
    # Whether the client refused a capture without an event ID.
    refused: bool = False


_NOTHING_PENDING = _Pending()


class PostHogAnalytics:
    """PostHog client identified by internal user ID only.

    A capture without an event ID (the request path) is queued and exported in
    batches by a background thread, so it never waits (OBS-06). A capture with
    one (an outbox handler's) is sent on its own before ``capture`` returns,
    waiting at most ``confirm_timeout_seconds``, so ``flush`` can confirm it
    without waiting on what other requests queued.

    While PostHog does not answer, those waits cannot pile up across handlers
    and the after-response dispatch slots they run in: once a send times out,
    the next ones are reported unconfirmed at once, without being sent, for
    ``cooldown_seconds`` (measured with ``clock``). Then one trial send goes
    through, and the cooldown ends once PostHog answers one in time. The outbox
    retries every event that was not confirmed.

    What ``flush`` confirms is kept per caller, in a context variable: each
    request, after-response dispatch, and job run has a context of its own, so
    what one captured never decides another's flush, even on a reused thread.
    """

    def __init__(
        self,
        *,
        api_key: str,
        host: str,
        flush_timeout_seconds: float = 5.0,
        confirm_timeout_seconds: float = 2.0,
        cooldown_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._flush_timeout = flush_timeout_seconds
        self._confirm_timeout = confirm_timeout_seconds
        self._cooldown = cooldown_seconds
        self._clock = clock
        self._failures = 0
        self._quiet_until: float | None = None  # guarded by _lock; None: sends go through
        self._lock = threading.Lock()
        self._pending: ContextVar[_Pending] = ContextVar(f"analytics_pending_{id(self)}", default=_NOTHING_PENDING)
        self._client = Posthog(
            api_key,
            host=host,
            disable_geoip=True,
            max_retries=1,
            timeout=3,
            max_queue_size=1_000,
            on_error=self._on_error,
        )
        # Longer than the confirm timeout, so a PostHog that does not answer always shows as a send that timed out.
        self._sender = Posthog(
            api_key, host=host, disable_geoip=True, sync_mode=True, timeout=2 * confirm_timeout_seconds
        )
        # The SDK's timeout bounds one connection attempt, not a send: it retries, and tries every address.
        self._sending = ThreadPoolExecutor(max_workers=_SENDING_THREADS, thread_name_prefix="analytics-send")

    def _on_error(self, error: Exception, _items: object) -> None:
        with self._lock:
            self._failures += 1
        logger.warning("analytics_export_failed", extra={"error_type": type(error).__name__})

    def capture(
        self,
        user_id: UUID,
        event: str,
        properties: Mapping[str, str | int | bool],
        *,
        event_id: UUID | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        filtered = _filter(event, properties)
        pending = self._pending.get()
        if event_id is not None:
            # An event dropped by the allowlist has nothing to confirm.
            accepted = filtered is None or self._send(user_id, event, filtered, event_id, occurred_at)
            self._pending.set(replace(pending, accepted=accepted and pending.accepted is not False))
        elif filtered is not None and not self._queue(user_id, event, filtered, occurred_at):
            self._pending.set(replace(pending, refused=True))

    def _queue(
        self, user_id: UUID, event: str, properties: dict[str, str | int | bool], occurred_at: datetime | None
    ) -> bool:
        """Queue the event for the background export. False if the client refused it."""
        try:
            # A timestamp of None means the current time.
            return (
                self._client.capture(event, distinct_id=str(user_id), properties=properties, timestamp=occurred_at)
                is not None
            )
        except Exception:  # noqa: BLE001 - telemetry must never break requests
            logger.warning("analytics_capture_failed")
            return False

    def _send(
        self,
        user_id: UUID,
        event: str,
        properties: dict[str, str | int | bool],
        event_id: UUID,
        occurred_at: datetime | None,
    ) -> bool:
        """Send the event now, on its own. True once PostHog accepted it within the confirm timeout."""
        if not self._may_send():
            return False
        try:
            sending = self._sending.submit(
                self._sender.capture,
                event,
                distinct_id=str(user_id),
                properties=properties,
                uuid=str(event_id),
                timestamp=occurred_at,
            )
        except RuntimeError:  # shut down
            logger.warning("analytics_capture_failed")
            return False
        try:
            # The client returns None for an event it did not send or PostHog did not accept.
            accepted = sending.result(timeout=self._confirm_timeout) is not None
        except TimeoutError:
            sending.cancel()  # if it has not started, it never will
            with self._lock:
                self._quiet_until = self._clock() + self._cooldown
            logger.warning(
                "analytics_send_unconfirmed",
                extra={"confirm_timeout_seconds": self._confirm_timeout, "cooldown_seconds": self._cooldown},
            )
            return False
        except Exception:  # noqa: BLE001
            logger.warning("analytics_capture_failed")
            accepted = False
        with self._lock:
            self._quiet_until = None  # answered in time, accepted or not
        return accepted

    def _may_send(self) -> bool:
        """False during a cooldown. Once it is over, one trial send at a time goes through."""
        with self._lock:
            if self._quiet_until is None:
                return True
            now = self._clock()
            if now < self._quiet_until:
                return False
            self._quiet_until = now + self._cooldown  # the others keep failing fast while the trial waits
            return True

    def flush(self) -> bool:
        """Confirm what the caller captured since its last flush.

        After captures with an event ID (each sent, or given up, before
        ``capture`` returned): True only if PostHog accepted every one. It waits
        on nothing else, so an outbox handler's confirmation never depends on
        what other requests queued.

        Otherwise it exports the shared queue now: True only if PostHog accepted
        everything queued, within the flush timeout. The client's flush returns
        quietly when its timeout runs out with events still unsent, and a slow
        host reports failure (``on_error``) only after its retries, so a
        timed-out flush confirms nothing. Events other requests keep queueing
        can stop the queue from draining in time, which also returns False,
        while events queued once it drained do not count. It also returns False
        if the client refused one of the caller's captures (its queue was full
        or closed): that event is never exported.
        """
        pending = self._pending.get()
        self._pending.set(_NOTHING_PENDING)
        if pending.accepted is not None:
            return pending.accepted
        if pending.refused:
            return False
        with self._lock:
            before = self._failures
        deadline = time.monotonic() + self._flush_timeout
        try:
            self._client.flush(timeout_seconds=self._flush_timeout)
        except Exception:  # noqa: BLE001
            logger.warning("analytics_flush_failed")
            return False
        if self._client.queue.unfinished_tasks and time.monotonic() >= deadline:
            logger.warning("analytics_flush_unconfirmed", extra={"flush_timeout_seconds": self._flush_timeout})
            return False
        with self._lock:
            return self._failures == before

    def shutdown(self) -> None:
        # A send still in flight ends on its own; its handler has stopped waiting, or soon will.
        self._sending.shutdown(wait=False, cancel_futures=True)
        try:
            self._client.shutdown()
        except Exception:  # noqa: BLE001
            logger.warning("analytics_shutdown_failed")
