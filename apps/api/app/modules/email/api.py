"""What other modules may use of email: the message, the service that sends it, and Resend's test inbox (ADR-0003)."""

from app.modules.email.domain import TEST_RECIPIENT, EmailMessage
from app.modules.email.service import EmailService

__all__ = ["TEST_RECIPIENT", "EmailMessage", "EmailService"]
