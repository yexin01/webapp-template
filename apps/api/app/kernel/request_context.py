"""Per-request correlation shared by the HTTP layer, logging, and error reports."""

from __future__ import annotations

import contextvars
from dataclasses import dataclass
from uuid import UUID

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)


@dataclass(slots=True)
class RequestTelemetry:
    """What the logs and error reports of one request carry (OBS-04, OBS-05).

    The middleware makes one per request. It is mutable on purpose: the auth dependencies are
    synchronous and run in a copy of the request's context, where setting a context variable
    would never reach the access log. Only internal, pseudonymous IDs go in it, never an
    identity provider's IDs, emails, or names.
    """

    request_id: str
    user_id: UUID | None = None
    org_id: UUID | None = None
    # Set when the request queued an error event or an analytics event that no one has sent yet.
    unsent_telemetry: bool = False


request_telemetry_var: contextvars.ContextVar[RequestTelemetry | None] = contextvars.ContextVar(
    "request_telemetry", default=None
)


def bind_tenant(user_id: UUID, org_id: UUID | None) -> None:
    """Records the authenticated user (and organization) for the logs of the current request, if there is one."""
    telemetry = request_telemetry_var.get()
    if telemetry is not None:
        telemetry.user_id = user_id
        telemetry.org_id = org_id


def note_unsent_telemetry() -> None:
    """Records that the current request queued telemetry, so the middleware sends it before the instance idles."""
    telemetry = request_telemetry_var.get()
    if telemetry is not None:
        telemetry.unsent_telemetry = True
