"""Structured logging, request correlation, Sentry, and PostHog (spec §11).

Telemetry never blocks or breaks request handling (OBS-06): every exporter is
wrapped so its failures are logged and swallowed.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import sentry_sdk
from posthog import Posthog

from app.kernel.infrastructure.config import CoreSettings, Mode
from app.kernel.infrastructure.redaction import REDACTED, SENSITIVE_KEYS, redact, redact_text
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


def _scrub_request(request: dict[str, Any]) -> None:
    headers = request.get("headers")
    if isinstance(headers, dict):
        request["headers"] = {
            k: (REDACTED if k.lower() in SENSITIVE_KEYS else redact_text(str(v))) for k, v in headers.items()
        }
    for key in ("cookies", "data", "env"):
        if key in request:
            request[key] = REDACTED
    if isinstance(request.get("query_string"), str):
        request["query_string"] = redact_text(request["query_string"])
    if isinstance(request.get("url"), str):
        request["url"] = redact_text(request["url"])


def scrub_event(event: Any, _hint: Any) -> Any:
    """Sentry ``before_send``: remove secrets, bodies, and personal data; tag the request ID."""
    request_id = request_id_var.get()
    if request_id:
        # Lets an operator go from a user's X-Request-ID to the event (OBS-05).
        tags = event.get("tags")
        event["tags"] = {**(tags if isinstance(tags, dict) else {}), "request_id": request_id}
    request = event.get("request")
    if isinstance(request, dict):
        _scrub_request(request)
    event.pop("user", None)
    for key in ("extra", "contexts", "tags"):
        if key in event:
            event[key] = redact(event[key])
    for exc in (event.get("exception") or {}).get("values", []) or []:
        if isinstance(exc.get("value"), str):
            exc["value"] = redact_text(exc["value"])
        for frame in (exc.get("stacktrace") or {}).get("frames", []) or []:
            frame.pop("vars", None)
    if isinstance(event.get("message"), str):
        event["message"] = redact_text(event["message"])
    logentry = event.get("logentry")
    if isinstance(logentry, dict) and isinstance(logentry.get("message"), str):
        logentry["message"] = redact_text(logentry["message"])
        logentry.pop("params", None)
    return event


def scrub_breadcrumb(crumb: Any, _hint: Any) -> Any:
    if isinstance(crumb.get("message"), str):
        crumb["message"] = redact_text(crumb["message"])
    if "data" in crumb:
        crumb["data"] = redact(crumb["data"])
    return crumb


def sentry_options(settings: CoreSettings) -> dict[str, Any]:
    if settings.sentry_dsn is None:
        raise ValueError("SENTRY_DSN is required")
    return {
        "dsn": settings.sentry_dsn.get_secret_value(),
        "environment": settings.app_env.value,
        "release": settings.sentry_release,  # None: sentry-sdk derives one (SENTRY_RELEASE, git, CI)
        # Collect nothing personal or request-derived by default; the
        # before_send scrubber is a second line of defense.
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
        "before_send": scrub_event,
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


def _filter(event: str, properties: Mapping[str, str | int | bool]) -> dict[str, str | int | bool] | None:
    allowed = ANALYTICS_ALLOWLIST.get(event)
    if allowed is None:
        logger.warning("analytics_event_not_allowlisted", extra={"analytics_event": event})
        return None
    return {k: v for k, v in properties.items() if k in allowed}


class NoopAnalytics:
    def capture(self, user_id: UUID, event: str, properties: Mapping[str, str | int | bool]) -> None:
        _filter(event, properties)

    def flush(self) -> bool:
        return True

    def shutdown(self) -> None:
        return None


class PostHogAnalytics:
    """Batched, non-blocking PostHog client identified by internal user ID only."""

    def __init__(self, *, api_key: str, host: str, flush_timeout_seconds: float = 5.0) -> None:
        self._flush_timeout = flush_timeout_seconds
        self._failures = 0
        self._lock = threading.Lock()
        self._client = Posthog(
            api_key,
            host=host,
            disable_geoip=True,
            max_retries=1,
            timeout=3,
            max_queue_size=1_000,
            on_error=self._on_error,
        )

    def _on_error(self, error: Exception, _items: object) -> None:
        with self._lock:
            self._failures += 1
        logger.warning("analytics_export_failed", extra={"error_type": type(error).__name__})

    def capture(self, user_id: UUID, event: str, properties: Mapping[str, str | int | bool]) -> None:
        filtered = _filter(event, properties)
        if filtered is None:
            return
        try:
            self._client.capture(event, distinct_id=str(user_id), properties=filtered)
        except Exception:  # noqa: BLE001 - telemetry must never break requests
            logger.warning("analytics_capture_failed")

    def flush(self) -> bool:
        with self._lock:
            before = self._failures
        try:
            self._client.flush(timeout_seconds=self._flush_timeout)
        except Exception:  # noqa: BLE001
            logger.warning("analytics_flush_failed")
            return False
        with self._lock:
            return self._failures == before

    def shutdown(self) -> None:
        try:
            self._client.shutdown()
        except Exception:  # noqa: BLE001
            logger.warning("analytics_shutdown_failed")
