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
from typing import Any, Literal
from uuid import UUID

import sentry_sdk
from posthog import Posthog

from app.kernel.errors import DomainError
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
from app.kernel.ports import Analytics
from app.kernel.request_context import note_unsent_telemetry, request_id_var, request_telemetry_var

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
        telemetry = request_telemetry_var.get()
        if telemetry is not None:
            # Internal, pseudonymous IDs (OBS-04); a log line's own ``extra`` keys win below.
            if telemetry.user_id is not None:
                payload["user_id"] = str(telemetry.user_id)
            if telemetry.org_id is not None:
                payload["org_id"] = str(telemetry.org_id)
        extras = {k: v for k, v in vars(record).items() if k not in _STANDARD_RECORD_FIELDS}
        if "request_id" in extras:
            # An operator filters by the request's ID: a log call's own (a provider's, say) never replaces it.
            extras["extra_request_id"] = extras.pop("request_id")
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
# A path segment shaped like a provider's ID: a short lower-case prefix, then 10 or more letters and digits
# (user_2abc..., org_..., cus_..., price_..., and cs_test_... for a Checkout Session). Any such segment goes, so
# an ID of a kind nobody listed does not get through. The outgoing Clerk and Stripe calls carry their IDs in the path.
_ID_SEGMENT = re.compile(r"(?<=/)[a-z]{2,6}_(?:(?:test|live)_)?[A-Za-z0-9]{10,}(?![A-Za-z0-9_])")
# The scheme and host of an absolute URL.
_ORIGIN = re.compile(r"[a-z][a-z0-9+.-]*://[^/]*", re.IGNORECASE)


def _scrub_trace_text(value: str, max_length: int = MAX_TEXT_LENGTH) -> str:
    """``value`` redacted, with each path or URL in it cut to its origin and path, and each ID in a path replaced."""
    return _ID_SEGMENT.sub("{id}", strip_url_queries(redact_text(value, max_length=max_length)))


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


def _matched_route(event: Any) -> str | None:
    """The template of the route that handled the request (the SDK names the transaction after it), if one did."""
    info, name = event.get("transaction_info"), event.get("transaction")
    if isinstance(info, dict) and info.get("source") == "route" and isinstance(name, str) and name.startswith("/"):
        return name
    return None


def _scrub_request(request: dict[str, Any], route: str | None) -> None:
    """Scrubs a request in place. ``route`` is the template of the route that matched, if any."""
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
        url = strip_query(request["url"])
        if route is not None:
            # The path of a matched route is its template: the IDs the caller put in the path stay out.
            origin = _ORIGIN.match(url)
            url = (origin[0] if origin else "") + route
        request["url"] = _scrub_trace_text(url)


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
        telemetry = request_telemetry_var.get()
        if telemetry is not None:
            # Internal, pseudonymous IDs: which user and tenant an error affects (OBS-04). Never the ``user`` field.
            ids = {"user_id": telemetry.user_id, "org_id": telemetry.org_id}
            event["tags"].update({name: str(value) for name, value in ids.items() if value is not None})
    if isinstance(event.get("transaction"), str):
        event["transaction"] = _scrub_trace_text(event["transaction"])
    request = event.get("request")
    if isinstance(request, dict):
        _scrub_request(request, _matched_route(event))
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
    note_unsent_telemetry()  # the SDK sends in the background: the middleware flushes before the instance idles
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
        # No outgoing call (Clerk, Stripe, the LLM provider, PostHog) carries sentry-trace or baggage: they hold the
        # release, environment, trace ID and route names, and no provider has a use for them.
        "trace_propagation_targets": [],
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


class ErrorThrottle:
    """Lets one report of each key through per interval, and remembers at most ``max_keys`` keys (OBS-07)."""

    def __init__(self, interval_seconds: float, max_keys: int, clock: Callable[[], float] = time.monotonic) -> None:
        self._interval = interval_seconds
        self._max_keys = max_keys
        self._clock = clock
        self._last: dict[tuple[str, ...], float] = {}  # in the order of the last report; guarded by _lock
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self._last)

    def allow(self, key: tuple[str, ...]) -> bool:
        now = self._clock()
        with self._lock:
            last = self._last.pop(key, None)
            if last is not None and now - last < self._interval:
                self._last[key] = last
                return False
            if len(self._last) >= self._max_keys:
                for stale in [k for k, t in self._last.items() if now - t >= self._interval]:
                    del self._last[stale]
            if len(self._last) >= self._max_keys:
                del self._last[next(iter(self._last))]
            self._last[key] = now
            return True


_server_errors = ErrorThrottle(interval_seconds=60.0, max_keys=256)


def report_server_error(exc: DomainError, *, level: Literal["error", "warning"]) -> None:
    """Report a 5xx the API chose to Sentry, at most once a minute per cause on this instance (OBS-07).

    The error handler consumes a ``DomainError`` before Sentry's hooks look at it, and they report only
    an error with a ``status_code``. The event passes ``before_send`` like any other, and carries the
    request ID. This never raises and never waits for the network (OBS-06): the SDK sends in the background.
    """
    if not sentry_sdk.is_initialized():
        return
    cause = type(exc.__cause__).__name__ if exc.__cause__ is not None else ""
    # Public messages of these errors are constants, so a cause is told apart by its code and type.
    if not _server_errors.allow((exc.code, exc.public_message, cause)):
        return
    try:
        sentry_sdk.capture_exception(
            exc, level=level, tags={"error_code": exc.code}, fingerprint=["{{ default }}", exc.code, cause]
        )
    except Exception:  # noqa: BLE001 - telemetry must never break requests
        logger.warning("error_report_failed")


def flush_telemetry(analytics: Analytics, timeout_seconds: float) -> None:
    """Send now what this process queued for Sentry and PostHog. Never raises.

    For a serverless instance that can be frozen once its invocation ends: both clients
    send in the background, on a schedule (PostHog: every 5 s). The request middleware
    calls this from a thread, after the response, and stops waiting after its budget.
    """
    if sentry_sdk.is_initialized():
        try:
            sentry_sdk.flush(timeout=timeout_seconds)
        except Exception:  # noqa: BLE001 - telemetry must never break requests
            logger.warning("telemetry_flush_failed", extra={"service": "sentry"})
    try:
        analytics.flush()
    except Exception:  # noqa: BLE001
        logger.warning("telemetry_flush_failed", extra={"service": "posthog"})


# ---------------------------------------------------------------------------
# Product analytics
# ---------------------------------------------------------------------------

# OBS-03: only these events, with only these properties, are ever sent: the template's own, which docs/data-inventory.md
# lists, and the ones a module registers at wiring time (``AnalyticsEvents``, ``Contribution.analytics_events``).
ANALYTICS_ALLOWLIST: Mapping[str, frozenset[str]] = {
    "project_created": frozenset({"context"}),
    "plan_changed": frozenset({"plan"}),
    "checkout_started": frozenset({"plan"}),
    "billing_portal_opened": frozenset(),
    "llm_check_run": frozenset({"ok", "provider"}),
    "integration_test_sent": frozenset({"source"}),
}

# A property whose name, split on "_", holds one of these words is refused when its event is registered:
# user_email, ip_address, first_name, phone_number. A joined form (firstname, ipaddress) is listed too. This stops
# the obvious mistake of naming a property after personal data or a credential; the allowlist is what keeps a
# property off the wire. docs/architecture.md ("Adding an analytics event") says so to authors.
PERSONAL_DATA_WORDS = frozenset(
    {
        # who someone is, and how to reach them
        *("email", "phone", "mobile", "name", "firstname", "lastname", "fullname", "surname", "username", "nickname"),
        *("address", "street", "zip", "zipcode", "postcode", "postal", "ip", "ipaddress"),
        *("birth", "birthday", "birthdate", "dob", "ssn", "passport", "iban"),
        # credentials
        *("password", "passwd", "secret", "token", "cookie", "jwt", "apikey"),
        # what a user wrote
        *("prompt", "message", "messages", "body"),
    }
)

logger = logging.getLogger(__name__)

_ANALYTICS_NAME = re.compile(r"[a-z][a-z0-9_]{2,63}")


class AnalyticsEvents:
    """The events the API may send, and the properties each may carry (OBS-03).

    It starts with ``ANALYTICS_ALLOWLIST``. A module adds its own events with
    ``Contribution.analytics_events``, which the composition root registers at startup, so adding
    an event never edits the kernel. Registration refuses what the allowlist exists to prevent: a
    name another event has, a property with a word of ``PERSONAL_DATA_WORDS`` in its name or one of
    ``SENSITIVE_KEYS`` as its name, a name outside lower-case snake case. A refusal stops the
    application from starting.
    """

    def __init__(self, base: Mapping[str, frozenset[str]] = ANALYTICS_ALLOWLIST) -> None:
        self._events: dict[str, frozenset[str]] = dict(base)

    def register(self, events: Mapping[str, frozenset[str]]) -> None:
        added: dict[str, frozenset[str]] = {}
        for name, properties in events.items():
            problems = [f"event {name!r} is already registered"] if name in self._events else []
            names = [name, *properties]
            problems += [f"{n!r} is not lower-case snake case" for n in names if not _ANALYTICS_NAME.fullmatch(n)]
            problems += [
                f"property {p!r} is named like personal data or a secret"
                for p in properties
                if p in SENSITIVE_KEYS or PERSONAL_DATA_WORDS.intersection(p.lower().split("_"))
            ]
            if problems:
                raise ValueError(f"analytics event {name!r} cannot be registered: {'; '.join(problems)}")
            added[name] = frozenset(properties)
        self._events |= added

    def properties(self, event: str) -> frozenset[str] | None:
        """The properties ``event`` may carry; None if it is not allowed at all."""
        return self._events.get(event)


# Per process, at most 9 after-response dispatches (DATABASE_WORKER_POOL_MAX_SIZE - 1) and a drain send at once.
_SENDING_THREADS = 10


def _filter(
    event: str, properties: Mapping[str, str | int | bool], events: AnalyticsEvents | None = None
) -> dict[str, str | int | bool] | None:
    allowed = ANALYTICS_ALLOWLIST.get(event) if events is None else events.properties(event)
    if allowed is None:
        logger.warning("analytics_event_not_allowlisted", extra={"analytics_event": event})
        return None
    return {k: v for k, v in properties.items() if k in allowed}


class NoopAnalytics:
    def __init__(self, events: AnalyticsEvents | None = None) -> None:
        self._events = events or AnalyticsEvents()

    def capture(
        self,
        user_id: UUID,
        event: str,
        properties: Mapping[str, str | int | bool],
        *,
        event_id: UUID | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        _filter(event, properties, self._events)

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
        events: AnalyticsEvents | None = None,
    ) -> None:
        self._events = events or AnalyticsEvents()
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
        filtered = _filter(event, properties, self._events)
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
        note_unsent_telemetry()
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
