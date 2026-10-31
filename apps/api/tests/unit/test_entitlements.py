"""Subscription state -> entitlement matrix (spec §9.5, BILL-01): the plan and the features it unlocks."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.modules.billing.domain import (
    PRO_FEATURES,
    BillingState,
    CheckoutBlock,
    Plan,
    PlanKey,
    SubscriptionSnapshot,
    SubscriptionStatus,
    checkout_block,
    decide,
    summarize,
)

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
LATER = NOW + timedelta(days=20)
EARLIER = NOW - timedelta(days=1)
NO_FEATURES: frozenset[str] = frozenset()


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
    decision = decide([sub(status)], NOW)
    assert decision.plan is expected
    assert decision.features == (PRO_FEATURES if expected is Plan.PRO else NO_FEATURES)


def test_no_subscription_is_free() -> None:
    decision = decide([], NOW)
    assert decision.plan is Plan.FREE
    assert decision.reason == "no_entitling_subscription"
    assert decision.features == NO_FEATURES


def test_cancel_at_period_end_keeps_pro_until_period_end() -> None:
    s = sub(SubscriptionStatus.ACTIVE, cancel_at_period_end=True)
    before, after = decide([s], NOW), decide([s], LATER + timedelta(seconds=1))
    assert (before.plan, before.features) == (Plan.PRO, PRO_FEATURES)
    assert (after.plan, after.features) == (Plan.FREE, NO_FEATURES)


def test_cancel_at_in_past_revokes_even_if_status_not_yet_updated() -> None:
    s = sub(SubscriptionStatus.ACTIVE, cancel_at=EARLIER)
    decision = decide([s], NOW)
    assert (decision.plan, decision.features) == (Plan.FREE, NO_FEATURES)


def test_unknown_price_never_entitles() -> None:
    s = sub(SubscriptionStatus.ACTIVE, plan_key=PlanKey.UNKNOWN)
    decision = decide([s], NOW)
    assert (decision.plan, decision.features) == (Plan.FREE, NO_FEATURES)


def test_entitled_subscription_wins_over_canceled_history() -> None:
    old = sub(SubscriptionStatus.CANCELED, stripe_subscription_id="sub_old", current_period_end=EARLIER)
    current = sub(SubscriptionStatus.ACTIVE, stripe_subscription_id="sub_new")
    decision = decide([old, current], NOW)
    assert decision.plan is Plan.PRO
    assert decision.features == PRO_FEATURES
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


# -- Refusing a second checkout (DEC-013 option B) ------------------------------

UNKNOWN = {"plan_key": PlanKey.UNKNOWN, "price_id": "price_replaced"}


@pytest.mark.parametrize(
    ("subscription", "expected"),
    [
        (sub(SubscriptionStatus.ACTIVE), CheckoutBlock.ALREADY_PRO),
        (sub(SubscriptionStatus.ACTIVE, cancel_at_period_end=True), CheckoutBlock.ALREADY_PRO),  # until it ends
        (sub(SubscriptionStatus.PAST_DUE), CheckoutBlock.ALREADY_PRO),  # DEC-010
        (sub(SubscriptionStatus.UNPAID), CheckoutBlock.PAYMENT_REQUIRED),
        (sub(SubscriptionStatus.PAUSED), CheckoutBlock.PAYMENT_REQUIRED),
        (sub(SubscriptionStatus.PAST_DUE, **UNKNOWN), CheckoutBlock.PAYMENT_REQUIRED),
        (sub(SubscriptionStatus.ACTIVE, **UNKNOWN), CheckoutBlock.SUBSCRIPTION_EXISTS),
        (sub(SubscriptionStatus.TRIALING), CheckoutBlock.SUBSCRIPTION_EXISTS),
        (sub(SubscriptionStatus.INCOMPLETE), CheckoutBlock.PAYMENT_PENDING),
        (sub(SubscriptionStatus.CANCELED), None),
        (sub(SubscriptionStatus.INCOMPLETE_EXPIRED), None),
        # Stripe still says active, but the end date has passed: the subscription is over, as for entitlement.
        (sub(SubscriptionStatus.ACTIVE, cancel_at_period_end=True, current_period_end=EARLIER), None),
        (sub(SubscriptionStatus.UNPAID, ended_at=EARLIER), None),
    ],
    ids=lambda v: v.value if isinstance(v, CheckoutBlock) else None,
)
def test_each_unsettled_subscription_blocks_a_new_checkout(
    subscription: SubscriptionSnapshot, expected: CheckoutBlock | None
) -> None:
    assert checkout_block([subscription], NOW) is expected
    assert summarize([subscription], NOW).checkout_block is expected


def test_no_subscription_blocks_nothing() -> None:
    assert checkout_block([], NOW) is None
    assert summarize([], NOW).checkout_block is None


def test_a_finished_subscription_beside_a_new_one_blocks_only_for_the_new_one() -> None:
    old = sub(SubscriptionStatus.CANCELED, stripe_subscription_id="sub_old")
    assert checkout_block([old], NOW) is None
    assert checkout_block([old, sub(SubscriptionStatus.UNPAID, stripe_subscription_id="sub_new")], NOW) is (
        CheckoutBlock.PAYMENT_REQUIRED
    )


def test_a_payment_that_is_due_is_named_before_an_active_subscription_the_catalog_does_not_know() -> None:
    unpaid = sub(SubscriptionStatus.UNPAID, stripe_subscription_id="sub_a")
    other = sub(SubscriptionStatus.ACTIVE, stripe_subscription_id="sub_b", **UNKNOWN)
    assert checkout_block([other, unpaid], NOW) is CheckoutBlock.PAYMENT_REQUIRED


def test_the_summary_blocks_exactly_when_it_does_not_offer_checkout() -> None:
    """The UI offers Upgrade only when nothing blocks it, so the two cannot disagree."""
    for status in SubscriptionStatus:
        summary = summarize([sub(status)], NOW)
        assert (summary.checkout_block is None) == (summary.state in (BillingState.CANCELED, BillingState.NONE))
