"""Billing domain: plans, the subscription-to-entitlement matrix (BILL-01,
spec §9.5), the price allowlist (BILL-03), permissions, and events.

This module is the single implementation of plan semantics. The frontend
renders its output and never re-derives it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import ClassVar
from uuid import UUID

from app.kernel.authorization import ALL_ROLES, Permission
from app.kernel.events import DomainEvent


class Plan(StrEnum):
    FREE = "free"
    PRO = "pro"


class PlanKey(StrEnum):
    PRO_MONTHLY = "pro_monthly"
    PRO_YEARLY = "pro_yearly"
    UNKNOWN = "unknown"


class SubscriptionStatus(StrEnum):
    INCOMPLETE = "incomplete"
    INCOMPLETE_EXPIRED = "incomplete_expired"
    TRIALING = "trialing"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELED = "canceled"
    UNPAID = "unpaid"
    PAUSED = "paused"


class BillingState(StrEnum):
    """What the UI shows. Derived here so every surface agrees (BILL-20)."""

    NONE = "none"
    ACTIVE = "active"
    CANCELING = "canceling"
    PAST_DUE = "past_due"
    PROCESSING = "processing"
    PAYMENT_REQUIRED = "payment_required"
    CANCELED = "canceled"


@dataclass(frozen=True, slots=True)
class SubscriptionSnapshot:
    stripe_subscription_id: str
    stripe_customer_id: str
    status: SubscriptionStatus
    plan_key: PlanKey
    price_id: str | None
    current_period_end: datetime | None
    cancel_at_period_end: bool
    cancel_at: datetime | None
    ended_at: datetime | None


@dataclass(frozen=True, slots=True)
class EntitlementDecision:
    plan: Plan
    reason: str
    subscription: SubscriptionSnapshot | None = None


@dataclass(frozen=True, slots=True)
class BillingSummary:
    plan: Plan
    state: BillingState
    plan_key: PlanKey | None
    current_period_end: datetime | None
    cancels_at: datetime | None


# DEC-010 (ADR-0001): past_due keeps Pro while Stripe retries payment.
_ENTITLED_STATUSES = frozenset({SubscriptionStatus.ACTIVE, SubscriptionStatus.PAST_DUE})


def _scheduled_end(sub: SubscriptionSnapshot) -> datetime | None:
    if sub.cancel_at is not None:
        return sub.cancel_at
    if sub.cancel_at_period_end:
        return sub.current_period_end
    return None


def _has_ended(sub: SubscriptionSnapshot, now: datetime) -> bool:
    if sub.ended_at is not None and sub.ended_at <= now:
        return True
    end = _scheduled_end(sub)
    # Defensive: if a cancellation date has passed but the webhook announcing
    # it is delayed, stop granting access anyway.
    return end is not None and end <= now


def _is_entitling(sub: SubscriptionSnapshot, now: datetime) -> bool:
    return sub.plan_key is not PlanKey.UNKNOWN and sub.status in _ENTITLED_STATUSES and not _has_ended(sub, now)


def _latest(subs: Sequence[SubscriptionSnapshot]) -> SubscriptionSnapshot:
    return max(subs, key=lambda s: s.current_period_end.timestamp() if s.current_period_end else 0.0)


def decide(
    subscriptions: Sequence[SubscriptionSnapshot],
    now: datetime,
) -> EntitlementDecision:
    """Decide the effective plan for an actor from the actor's own subscriptions.

    DEC-002 option (b), ADR-0002 (BILL-ORG-02): the same decision applies in
    the personal context and in every organization the actor belongs to.
    Callers pass only the actor's subscriptions, so Pro is never shared with
    other members; membership itself is checked before this is reached.
    """
    entitling = [s for s in subscriptions if _is_entitling(s, now)]
    if entitling:
        sub = _latest(entitling)
        return EntitlementDecision(Plan.PRO, f"subscription_{sub.status.value}", sub)

    if any(s.status is SubscriptionStatus.TRIALING for s in subscriptions):
        # Trials are out of scope (DEC-010); never grant access implicitly.
        return EntitlementDecision(Plan.FREE, "trialing_not_supported")
    return EntitlementDecision(Plan.FREE, "no_entitling_subscription")


_PENDING_STATES: dict[SubscriptionStatus, BillingState] = {
    SubscriptionStatus.INCOMPLETE: BillingState.PROCESSING,
    SubscriptionStatus.UNPAID: BillingState.PAYMENT_REQUIRED,
    SubscriptionStatus.PAUSED: BillingState.PAYMENT_REQUIRED,
    SubscriptionStatus.TRIALING: BillingState.PROCESSING,
}


def summarize(
    subscriptions: Sequence[SubscriptionSnapshot],
    now: datetime,
) -> BillingSummary:
    """Summarize personal billing state for presentation."""
    decision = decide(subscriptions, now)
    sub = decision.subscription
    if sub is not None:
        end = _scheduled_end(sub)
        if sub.status is SubscriptionStatus.PAST_DUE:
            state = BillingState.PAST_DUE
        elif end is not None:
            state = BillingState.CANCELING
        else:
            state = BillingState.ACTIVE
        return BillingSummary(Plan.PRO, state, sub.plan_key, sub.current_period_end, end)

    pending = [s for s in subscriptions if s.status in _PENDING_STATES]
    if pending:
        p = _latest(pending)
        return BillingSummary(Plan.FREE, _PENDING_STATES[p.status], p.plan_key, p.current_period_end, None)

    if subscriptions:
        last = _latest(subscriptions)
        return BillingSummary(Plan.FREE, BillingState.CANCELED, last.plan_key, None, None)
    return BillingSummary(Plan.FREE, BillingState.NONE, None, None, None)


@dataclass(frozen=True, slots=True)
class PriceCatalog:
    """Server-side allowlist of purchasable prices (BILL-03)."""

    prices: Mapping[PlanKey, str]

    def price_for(self, key: PlanKey) -> str | None:
        return self.prices.get(key)

    def key_for(self, price_id: str | None) -> PlanKey:
        for key, value in self.prices.items():
            if value == price_id:
                return key
        return PlanKey.UNKNOWN


# Billing is owned by individuals, so it is never managed from an organization
# context. Pro features are used in any context (DEC-002 option (b)).
BILLING_MANAGE = Permission("billing:manage", personal=True)
PRO_FEATURE_USE = Permission("pro_feature:use", personal=True, org_roles=ALL_ROLES)


@dataclass(frozen=True, slots=True, kw_only=True)
class EntitlementChanged(DomainEvent):
    """A user's effective plan changed after synchronizing with Stripe."""

    event_type: ClassVar[str] = "billing.entitlement_changed"
    user_id: UUID
    plan: Plan
