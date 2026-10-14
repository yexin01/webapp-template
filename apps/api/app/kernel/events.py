"""Domain events and the transactional outbox contract.

A module records an event inside the transaction that changes state. The event
is written to the outbox table in that same transaction, so it exists if and
only if the change committed. Handlers run after commit, either right after
the response or from the ``drain-outbox`` job, and they run **at least once**:
every handler must be idempotent.

Payloads hold identifiers and small facts only. They never hold personal data
beyond internal IDs, and never hold secrets.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, ClassVar
from uuid import UUID

_EVENT_TYPE = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
_HANDLER_NAME = re.compile(r"^[a-z][a-z0-9_.-]{1,80}$")

JsonValue = str | int | float | bool | None


@dataclass(frozen=True, slots=True, kw_only=True)
class DomainEvent:
    """Base class for events. Subclasses set ``event_type`` as ``<module>.<fact>``."""

    event_type: ClassVar[str]

    def payload(self) -> dict[str, JsonValue]:
        return {f.name: _json(getattr(self, f.name)) for f in dataclasses.fields(self)}


def _json(value: object) -> JsonValue:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return _json(value.value)
    raise TypeError(f"event payload values must be scalars, UUIDs, datetimes, or enums: {type(value).__name__}")


@dataclass(frozen=True, slots=True)
class StoredEvent:
    """An event as read back from the outbox."""

    id: UUID
    event_type: str
    payload: Mapping[str, Any]
    occurred_at: datetime
    attempts: int


EventHandler = Callable[[StoredEvent], None]


class EventHandlers:
    """Subscriptions from event types to handlers, registered at composition."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[tuple[str, EventHandler]]] = {}

    def subscribe(self, event: type[DomainEvent], name: str, handler: EventHandler) -> None:
        if not _EVENT_TYPE.fullmatch(event.event_type):
            raise ValueError(f"event_type must look like 'module.fact': {event.event_type!r}")
        if not _HANDLER_NAME.fullmatch(name):
            raise ValueError(f"invalid handler name: {name!r}")
        existing = self._handlers.setdefault(event.event_type, [])
        if any(n == name for n, _ in existing):
            raise ValueError(f"handler {name!r} is already subscribed to {event.event_type}")
        existing.append((name, handler))

    def for_type(self, event_type: str) -> list[tuple[str, EventHandler]]:
        return list(self._handlers.get(event_type, ()))

    def event_types(self) -> list[str]:
        return sorted(self._handlers)
