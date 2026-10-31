"""Domain and application errors.

Every error carries a stable machine-readable ``code`` and a public message
that is safe to return to clients. Messages must never contain secrets,
tokens, SQL, or data belonging to another tenant.

A module declares its own errors next to its domain. Each carries a ``code``
and a ``status``: a client error (4xx), or 503 for a dependency or
configuration failure. The error handler maps it with no change to the kernel.
A request transaction keeps its writes when the request raises such an error,
so an expected outcome (a denial's audit entry) persists. A failure that is a
bug is a plain exception: 500, rollback, and an error report.
"""

from __future__ import annotations

from typing import ClassVar


class DomainError(Exception):
    code = "INTERNAL_ERROR"
    default_message = "An unexpected error occurred."
    # Not ``status_code``: Sentry's Starlette hook would then report every expected 503 itself.
    status: ClassVar[int] = 500

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if not (400 <= cls.status < 500 or cls.status == 503):
            raise TypeError(
                f"{cls.__name__}: declare status (4xx, or 503); an internal failure is a plain exception, "
                "not a DomainError"
            )

    def __init__(self, message: str | None = None) -> None:
        self.public_message = message or self.default_message
        super().__init__(self.public_message)


class AuthenticationError(DomainError):
    code = "UNAUTHENTICATED"
    status = 401
    default_message = "Authentication is required."


class ForbiddenError(DomainError):
    code = "FORBIDDEN"
    status = 403
    default_message = "You do not have permission to perform this action."


class NotFoundError(DomainError):
    code = "NOT_FOUND"
    status = 404
    default_message = "The requested resource was not found."


class EntitlementRequiredError(DomainError):
    code = "ENTITLEMENT_REQUIRED"
    status = 403
    default_message = "This feature requires the Pro plan."


class ConflictError(DomainError):
    code = "CONFLICT"
    status = 409
    default_message = "The request conflicts with the current state."


class InvalidRequestError(DomainError):
    code = "INVALID_REQUEST"
    status = 400
    default_message = "The request is invalid."


class RateLimitedError(DomainError):
    code = "RATE_LIMITED"
    status = 429
    default_message = "Too many requests. Try again later."

    def __init__(self, retry_after_seconds: int, message: str | None = None) -> None:
        self.retry_after_seconds = max(1, retry_after_seconds)
        super().__init__(message)


class BudgetExceededError(RateLimitedError):
    """A shared spending budget is used up (LLM-02, LLM-06), as opposed to the caller's own limit."""

    code = "BUDGET_EXCEEDED"
    default_message = "The usage budget for this feature is used up. Try again later."


class DependencyUnavailableError(DomainError):
    code = "DEPENDENCY_UNAVAILABLE"
    status = 503
    default_message = "A required service is temporarily unavailable."


class NotConfiguredError(DomainError):
    code = "NOT_CONFIGURED"
    status = 503
    default_message = "This feature is not configured in this environment."


class ProviderRejectedError(NotConfiguredError):
    """The feature is configured, but the provider refuses the key or a request the server built.

    The answer is the same ``NOT_CONFIGURED``. It is reported as an error, not a warning, because an
    operator has to act and nothing is waiting to be set: an unset secret is only a warning.
    """


class WebhookSignatureError(DomainError):
    code = "WEBHOOK_SIGNATURE_INVALID"
    status = 400
    default_message = "Webhook signature verification failed."
