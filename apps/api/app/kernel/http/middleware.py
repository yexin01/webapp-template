"""Pure ASGI middleware: request correlation, security headers, body-size limit."""

from __future__ import annotations

import logging
import re
import threading
import time
import uuid
from collections.abc import Callable

import anyio
from anyio.lowlevel import RunVar
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.kernel.http.problem import PayloadTooLargeError, internal_error_response
from app.kernel.request_context import RequestTelemetry, request_id_var, request_telemetry_var

logger = logging.getLogger("app.access")
errors_logger = logging.getLogger("app.errors")

_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")

# The most a request that queued telemetry waits for it to be sent, after its response (OBS-06).
TELEMETRY_FLUSH_SECONDS = 2.0

SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"cache-control", b"no-store"),
    (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"),
    (b"cross-origin-resource-policy", b"same-origin"),
)


class RequestContextMiddleware:
    """Assigns a request ID, adds security headers, and writes an access log line.

    The access log records method, route path, status, and duration only:
    no query strings, headers, or bodies.

    It also turns an unhandled exception into the problem-details ``500``
    here, inside the request context, so the response carries the request ID
    and security headers. Starlette's own last-resort handler runs outside
    every middleware, and the ASGI server would then print the raw exception
    message, unredacted.

    Logs and error reports carry the request ID and, once the request is
    authenticated, the internal user and organization IDs (OBS-04, OBS-05).

    A serverless instance can be frozen as soon as its invocation ends, and
    the Sentry and PostHog clients send in the background. So a request that
    queued an error event or an analytics event hands ``flush_telemetry`` to a
    thread once its response (or its 500) is out, and waits for it at most
    ``TELEMETRY_FLUSH_SECONDS``. That bounds what an outage of either service
    costs the invocation, and never changes the response (OBS-06). A request
    that queued nothing does not wait or switch threads.
    """

    def __init__(self, app: ASGIApp, flush_telemetry: Callable[[float], None] | None = None) -> None:
        self.app = app
        self._flush_telemetry = flush_telemetry
        # Outside the request threads (a limiter belongs to one event loop). A thread whose wait was
        # given up on keeps running, so a flush does not start while another is still sending.
        self._flush_threads: RunVar[anyio.CapacityLimiter] = RunVar(f"telemetry_flush_{id(self)}")
        self._sending = threading.Lock()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = dict(scope.get("headers") or []).get(b"x-request-id", b"").decode("latin-1")
        request_id = incoming if _REQUEST_ID.fullmatch(incoming) else uuid.uuid4().hex
        token = request_id_var.set(request_id)
        telemetry = RequestTelemetry(request_id)
        telemetry_token = request_telemetry_var.set(telemetry)
        started = time.monotonic()
        status_holder = {"status": 500}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                headers = list(message.get("headers", []))
                existing = {k.lower() for k, _ in headers}
                headers += [(k, v) for k, v in SECURITY_HEADERS if k not in existing]
                headers.append((b"x-request-id", request_id.encode("ascii")))
                message["headers"] = headers
            await send(message)

        response_started = False

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send_wrapper(message)

        try:
            await self.app(scope, receive, tracking_send)
        except Exception as exc:
            # The log record holds the exception type only (JsonFormatter), and
            # error reporting scrubs the event; the message never reaches stdout.
            errors_logger.error("unhandled_exception", exc_info=exc)
            if not response_started:
                await internal_error_response()(scope, receive, send_wrapper)
            # A response that already started cannot be replaced; the server
            # closes the connection when the app returns without finishing it.
        finally:
            route = scope.get("route")
            logger.info(
                "request",
                extra={
                    "method": scope.get("method"),
                    "path": getattr(route, "path", None) or "unmatched",
                    "status": status_holder["status"],
                    "duration_ms": int((time.monotonic() - started) * 1000),
                },
            )
            if telemetry.unsent_telemetry and self._flush_telemetry is not None:
                await self._flush(self._flush_telemetry)
            request_telemetry_var.reset(telemetry_token)
            request_id_var.reset(token)

    async def _flush(self, flush: Callable[[float], None]) -> None:
        """Runs ``flush`` on a thread of its own, waiting at most ``TELEMETRY_FLUSH_SECONDS`` for it."""

        def send() -> None:
            if not self._sending.acquire(blocking=False):
                logger.warning("telemetry_flush_skipped")  # the one sending now may take this request's events too
                return
            try:
                flush(TELEMETRY_FLUSH_SECONDS)
            finally:
                self._sending.release()

        try:
            limiter = self._flush_threads.get()
        except LookupError:
            limiter = anyio.CapacityLimiter(1)
            self._flush_threads.set(limiter)
        # Shielded: a request cancelled after its response (a server shutting down) still hands its events over.
        with anyio.move_on_after(TELEMETRY_FLUSH_SECONDS, shield=True) as scope:
            try:
                await anyio.to_thread.run_sync(send, limiter=limiter, abandon_on_cancel=True)
            except Exception:  # noqa: BLE001 - telemetry must never break requests
                logger.warning("telemetry_flush_failed")
        if scope.cancelled_caught:
            logger.warning("telemetry_flush_incomplete", extra={"budget_seconds": TELEMETRY_FLUSH_SECONDS})


class BodySizeLimitMiddleware:
    """Rejects bodies above ``max_bytes`` using Content-Length and a streaming count (SEC-09)."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = dict(scope.get("headers") or []).get(b"content-length")
        if declared is not None:
            try:
                too_large = int(declared) > self.max_bytes
            except ValueError:
                too_large = True
            if too_large:
                await _reject(send)
                return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise PayloadTooLargeError()
            return message

        await self.app(scope, limited_receive, send)


async def _reject(send: Send) -> None:
    body = (
        b'{"type":"about:blank","title":"Content Too Large","status":413,'
        b'"code":"PAYLOAD_TOO_LARGE","detail":"The request body is too large.","request_id":'
        + (f'"{request_id_var.get()}"' if request_id_var.get() else "null").encode()
        + b"}"
    )
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [(b"content-type", b"application/problem+json"), (b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})
