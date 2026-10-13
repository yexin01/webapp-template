"""Per-request correlation identifier shared by the HTTP layer and logging."""

from __future__ import annotations

import contextvars

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)
