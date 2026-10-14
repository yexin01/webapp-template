"""Transactional outbox and scheduled jobs (ADR-0003)."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest

from app.kernel.application.outbox import OutboxDispatcher
from app.kernel.events import EventHandlers, StoredEvent
from app.kernel.infrastructure.config import REPO_ROOT
from app.kernel.tenancy import Role
from app.modules.billing.domain import EntitlementChanged, Plan, SubscriptionStatus
from app.modules.identity.domain import ExternalMembership
from tests.conftest import Harness, superuser_query
from tests.integration.test_billing import customer_of, event_payload, make_pro, send_webhook, snapshot

pytestmark = pytest.mark.db

CRON_SECRET = "cron-test-secret-0123456789abcdef0123"  # test-only value


def outbox_rows(h: Harness) -> list[tuple[Any, ...]]:
    return superuser_query(
        h.database_url, "SELECT event_type, status, attempts, payload FROM app.outbox ORDER BY occurred_at"
    )


# -- Outbox ------------------------------------------------------------------------


def test_event_is_recorded_with_the_change_and_dispatched_after_the_response(harness: Harness) -> None:
    headers = harness.login("user_a")
    r = harness.client.post("/api/v1/projects", json={"name": "evented"}, headers=headers)
    assert r.status_code == 201
    rows = outbox_rows(harness)
    assert [(t, s, a) for t, s, a, _ in rows] == [("projects.created", "processed", 1)]
    assert rows[0][3]["project_id"] == r.json()["id"]
    assert ("project_created", {"context": "personal"}) in [(e, p) for _, e, p in harness.analytics.events]


def test_no_event_without_the_change(harness: Harness) -> None:
    headers = harness.login("user_a")
    assert harness.client.post("/api/v1/projects", json={"name": "   "}, headers=headers).status_code in (400, 422)
    assert outbox_rows(harness) == []


def test_request_role_can_only_append_to_the_outbox(harness: Harness) -> None:
    with psycopg.connect(harness.database_url) as conn, conn.transaction():
        conn.execute("SET LOCAL ROLE app_api")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT * FROM app.outbox")
    with psycopg.connect(harness.database_url) as conn, conn.transaction():
        conn.execute("SET LOCAL ROLE app_api")
        # Without a user context, even appending is refused (RLS).
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("INSERT INTO app.outbox (id, event_type) VALUES (gen_random_uuid(), 'projects.created')")


def _dispatcher(harness: Harness, handlers: EventHandlers, max_attempts: int = 3) -> OutboxDispatcher:
    kernel = harness.container.kernel
    return OutboxDispatcher(kernel.store, handlers, harness.clock, batch_size=10, max_attempts=max_attempts)


def _record(harness: Harness, user: str = "user_a") -> None:
    """Record one event directly, outside any request, so nothing dispatches it."""
    from app.kernel.tenancy import VerifiedIdentity

    ctx = harness.container.identity.resolver.personal(VerifiedIdentity(user))
    with harness.container.kernel.store.as_tenant(ctx) as t:
        t.record(EntitlementChanged(user_id=ctx.user_id, plan=Plan.PRO))


def test_failed_handler_retries_with_backoff_then_dead_letters(harness: Harness) -> None:
    calls: list[StoredEvent] = []

    def flaky(event: StoredEvent) -> None:
        calls.append(event)
        raise RuntimeError("downstream down")

    handlers = EventHandlers()
    handlers.subscribe(EntitlementChanged, "test.flaky", flaky)
    dispatcher = _dispatcher(harness, handlers, max_attempts=3)
    _record(harness)

    assert dispatcher.drain().retried == 1
    assert dispatcher.drain().claimed == 0  # backoff: not due yet
    harness.clock.advance(seconds=2)
    assert dispatcher.drain().retried == 1
    harness.clock.advance(seconds=4)
    report = dispatcher.drain()
    assert report.dead == 1
    status, attempts, error = superuser_query(
        harness.database_url, "SELECT status, attempts, last_error FROM app.outbox"
    )[0]
    assert (status, attempts) == ("dead", 3)
    assert "test.flaky" in error
    assert len(calls) == 3


def test_expired_lease_is_reclaimed(harness: Harness) -> None:
    seen: list[StoredEvent] = []
    handlers = EventHandlers()
    handlers.subscribe(EntitlementChanged, "test.ok", seen.append)
    _record(harness)
    # Simulate a worker that leased the event and died.
    superuser_query(
        harness.database_url,
        "UPDATE app.outbox SET status = 'processing', attempts = 1, locked_until = %s",
        (harness.clock.now() + timedelta(seconds=60),),
    )
    dispatcher = _dispatcher(harness, handlers)
    assert dispatcher.drain().claimed == 0
    harness.clock.advance(seconds=61)
    assert dispatcher.drain().processed == 1
    assert len(seen) == 1


def test_billing_sync_emits_entitlement_change_once(harness: Harness) -> None:
    customer = make_pro(harness, "user_a")
    rows = [r for r in outbox_rows(harness) if r[0] == "billing.entitlement_changed"]
    assert [(r[1], r[3]["plan"]) for r in rows] == [("processed", "pro")]
    assert ("plan_changed", {"plan": "pro"}) in [(e, p) for _, e, p in harness.analytics.events]

    # A duplicate or no-op sync changes nothing, so no new event.
    assert send_webhook(harness, event_payload(customer)).json()["status"] == "processed"
    assert len([r for r in outbox_rows(harness) if r[0] == "billing.entitlement_changed"]) == 1

    harness.billing.set_subscriptions(customer, [snapshot(customer, SubscriptionStatus.CANCELED)])
    assert send_webhook(harness, event_payload(customer)).json()["status"] == "processed"
    plans = [r[3]["plan"] for r in outbox_rows(harness) if r[0] == "billing.entitlement_changed"]
    assert plans == ["pro", "free"]
    assert customer_of(harness, "user_a") == customer


# -- Jobs ----------------------------------------------------------------------------


@pytest.fixture
def cron(make_harness: Any) -> Harness:
    return make_harness(settings_overrides={"cron_secret": CRON_SECRET})


def test_jobs_endpoint_is_off_without_cron_secret(harness: Harness) -> None:
    r = harness.client.get("/api/v1/jobs/drain-outbox", headers={"Authorization": f"Bearer {CRON_SECRET}"})
    assert r.status_code == 404


@pytest.mark.parametrize("header", [None, "Bearer wrong-secret", f"Basic {CRON_SECRET}", CRON_SECRET])
def test_jobs_endpoint_requires_the_secret(cron: Harness, header: str | None) -> None:
    headers = {"Authorization": header} if header else {}
    r = cron.client.get("/api/v1/jobs/drain-outbox", headers=headers)
    assert r.status_code == 401
    assert CRON_SECRET not in r.text


def test_unknown_job_is_not_found(cron: Harness) -> None:
    r = cron.client.get("/api/v1/jobs/nope", headers={"Authorization": f"Bearer {CRON_SECRET}"})
    assert r.status_code == 404


def test_job_runs_and_reports_a_summary(cron: Harness) -> None:
    r = cron.client.get("/api/v1/jobs/prune-expired", headers={"Authorization": f"Bearer {CRON_SECRET}"})
    assert r.status_code == 200
    body = r.json()
    assert body["job"] == "prune-expired"
    assert body["ok"] is True
    assert body["summary"] == {"rate_limit_windows": 0, "outbox_events": 0}


def test_jobs_are_not_in_the_public_contract(cron: Harness) -> None:
    assert not any("/jobs" in path for path in cron.client.app.openapi()["paths"])  # type: ignore[attr-defined]


def test_drain_outbox_job_dispatches_leftovers(cron: Harness) -> None:
    _record(cron)
    r = cron.client.get("/api/v1/jobs/drain-outbox", headers={"Authorization": f"Bearer {CRON_SECRET}"})
    assert r.json()["summary"]["processed"] == 1


def test_reconcile_memberships_job_refreshes_stale_mirrors(cron: Harness) -> None:
    cron.directory.set("user_a", [ExternalMembership("org_q", Role.MEMBER)])
    headers = cron.login("user_a")
    assert cron.client.get("/api/v1/orgs/org_q/projects", headers=headers).status_code == 200
    cron.directory.set("user_a", [])  # removed in the identity provider
    cron.clock.advance(seconds=cron.container.settings.membership_max_staleness_seconds + 1)
    r = cron.client.get("/api/v1/jobs/reconcile-memberships", headers={"Authorization": f"Bearer {CRON_SECRET}"})
    assert r.json()["summary"] == {"checked": 1, "refreshed": 1, "failed": 0}
    assert superuser_query(cron.database_url, "SELECT count(*) FROM app.organization_memberships")[0][0] == 0


def test_reconcile_billing_job_reports_without_applying_by_default(cron: Harness) -> None:
    customer = make_pro(cron, "user_a")
    cron.billing.set_subscriptions(customer, [snapshot(customer, SubscriptionStatus.PAST_DUE)])
    r = cron.client.get("/api/v1/jobs/reconcile-billing", headers={"Authorization": f"Bearer {CRON_SECRET}"})
    assert r.json()["summary"] == {"customers": 1, "divergences": 1, "applied": False}
    status = superuser_query(cron.database_url, "SELECT status FROM app.subscriptions")[0][0]
    assert status == "active"


def test_vercel_cron_config_matches_the_job_registry(harness: Harness) -> None:
    """apps/api/vercel.json must schedule exactly the registered jobs, at their declared schedules."""
    config = json.loads((REPO_ROOT / "apps" / "api" / "vercel.json").read_text())
    scheduled = {c["path"]: c["schedule"] for c in config["crons"]}
    registered = {f"/api/v1/jobs/{j.name}": j.schedule for j in harness.container.kernel.jobs.all()}
    assert scheduled == registered


def test_cli_lists_jobs(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, harness: Harness) -> None:
    from app import cli

    monkeypatch.setattr("app.bootstrap.bootstrap", lambda: harness.container)
    monkeypatch.setattr(harness.container, "close", lambda: None)
    assert cli.main(["job"]) == 0
    names = {j["name"] for j in json.loads(capsys.readouterr().out)}
    assert names == {"drain-outbox", "prune-expired", "reconcile-billing", "reconcile-memberships"}


def test_repo_root_is_resolved() -> None:
    assert (Path(REPO_ROOT) / "supabase" / "migrations").is_dir()


def test_prune_deletes_processed_events_past_retention_only(cron: Harness) -> None:
    cron.client.post("/api/v1/projects", json={"name": "old"}, headers=cron.login("user_a"))
    _record(cron)  # stays pending
    auth = {"Authorization": f"Bearer {CRON_SECRET}"}
    assert cron.client.get("/api/v1/jobs/prune-expired", headers=auth).json()["summary"]["outbox_events"] == 0
    cron.clock.advance(days=cron.container.settings.outbox_retention_days, seconds=1)
    assert cron.client.get("/api/v1/jobs/prune-expired", headers=auth).json()["summary"]["outbox_events"] == 1
    assert [r[1] for r in outbox_rows(cron)] == ["pending"]
