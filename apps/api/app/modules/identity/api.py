"""What other modules may use of identity: the events it publishes (ADR-0003)."""

from app.modules.identity.domain import UserDeleted

__all__ = ["UserDeleted"]
