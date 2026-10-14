"""Organization entitlement truth table (spec §9.9, BILL-ORG-02; DEC-002 option (b), ADR-0002).

An individual's Pro subscription applies to that individual's own actions in
every organization they currently belong to. It is never shared with other
members, and organization roles neither grant nor remove it.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.domain.entitlements import SubscriptionStatus
from app.domain.tenancy import ExternalMembership, Role
from tests.conftest import Harness
from tests.integration.test_billing import make_pro

pytestmark = pytest.mark.db

ACME = "org_acme"
OTHER = "org_other"


@pytest.fixture
def acme(harness: Harness) -> dict[str, Any]:
    h = harness
    h.directory.set("user_admin", [ExternalMembership(ACME, Role.ADMIN)])
    h.directory.set("user_member", [ExternalMembership(ACME, Role.MEMBER)])
    h.directory.set("user_outsider", [ExternalMembership(OTHER, Role.ADMIN)])
    return {name: h.login(f"user_{name}") for name in ("admin", "member", "outsider")}


def plan_in(h: Harness, headers: dict[str, str], org: str | None) -> tuple[int, int, str | None]:
    """(entitlement status, Pro feature status, plan) for one actor in one context."""
    scope = f"/orgs/{org}" if org else ""
    ent = h.client.get(f"/api/v1{scope}/entitlement", headers=headers)
    pro = h.client.get(f"/api/v1{scope}/pro/insight", headers=headers)
    return ent.status_code, pro.status_code, ent.json().get("plan") if ent.status_code == 200 else None


# Rows: (actor, actor's subscription, context, expected Pro-feature status, expected plan)
TRUTH_TABLE = [
    # Subscriber, entitled subscription: Pro in personal and in every org they belong to, any role.
    ("admin", SubscriptionStatus.ACTIVE, None, 200, "pro"),
    ("admin", SubscriptionStatus.ACTIVE, ACME, 200, "pro"),
    ("member", SubscriptionStatus.ACTIVE, ACME, 200, "pro"),
    ("member", SubscriptionStatus.PAST_DUE, ACME, 200, "pro"),  # DEC-010 grace applies in orgs too
    # Subscriber whose subscription no longer entitles: Free everywhere.
    ("member", SubscriptionStatus.CANCELED, ACME, 403, "free"),
    ("member", SubscriptionStatus.UNPAID, ACME, 403, "free"),
    ("admin", SubscriptionStatus.INCOMPLETE, ACME, 403, "free"),
    # Non-subscribers: Free everywhere, regardless of role.
    ("admin", None, None, 403, "free"),
    ("admin", None, ACME, 403, "free"),
    ("member", None, ACME, 403, "free"),
]


@pytest.mark.parametrize(("actor", "subscription", "org", "pro_status", "plan"), TRUTH_TABLE)
def test_truth_table(
    harness: Harness,
    acme: dict[str, Any],
    actor: str,
    subscription: SubscriptionStatus | None,
    org: str | None,
    pro_status: int,
    plan: str,
) -> None:
    if subscription is not None:
        make_pro(harness, f"user_{actor}", subscription)
    assert plan_in(harness, acme[actor], org) == (200, pro_status, plan)


def test_pro_is_never_shared_with_other_members(harness: Harness, acme: dict[str, Any]) -> None:
    make_pro(harness, "user_admin")
    assert plan_in(harness, acme["admin"], ACME) == (200, 200, "pro")
    assert plan_in(harness, acme["member"], ACME) == (200, 403, "free")
    assert plan_in(harness, acme["member"], None) == (200, 403, "free")


def test_non_member_subscriber_gets_no_context(harness: Harness, acme: dict[str, Any]) -> None:
    make_pro(harness, "user_outsider")
    assert plan_in(harness, acme["outsider"], ACME) == (404, 404, None)


def test_removed_member_loses_org_pro_with_membership(harness: Harness, acme: dict[str, Any]) -> None:
    make_pro(harness, "user_member")
    assert plan_in(harness, acme["member"], ACME) == (200, 200, "pro")
    harness.directory.set("user_member", [])  # removed in the identity provider
    harness.clock.advance(seconds=harness.container.settings.membership_max_staleness_seconds + 1)
    assert plan_in(harness, acme["member"], ACME) == (404, 404, None)
    # The subscription itself is untouched: personal Pro continues.
    assert plan_in(harness, acme["member"], None) == (200, 200, "pro")


def test_cancellation_ends_org_pro_at_the_same_time_as_personal_pro(harness: Harness, acme: dict[str, Any]) -> None:
    from tests.integration.test_billing import event_payload, send_webhook, snapshot

    customer = make_pro(harness, "user_member")
    harness.billing.set_subscriptions(customer, [snapshot(customer, SubscriptionStatus.CANCELED)])
    assert send_webhook(harness, event_payload(customer, event_id="evt_cancel")).json()["status"] == "processed"
    assert plan_in(harness, acme["member"], ACME) == (200, 403, "free")
    assert plan_in(harness, acme["member"], None) == (200, 403, "free")


def test_org_administration_is_independent_of_billing(harness: Harness, acme: dict[str, Any]) -> None:
    """A Free admin manages org resources; a Pro member gains no admin rights."""
    make_pro(harness, "user_member")
    created = harness.client.post(f"/api/v1/orgs/{ACME}/projects", json={"name": "shared"}, headers=acme["admin"])
    assert created.status_code == 201
    project = created.json()["id"]
    denied = harness.client.delete(f"/api/v1/orgs/{ACME}/projects/{project}", headers=acme["member"])
    assert denied.status_code == 403
    allowed = harness.client.delete(f"/api/v1/orgs/{ACME}/projects/{project}", headers=acme["admin"])
    assert allowed.status_code == 204


def test_billing_management_stays_personal(harness: Harness, acme: dict[str, Any]) -> None:
    """Checkout and the portal exist only in the personal context; no org-scoped billing routes."""
    for path in (f"/api/v1/orgs/{ACME}/billing/checkout", f"/api/v1/orgs/{ACME}/billing/portal"):
        assert harness.client.post(path, headers=acme["admin"]).status_code in (404, 405)
