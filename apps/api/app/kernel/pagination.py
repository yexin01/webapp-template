"""Keyset pagination shared by every list endpoint (API-03, DB-04).

Lists are ordered by ``(created_at DESC, id DESC)``, and the cursor encodes the
last row's pair. Unlike offsets, keyset cursors stay stable while rows are
inserted, and the query cost does not grow with the page number.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.kernel.errors import InvalidRequestError

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


@dataclass(frozen=True, slots=True)
class PageCursor:
    created_at: datetime
    id: UUID


def bounded_limit(limit: int) -> int:
    return max(1, min(limit, MAX_PAGE_SIZE))


def encode_cursor(created_at: datetime, row_id: UUID) -> str:
    raw = f"{created_at.isoformat()}|{row_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(value: str | None) -> PageCursor | None:
    if not value:
        return None
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode()
        created_at, _, ident = raw.partition("|")
        return PageCursor(created_at=datetime.fromisoformat(created_at), id=UUID(ident))
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise InvalidRequestError("The pagination cursor is invalid.") from exc
