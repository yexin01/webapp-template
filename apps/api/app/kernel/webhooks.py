"""Inbound webhook ledger status (BILL-09, BILL-12).

Every provider's webhooks are recorded in a ledger keyed by the provider's
event ID, which makes duplicate, delayed, and concurrent deliveries
idempotent.
"""

from __future__ import annotations

from enum import StrEnum


class EventStatus(StrEnum):
    RECEIVED = "received"
    PROCESSED = "processed"
    IGNORED = "ignored"
    NEEDS_RECONCILIATION = "needs_reconciliation"
    FAILED = "failed"
