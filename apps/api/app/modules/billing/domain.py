"""Billing domain: plans, the subscription-to-entitlement matrix (BILL-01,
spec §9.5), the price allowlist (BILL-03), permissions, and events.

This module is the single implementation of plan semantics. The frontend
renders its output and never re-derives it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
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


def entitling_subscriptions(subscriptions: Sequence[SubscriptionSnapshot], now: datetime) -> list[SubscriptionSnapshot]:
    return [s for s in subscriptions if _is_entitling(s, now)]


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
    entitling = entitling_subscriptions(subscriptions, now)
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
    """Server-side allowlist of purchasable prices (BILL-03).

    ``retained`` maps prices that are no longer sold to their plan key, so
    subscribers who still pay a replaced price keep Pro. Stripe prices cannot
    be edited: changing a price creates a new price ID.
    """

    prices: Mapping[PlanKey, str]
    retained: Mapping[str, PlanKey] = field(default_factory=dict)

    def price_for(self, key: PlanKey) -> str | None:
        return self.prices.get(key)

    def key_for(self, price_id: str | None) -> PlanKey:
        for key, value in self.prices.items():
            if value == price_id:
                return key
        if price_id is None:
            return PlanKey.UNKNOWN
        return self.retained.get(price_id, PlanKey.UNKNOWN)


@dataclass(frozen=True, slots=True)
class PortalConfiguration:
    """What the Customer Portal's default configuration lets customers do (DEC-010, ADR-0001).

    The portal is configured in the Stripe Dashboard; this only describes it.
    """

    active: bool
    payment_method_update: bool
    invoice_history: bool
    cancel_enabled: bool
    cancel_mode: str
    switch_enabled: bool
    allowed_updates: tuple[str, ...]
    # Every price a customer can switch between, across all products.
    switch_prices: frozenset[str]


_PORTAL_FIX = "Settings > Billing > Customer portal"


def portal_problems(portal: PortalConfiguration | None, catalog: PriceCatalog) -> list[str]:
    """How the default portal configuration departs from DEC-010; empty when it matches.

    Proration, promotion codes, and customer details are product choices and are not checked.
    """
    if portal is None:
        return [f"no default Customer Portal configuration is saved in this mode (save one in {_PORTAL_FIX})"]
    problems: list[str] = []
    if not portal.active:
        problems.append("the default Customer Portal configuration is inactive")
    if not portal.payment_method_update:
        problems.append("the portal does not let customers update their payment method")
    if not portal.invoice_history:
        problems.append("the portal does not show invoice history")
    if not portal.cancel_enabled:
        problems.append("the portal does not let customers cancel")
    elif portal.cancel_mode != "at_period_end":
        problems.append("the portal cancels immediately instead of at the end of the billing period")
    if not portal.switch_enabled or "price" not in portal.allowed_updates:
        problems.append("the portal does not let customers switch between the Pro prices")
        return problems
    wanted = {key: catalog.price_for(key) for key in (PlanKey.PRO_MONTHLY, PlanKey.PRO_YEARLY)}
    for key, price in wanted.items():
        if price is not None and price not in portal.switch_prices:
            problems.append(f"the portal cannot switch to the {key.value} price {price}")
    for price in sorted(portal.switch_prices - {p for p in wanted.values() if p}):
        known = catalog.key_for(price)
        problems.append(
            f"the portal also offers {price}, "
            + (
                f"a replaced {known.value} price that is no longer sold"
                if known is not PlanKey.UNKNOWN
                else "a price outside the catalog that grants no Pro"
            )
        )
    return problems


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
