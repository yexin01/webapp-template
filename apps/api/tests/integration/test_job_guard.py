"""A job started over HTTP runs once at a time, and no sooner than JOB_MIN_SPACING_SECONDS after its last start (SEC-09).

GET /api/v1/jobs/<name> runs the job on every authorized call, so whoever holds CRON_SECRET could start the same
job many times at once (reconcile-billing makes up to BILLING_RECONCILE_BATCH_SIZE Stripe calls a run). The guard
is a row per job in PostgreSQL, claimed in one statement and released in another, so it works across instances
and through a transaction pooler. The command line is not guarded.
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import timedelta
from typing import Any

import psycopg
import pytest

from app.kernel.application.job_guard import LEASE_SECONDS, JobGuard
from app.kernel.jobs import Job
from tests.conftest import Harness, superuser_query

pytestmark = pytest.mark.db

CRON_SECRET = "cron-secret-test_only-job-guard-0123456789abcd"  # test-only value
AUTH = {"Authorization": f"Bearer {CRON_SECRET}"}
SPACING = 60


class CountingJob:
    """A job that counts its runs, can be told to fail, and can wait for the test before it ends."""

    def __init__(self, *, fail: bool = False, gated: bool = False) -> None:
        self.runs = 0
        self.fail = fail
        self.started = threading.Event()
        self.proceed = threading.Event()
        if not gated:
            self.proceed.set()

    def __call__(self) -> Mapping[str, int | str | bool]:
        self.runs += 1
        self.started.set()
        assert self.proceed.wait(10), "the test never let the job end"
        if self.fail:
            raise RuntimeError("the job failed")
        return {"runs": self.runs}


@pytest.fixture
def cron(make_harness: Callable[..., Harness]) -> Harness:
    return make_harness(settings_overrides={"cron_secret": CRON_SECRET})


def register(h: Harness, name: str, job: CountingJob) -> None:
    h.container.kernel.jobs.register(Job(name, "A job of the test.", "0 1 * * *", job))


def call(h: Harness, name: str, headers: dict[str, str] | None = None) -> Any:
    return h.client.get(f"/api/v1/jobs/{name}", headers=AUTH if headers is None else headers)


def job_rows(h: Harness) -> list[tuple[Any, ...]]:
    return superuser_query(h.database_url, "SELECT job_name, started_at, lease_until FROM app.job_runs")


def test_two_concurrent_requests_run_the_job_once(cron: Harness) -> None:
    job = CountingJob(gated=True)
    register(cron, "gated", job)
    first: list[Any] = []
    thread = threading.Thread(target=lambda: first.append(call(cron, "gated")))
    thread.start()
    try:
        assert job.started.wait(10)
        second = call(cron, "gated")
    finally:
        job.proceed.set()
        thread.join(10)
    assert second.status_code == 409
    assert second.headers["content-type"] == "application/problem+json"
    assert (second.json()["code"], second.json()["request_id"] is not None) == ("CONFLICT", True)
    assert "retry-after" not in second.headers
    assert [r.status_code for r in first] == [200]
    assert first[0].json()["summary"] == {"runs": 1}
    assert job.runs == 1


def test_a_burst_of_requests_runs_the_job_once(cron: Harness) -> None:
    job = CountingJob(gated=True)
    register(cron, "burst", job)
    answers: queue.Queue[int] = queue.Queue()
    start = threading.Barrier(8)

    def request() -> None:
        start.wait(10)
        answers.put(call(cron, "burst").status_code)

    threads = [threading.Thread(target=request) for _ in range(8)]
    for thread in threads:
        thread.start()
    try:
        assert job.started.wait(10)
        # The seven that lost answer while the winner is still running.
        assert [answers.get(timeout=30) for _ in range(7)] == [409] * 7
    finally:
        job.proceed.set()
        for thread in threads:
            thread.join(10)
    assert answers.get(timeout=30) == 200
    assert job.runs == 1


def test_a_request_inside_the_spacing_is_refused_until_it_has_passed(cron: Harness) -> None:
    job = CountingJob()
    register(cron, "spaced", job)
    assert call(cron, "spaced").status_code == 200
    soon = call(cron, "spaced")
    assert soon.status_code == 429
    assert soon.headers["content-type"] == "application/problem+json"
    assert soon.json()["code"] == "RATE_LIMITED"
    assert int(soon.headers["retry-after"]) == SPACING
    cron.clock.advance(seconds=SPACING - 1)
    almost = call(cron, "spaced")
    assert (almost.status_code, int(almost.headers["retry-after"])) == (429, 1)
    assert job.runs == 1
    cron.clock.advance(seconds=1)
    assert call(cron, "spaced").status_code == 200
    assert job.runs == 2


def test_the_spacing_is_per_job(cron: Harness) -> None:
    one, other = CountingJob(), CountingJob()
    register(cron, "one", one)
    register(cron, "other", other)
    assert [call(cron, name).status_code for name in ("one", "other", "one")] == [200, 200, 429]
    assert (one.runs, other.runs) == (1, 1)


def test_a_failed_run_does_not_leave_the_job_locked(cron: Harness) -> None:
    job = CountingJob(fail=True)
    register(cron, "failing", job)
    failed = call(cron, "failing")
    assert failed.status_code == 500
    assert failed.json()["code"] == "INTERNAL_ERROR"
    [(_, started_at, lease_until)] = job_rows(cron)
    assert (started_at, lease_until <= cron.clock.now()) == (cron.clock.now(), True)  # released: it holds no job
    # Only the spacing refuses the next call, not a run that never ended.
    again = call(cron, "failing")
    assert (again.status_code, again.json()["code"]) == (429, "RATE_LIMITED")
    cron.clock.advance(seconds=SPACING)
    job.fail = False
    assert call(cron, "failing").status_code == 200
    assert job.runs == 2


def test_spacing_zero_leaves_single_flight_only(make_harness: Callable[..., Harness]) -> None:
    h = make_harness({"cron_secret": CRON_SECRET, "job_min_spacing_seconds": 0})
    job = CountingJob()
    register(h, "unspaced", job)
    assert [call(h, "unspaced").status_code for _ in range(3)] == [200, 200, 200]
    gated = CountingJob(gated=True)
    register(h, "unspaced-gated", gated)
    first: list[Any] = []
    thread = threading.Thread(target=lambda: first.append(call(h, "unspaced-gated")))
    thread.start()
    try:
        assert gated.started.wait(10)
        assert call(h, "unspaced-gated").status_code == 409
    finally:
        gated.proceed.set()
        thread.join(10)
    assert [r.status_code for r in first] == [200]
    assert call(h, "unspaced-gated").status_code == 200  # no spacing, and the job is free again


def test_a_run_that_never_ended_holds_the_job_until_its_lease_runs_out(cron: Harness) -> None:
    """A crash or a platform timeout skips the release. The lease is longer than any function may run."""
    job = CountingJob()
    register(cron, "crashed", job)
    store = cron.container.kernel.store
    now = cron.clock.now()
    with store.as_system() as t:
        assert t.repo.claim_job_run(
            "crashed", now=now, lease_until=now + timedelta(seconds=LEASE_SECONDS), spaced_before=now
        )
    cron.clock.advance(seconds=SPACING * 2)  # past the spacing, inside the lease
    held = call(cron, "crashed")
    assert (held.status_code, held.json()["code"]) == (409, "CONFLICT")
    assert job.runs == 0
    cron.clock.advance(seconds=LEASE_SECONDS)
    assert call(cron, "crashed").status_code == 200
    assert job.runs == 1


def test_a_run_whose_lease_ran_out_cannot_release_the_run_that_replaced_it(cron: Harness) -> None:
    guard = JobGuard(cron.container.kernel.store, cron.clock, min_spacing_seconds=0)
    store = cron.container.kernel.store
    old = cron.clock.now()
    with store.as_system() as t:
        t.repo.claim_job_run("fenced", now=old, lease_until=old + timedelta(seconds=LEASE_SECONDS), spaced_before=old)
    cron.clock.advance(seconds=LEASE_SECONDS + 1)
    with guard.exclusive("fenced"):  # the replacement
        [(_, started_at, lease_until)] = job_rows(cron)
        assert started_at > old
        # The old run finally ends and releases: it holds nothing any more, so nothing changes.
        with store.as_system() as t:
            t.repo.release_job_run("fenced", old, cron.clock.now())
        assert job_rows(cron) == [("fenced", started_at, lease_until)]
        assert lease_until > cron.clock.now()
    assert job_rows(cron)[0][2] <= cron.clock.now()  # its own release works


def test_a_wrong_secret_an_unknown_name_and_a_disabled_endpoint_claim_nothing(
    cron: Harness, make_harness: Callable[..., Harness]
) -> None:
    register(cron, "known", CountingJob())
    assert call(cron, "known", {}).status_code == 401
    assert call(cron, "known", {"Authorization": "Bearer wrong"}).status_code == 401
    assert call(cron, "nobody").status_code == 404
    assert call(cron, "x" * 500).status_code == 404  # a name is only recorded once the job exists
    disabled = make_harness()
    assert call(disabled, "prune-expired").status_code == 404
    assert job_rows(cron) == []
    assert job_rows(disabled) == []


def test_the_shipped_jobs_answer_through_the_guard(cron: Harness) -> None:
    for name in ("drain-outbox", "prune-expired", "reconcile-billing", "reconcile-memberships"):
        assert call(cron, name).status_code == 200, name
        assert call(cron, name).status_code == 429, name
    assert sorted(name for name, _, _ in job_rows(cron)) == [
        "drain-outbox",
        "prune-expired",
        "reconcile-billing",
        "reconcile-memberships",
    ]


def test_the_command_line_is_not_guarded(
    cron: Harness, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from app import cli

    monkeypatch.setattr("app.bootstrap.bootstrap", lambda: cron.container)
    monkeypatch.setattr(cron.container, "close", lambda: None)
    assert [cli.main(["job", "prune-expired"]) for _ in range(3)] == [0, 0, 0]
    assert capsys.readouterr().out.count('"job": "prune-expired"') == 3
    assert job_rows(cron) == []
    # ... and neither it nor the guard stops the other.
    assert call(cron, "prune-expired").status_code == 200
    assert cli.main(["job", "prune-expired"]) == 0


@contextmanager
def as_role(url: str, role: str) -> Iterator[psycopg.Connection[Any]]:
    with psycopg.connect(url) as conn, conn.transaction():
        conn.execute(f"SET LOCAL ROLE {role}")  # type: ignore[arg-type]
        yield conn


def test_only_the_worker_role_reaches_the_table(cron: Harness) -> None:
    register(cron, "private", CountingJob())
    assert call(cron, "private").status_code == 200
    for statement in (
        "SELECT * FROM app.job_runs",
        "INSERT INTO app.job_runs (job_name, started_at, lease_until) VALUES ('x', now(), now())",
        "UPDATE app.job_runs SET lease_until = now()",
        "DELETE FROM app.job_runs",
    ):
        with pytest.raises(psycopg.errors.InsufficientPrivilege), as_role(cron.database_url, "app_api") as conn:
            conn.execute(statement)  # type: ignore[arg-type]
    with as_role(cron.database_url, "app_worker") as conn:
        assert conn.execute("SELECT count(*) FROM app.job_runs").fetchone() == (1,)
    with pytest.raises(psycopg.errors.InsufficientPrivilege), as_role(cron.database_url, "app_worker") as conn:
        conn.execute("DELETE FROM app.job_runs")
