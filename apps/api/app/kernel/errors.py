"""Domain and application errors.

Every error carries a stable machine-readable ``code`` and a public message
that is safe to return to clients. Messages must never contain secrets,
tokens, SQL, or data belonging to another tenant.
"""

from __future__ import annotations


class DomainError(Exception):
    code = "INTERNAL_ERROR"
    default_message = "An unexpected error occurred."

    def __init__(self, message: str | None = None) -> None:
        self.public_message = message or self.default_message
        super().__init__(self.public_message)


class AuthenticationError(DomainError):
    code = "UNAUTHENTICATED"
    default_message = "Authentication is required."


class ForbiddenError(DomainError):
    code = "FORBIDDEN"
    default_message = "You do not have permission to perform this action."


class NotFoundError(DomainError):
    code = "NOT_FOUND"
    default_message = "The requested resource was not found."


class EntitlementRequiredError(DomainError):
    code = "ENTITLEMENT_REQUIRED"
    default_message = "This feature requires the Pro plan."


class ConflictError(DomainError):
    code = "CONFLICT"
    default_message = "The request conflicts with the current state."


class InvalidRequestError(DomainError):
    code = "INVALID_REQUEST"
    default_message = "The request is invalid."


class RateLimitedError(DomainError):
    code = "RATE_LIMITED"
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
    default_message = "A required service is temporarily unavailable."


class NotConfiguredError(DomainError):
    code = "NOT_CONFIGURED"
    default_message = "This feature is not configured in this environment."


class WebhookSignatureError(DomainError):
    code = "WEBHOOK_SIGNATURE_INVALID"
    default_message = "Webhook signature verification failed."
