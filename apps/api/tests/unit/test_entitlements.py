"""Subscription state -> entitlement matrix (spec §9.5, BILL-01)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.modules.billing.domain import (
    BillingState,
    Plan,
    PlanKey,
    SubscriptionSnapshot,
    SubscriptionStatus,
    decide,
    summarize,
)

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
LATER = NOW + timedelta(days=20)
EARLIER = NOW - timedelta(days=1)


def sub(status: SubscriptionStatus, **kw: object) -> SubscriptionSnapshot:
    base = SubscriptionSnapshot(
        stripe_subscription_id="sub_1",
        stripe_customer_id="cus_1",
        status=status,
        plan_key=PlanKey.PRO_MONTHLY,
        price_id="price_m",
        current_period_end=LATER,
        cancel_at_period_end=False,
        cancel_at=None,
        ended_at=None,
    )
    return replace(base, **kw)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (SubscriptionStatus.ACTIVE, Plan.PRO),
        (SubscriptionStatus.PAST_DUE, Plan.PRO),  # DEC-010: grace during Stripe retries
        (SubscriptionStatus.INCOMPLETE, Plan.FREE),
        (SubscriptionStatus.INCOMPLETE_EXPIRED, Plan.FREE),
        (SubscriptionStatus.TRIALING, Plan.FREE),  # trials are out of scope
        (SubscriptionStatus.UNPAID, Plan.FREE),
        (SubscriptionStatus.CANCELED, Plan.FREE),
        (SubscriptionStatus.PAUSED, Plan.FREE),
    ],
)
def test_status_matrix(status: SubscriptionStatus, expected: Plan) -> None:
    assert decide([sub(status)], NOW).plan is expected


def test_no_subscription_is_free() -> None:
    decision = decide([], NOW)
    assert decision.plan is Plan.FREE
    assert decision.reason == "no_entitling_subscription"


def test_cancel_at_period_end_keeps_pro_until_period_end() -> None:
    s = sub(SubscriptionStatus.ACTIVE, cancel_at_period_end=True)
    assert decide([s], NOW).plan is Plan.PRO
    assert decide([s], LATER + timedelta(seconds=1)).plan is Plan.FREE


def test_cancel_at_in_past_revokes_even_if_status_not_yet_updated() -> None:
    s = sub(SubscriptionStatus.ACTIVE, cancel_at=EARLIER)
    assert decide([s], NOW).plan is Plan.FREE


def test_unknown_price_never_entitles() -> None:
    s = sub(SubscriptionStatus.ACTIVE, plan_key=PlanKey.UNKNOWN)
    assert decide([s], NOW).plan is Plan.FREE


def test_entitled_subscription_wins_over_canceled_history() -> None:
    old = sub(SubscriptionStatus.CANCELED, stripe_subscription_id="sub_old", current_period_end=EARLIER)
    current = sub(SubscriptionStatus.ACTIVE, stripe_subscription_id="sub_new")
    decision = decide([old, current], NOW)
    assert decision.plan is Plan.PRO
    assert decision.subscription is not None
    assert decision.subscription.stripe_subscription_id == "sub_new"


@pytest.mark.parametrize(
    ("subs", "state", "plan"),
    [
        ([], BillingState.NONE, Plan.FREE),
        ([sub(SubscriptionStatus.ACTIVE)], BillingState.ACTIVE, Plan.PRO),
        ([sub(SubscriptionStatus.ACTIVE, cancel_at_period_end=True)], BillingState.CANCELING, Plan.PRO),
        ([sub(SubscriptionStatus.PAST_DUE)], BillingState.PAST_DUE, Plan.PRO),
        ([sub(SubscriptionStatus.INCOMPLETE)], BillingState.PROCESSING, Plan.FREE),
        ([sub(SubscriptionStatus.UNPAID)], BillingState.PAYMENT_REQUIRED, Plan.FREE),
        ([sub(SubscriptionStatus.CANCELED)], BillingState.CANCELED, Plan.FREE),
    ],
)
def test_summary_states(subs: list[SubscriptionSnapshot], state: BillingState, plan: Plan) -> None:
    summary = summarize(subs, NOW)
    assert summary.state is state
    assert summary.plan is plan


def test_summary_reports_cancellation_date() -> None:
    summary = summarize([sub(SubscriptionStatus.ACTIVE, cancel_at_period_end=True)], NOW)
    assert summary.cancels_at == LATER
