"""Pure ASGI middleware: request correlation, security headers, body-size limit."""

from __future__ import annotations

import logging
import re
import time
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.kernel.http.problem import PayloadTooLargeError
from app.kernel.request_context import request_id_var

logger = logging.getLogger("app.access")

_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")

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
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = dict(scope.get("headers") or []).get(b"x-request-id", b"").decode("latin-1")
        request_id = incoming if _REQUEST_ID.fullmatch(incoming) else uuid.uuid4().hex
        token = request_id_var.set(request_id)
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

        try:
            await self.app(scope, receive, send_wrapper)
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
            request_id_var.reset(token)


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
