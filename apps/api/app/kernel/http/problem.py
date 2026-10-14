"""RFC 9457 problem-details error contract (API-02).

Responses carry a stable ``code`` and the request ID. They never include
stack traces, SQL, submitted values, or raw vendor messages.
"""

from __future__ import annotations

import logging
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.kernel.errors import (
    AuthenticationError,
    ConflictError,
    DependencyUnavailableError,
    DomainError,
    EntitlementRequiredError,
    ForbiddenError,
    InvalidRequestError,
    NotConfiguredError,
    NotFoundError,
    RateLimitedError,
    WebhookSignatureError,
)
from app.kernel.request_context import request_id_var

logger = logging.getLogger(__name__)

PROBLEM_MEDIA_TYPE = "application/problem+json"

_STATUS: dict[type[DomainError], int] = {
    AuthenticationError: 401,
    ForbiddenError: 403,
    EntitlementRequiredError: 403,
    NotFoundError: 404,
    ConflictError: 409,
    InvalidRequestError: 400,
    WebhookSignatureError: 400,
    RateLimitedError: 429,
    DependencyUnavailableError: 503,
    NotConfiguredError: 503,
}


class PayloadTooLargeError(StarletteHTTPException):
    """Raised by the body-size middleware while the body is being read.

    An ``HTTPException``, so FastAPI's body parsing re-raises it as is instead
    of turning it into a generic ``400``.
    """

    def __init__(self) -> None:
        super().__init__(status_code=413)


def problem(
    status: int,
    code: str,
    detail: str,
    *,
    headers: dict[str, str] | None = None,
    extra: dict[str, Any] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": "about:blank",
        "title": HTTPStatus(status).phrase,
        "status": status,
        "code": code,
        "detail": detail,
        "request_id": request_id_var.get(),
    }
    if extra:
        body.update(extra)
    return JSONResponse(body, status_code=status, headers=headers, media_type=PROBLEM_MEDIA_TYPE)


def _status_for(exc: DomainError) -> int:
    for cls in type(exc).__mro__:
        if cls in _STATUS:
            return _STATUS[cls]
    return 500


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain(_request: Request, exc: DomainError) -> JSONResponse:
        status = _status_for(exc)
        headers: dict[str, str] = {}
        if isinstance(exc, RateLimitedError):
            headers["Retry-After"] = str(exc.retry_after_seconds)
        if isinstance(exc, AuthenticationError):
            headers["WWW-Authenticate"] = "Bearer"
        if status >= 500:
            logger.warning("dependency_or_configuration_error", extra={"code": exc.code})
        return problem(status, exc.code, exc.public_message, headers=headers or None)

    @app.exception_handler(RequestValidationError)
    async def _validation(_request: Request, exc: RequestValidationError) -> JSONResponse:
        # Field locations and messages only; submitted values are never echoed.
        errors = [
            {
                "loc": [str(p) for p in err.get("loc", ())],
                "msg": str(err.get("msg", "")),
                "type": str(err.get("type", "")),
            }
            for err in exc.errors()
        ]
        return problem(
            422, "VALIDATION_FAILED", "The request body or parameters are invalid.", extra={"errors": errors}
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(exc.status_code, "HTTP_ERROR")
        detail = HTTPStatus(exc.status_code).description or HTTPStatus(exc.status_code).phrase
        return problem(exc.status_code, code, detail)

    @app.exception_handler(PayloadTooLargeError)
    async def _too_large(_request: Request, _exc: PayloadTooLargeError) -> JSONResponse:
        return problem(413, "PAYLOAD_TOO_LARGE", "The request body is too large.")

    @app.exception_handler(Exception)
    async def _unhandled(_request: Request, exc: Exception) -> JSONResponse:
        # Last resort only: RequestContextMiddleware handles unhandled
        # exceptions first, inside the request context. ERROR-level logs are
        # forwarded to error reporting with redaction.
        logger.error("unhandled_exception", exc_info=exc)
        return internal_error_response()


def internal_error_response() -> JSONResponse:
    return problem(500, "INTERNAL_ERROR", "An unexpected error occurred.")
