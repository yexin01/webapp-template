"""Measurement harnesses outside the app: perf/ and the DEC-001 proof.

For perf/: its dataset and the identifiers k6 sends for it, its PERF-06 oracle,
its per-stage k6 results, and its exit report. The k6 parts run under Node:
perf/k6 keeps them in modules that import no k6 modules. For proofs/dec-001: the
database sampler of ``proof.py probe``, and the key files ``proof.py env`` writes
for probing a preview.

The PERF-06 oracle must tell exactly-once processing from reprocessing. The
webhook ledger alone cannot: a duplicate delivery that runs the
synchronization again leaves the same final row (status and attempts). The
harness therefore also counts Stripe re-reads. These tests drive the harness's
own ``seed``, ``build_app``, and ``webhook_report`` with concurrent duplicate
deliveries, in this suite's throwaway database (never ``app_perf``). Stripe is
the harness's in-memory mock.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import importlib.util
import json
import math
import shutil
import signal
import socket
import subprocess
import sys
import textwrap
import time
import uuid
from collections import Counter
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.parse import urlparse, urlunparse

import psycopg
import pytest
from cryptography.hazmat.primitives import serialization
from fastapi.testclient import TestClient

from app.kernel.infrastructure.config import REPO_ROOT
from app.kernel.webhooks import EventStatus
from app.modules.billing.adapters.repository import PgBillingRepository
from app.modules.identity.settings import is_rsa_public_key_pem, normalize_pem
from tests.conftest import superuser_query

USERS = 8  # PERF-06's four customers: cus_perf0001, cus_perf0003, ...
ORGS = 2
EVENTS = 12
COPIES = 3  # concurrent deliveries of each event, as PERF-06 sends them
EVENTS_PER_CUSTOMER = 2  # distinct events that contend for one customer's lock
# One sync slot per copy (slots = worker pool - 1), so the copies of an event
# reach the ledger row together instead of queueing for admission.
WORKER_POOL = COPIES + 1


@pytest.fixture(scope="module")
def perf_server() -> ModuleType:
    assert REPO_ROOT is not None  # a repository checkout, not a bundle
    spec = importlib.util.spec_from_file_location("perf_server", REPO_ROOT / "perf" / "server.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def harness(perf_server: ModuleType, db: str) -> Iterator[tuple[TestClient, Any]]:
    perf_server.seed(db, USERS, ORGS, 1)
    app, billing = perf_server.build_app(db, USERS, ORGS, 4, worker_pool_size=WORKER_POOL)
    yield TestClient(app, raise_server_exceptions=False), billing
    app.state.container.close()


def _node(module: str, expression: str, *args: Any) -> Any:
    """``expression`` evaluated under Node, with ``m`` the exports of perf/k6/``module`` and ``args`` these arguments.

    The k6 scripts keep their logic in modules that import no k6 modules, so it runs here as it runs in k6.
    """
    node = shutil.which("node")
    assert node is not None, "Node.js is required (pnpm needs it as well)"
    assert REPO_ROOT is not None
    url = json.dumps((REPO_ROOT / "perf" / "k6" / module).as_uri())
    script = (
        f"const m = await import({url});"
        "const args = JSON.parse(process.argv[1]);"
        f"console.log(JSON.stringify({expression}));"
    )
    result = subprocess.run(  # noqa: S603 (fixed interpreter and script)
        [node, "--input-type=module", "-e", script, json.dumps(args)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def k6_customers() -> list[str]:
    """The customer of each group of EVENTS_PER_CUSTOMER events, as perf/k6/scenarios.js chooses it."""
    groups = list(range(math.ceil(EVENTS / EVENTS_PER_CUSTOMER)))
    return list(_node("dataset.mjs", "args[0].map((g) => m.customerFor(g, args[1]))", groups, USERS))


def _payload(n: int, customers: list[str]) -> bytes:
    """The event perf/k6/scenarios.js sends for event ``n``."""
    customer = customers[n // EVENTS_PER_CUSTOMER]
    return json.dumps(
        {
            "id": f"evt_perf_{n:05d}",
            "object": "event",
            "type": "customer.subscription.updated",
            "created": int(time.time()),
            "data": {"object": {"id": f"sub_{customer[4:]}", "object": "subscription", "customer": customer}},
        }
    ).encode()


def _deliver_all(perf_server: ModuleType, client: TestClient, customers: list[str]) -> Counter[tuple[int, Any]]:
    """Every event COPIES times, consecutive copies at once. A 503 is retried, as Stripe would.

    Counts (HTTP status, ledger status in the body) per delivery.
    """
    secret = perf_server.WEBHOOK_SECRET.encode()

    def deliver(iteration: int) -> tuple[int, Any]:
        payload = _payload(iteration // COPIES, customers)
        for _ in range(20):
            stamp = int(time.time())
            digest = hmac.new(secret, f"{stamp}.".encode() + payload, hashlib.sha256).hexdigest()
            response = client.post(
                "/api/v1/webhooks/stripe",
                content=payload,
                headers={"Content-Type": "application/json", "Stripe-Signature": f"t={stamp},v1={digest}"},
            )
            if response.status_code != 503:
                return int(response.status_code), response.json().get("status")
            time.sleep(0.05)
        return 503, None

    with ThreadPoolExecutor(max_workers=2 * COPIES) as pool:
        return Counter(pool.map(deliver, range(EVENTS * COPIES)))


@pytest.mark.db
def test_the_llm_check_class_of_the_load_test_gets_its_200(
    perf_server: ModuleType, harness: tuple[TestClient, Any]
) -> None:
    """INCLUDE_LLM=1 expects 200 from POST /llm/check, an operator tool: the synthetic users are operators."""
    client, _ = harness
    token = f"perf-token-{perf_server.user_id(1)}"
    assert client.post("/api/v1/llm/check", headers={"Authorization": f"Bearer {token}"}).status_code == 200


@pytest.mark.db
def test_seeded_ids_keep_every_digit_beyond_the_padding_width(perf_server: ModuleType, db: str) -> None:
    """Indexes wider than the zero padding (10000 users, 1000 orgs) match user_id() and k6's padStart."""
    users, orgs = 10_002, 1_001
    counts = perf_server.seed(db, users, orgs, 0)
    assert (counts["users"], counts["organizations"], counts["organization_memberships"]) == (users, orgs, users)

    def present(table: str, column: str, value: str) -> bool:
        return bool(superuser_query(db, f"SELECT 1 FROM app.{table} WHERE {column} = %s", (value,)))

    assert present("users", "clerk_user_id", perf_server.user_id(10_000))
    assert present("users", "clerk_user_id", perf_server.user_id(1_000))
    assert present("organizations", "clerk_org_id", perf_server.org_id(1_000))
    assert present("billing_customers", "stripe_customer_id", perf_server.customer_id(10_000))
    assert present("subscriptions", "stripe_subscription_id", "sub_perf10000")
    # User 10000 belongs to organization 10000 % orgs, as the harness's directory says.
    member = superuser_query(
        db,
        "SELECT o.clerk_org_id FROM app.organization_memberships m "
        "JOIN app.users u ON u.id = m.user_id JOIN app.organizations o ON o.id = m.org_id "
        "WHERE u.clerk_user_id = %s",
        (perf_server.user_id(10_000),),
    )
    assert member == [(perf_server.org_id(10_000 % orgs),)]


def test_k6_addresses_the_users_and_customers_the_server_seeds(perf_server: ModuleType) -> None:
    """perf/k6/dataset.mjs names perf/server.py's users, organizations, and customers, past the padding width too."""
    users, orgs = 10_002, 1_001
    indexes = [0, 1, 2, 999, 1_000, 1_001, 9_999, 10_000, 10_001]
    k6_users = _node("dataset.mjs", "args[0].map((i) => m.syntheticUser(i, args[1]))", indexes, orgs)
    tokens = perf_server.TokenTable(users)
    for i, user in zip(indexes, k6_users, strict=True):
        assert tokens.verify(user["token"]).external_user_id == perf_server.user_id(i)
        assert user["org"] == perf_server.org_id(i % orgs)
        assert user["pro"] is (i % 2 == 0)  # seed() gives the even-numbered users a subscription
    # PERF-06 cycles through the odd-numbered users' customers, which have no subscription.
    groups = [0, 1, 4_999, 5_000, 5_001]
    customers = _node("dataset.mjs", "args[0].map((g) => m.customerFor(g, args[1]))", groups, users)
    assert customers == [perf_server.customer_id(i) for i in (1, 3, 9_999, 10_001, 1)]


@pytest.mark.db
def test_concurrent_duplicates_resync_each_event_exactly_once(
    perf_server: ModuleType, harness: tuple[TestClient, Any], db: str, k6_customers: list[str]
) -> None:
    client, billing = harness
    assert _deliver_all(perf_server, client, k6_customers) == {(200, "processed"): EVENTS * COPIES}
    report = perf_server.webhook_report(db, billing)
    assert report["billing_events"] == {"processed": {"events": EVENTS, "claims": EVENTS * COPIES}}
    assert report["stripe_resyncs"] == EVENTS
    assert report["stripe_calls"] == {"list_customer_subscriptions": EVENTS}
    assert report["exactly_once"] is True


@pytest.mark.db
def test_a_deployed_mock_with_no_stripe_state_still_processes_every_delivery(
    perf_server: ModuleType, db: str, monkeypatch: pytest.MonkeyPatch, k6_customers: list[str]
) -> None:
    """A preview with mocked billing starts with an empty Stripe mock that nothing can fill.

    PERF-06's customers must not need it: a stored subscription that is not
    final would be re-read by its ID, rejected by the empty mock, and every
    delivery would come back needs_reconciliation.
    """
    monkeypatch.setattr(perf_server.InMemoryBillingProvider, "set_subscriptions", lambda *_: None)
    perf_server.seed(db, USERS, ORGS, 1)
    app, billing = perf_server.build_app(db, USERS, ORGS, 4, worker_pool_size=WORKER_POOL)
    try:
        delivered = _deliver_all(perf_server, TestClient(app, raise_server_exceptions=False), k6_customers)
    finally:
        app.state.container.close()
    assert delivered == {(200, "processed"): EVENTS * COPIES}
    assert perf_server.webhook_report(db, billing)["exactly_once"] is True


@pytest.mark.db
def test_reprocessed_duplicates_are_reported_although_the_ledger_looks_identical(
    perf_server: ModuleType,
    harness: tuple[TestClient, Any],
    db: str,
    monkeypatch: pytest.MonkeyPatch,
    k6_customers: list[str],
) -> None:
    """Control: a ledger claim that never sees the earlier delivery must fail the report."""
    claim = PgBillingRepository.claim_billing_event

    def forgetful_claim(self: PgBillingRepository, event: Any) -> EventStatus:
        claim(self, event)
        return EventStatus.RECEIVED

    monkeypatch.setattr(PgBillingRepository, "claim_billing_event", forgetful_claim)
    client, billing = harness
    assert _deliver_all(perf_server, client, k6_customers) == {(200, "processed"): EVENTS * COPIES}
    report = perf_server.webhook_report(db, billing)
    # Exactly what the correct run leaves behind ...
    assert report["billing_events"] == {"processed": {"events": EVENTS, "claims": EVENTS * COPIES}}
    # ... but every copy re-synchronized.
    assert report["stripe_resyncs"] == EVENTS * COPIES
    assert report["exactly_once"] is False


def _stage_results(profile: list[dict[str, Any]], metrics: dict[str, Any], elapsed: float) -> dict[str, Any]:
    """perf/k6/summary.mjs's stageResults."""
    return dict(_node("summary.mjs", "m.stageResults(...args)", profile, metrics, elapsed))


def _stage_metrics(stage: int, started: int, *, errors: float = 0.0, p95: float = 20.0) -> dict[str, Any]:
    """k6's summary values for one stage's selectors: 8 requests per iteration.

    A stage without requests (idle, or never reached) is reported as k6 does:
    zero counts, and a trend of zeros.
    """
    requests = 8 * started
    failed = round(errors * requests)
    latency = (2.0, 10.0, 12.0, p95 / 2, p95, 2 * p95, 3 * p95) if requests else (0.0,) * 7
    trend = dict(zip(("min", "med", "avg", "p(90)", "p(95)", "p(99)", "max"), latency, strict=True))
    return {
        f"iteration_starts{{stage:{stage}}}": {"values": {"count": started}},
        f"http_reqs{{stage:{stage}}}": {"values": {"count": requests}},
        f"http_req_failed{{stage:{stage}}}": {
            "values": {"rate": failed / requests if requests else 0.0, "passes": failed, "fails": requests - failed}
        },
        f"http_req_duration{{stage:{stage}}}": {"values": trend},
    }


# PERF-03 with MAX_RATE=100 and STEP=60s: six held levels (k6 stages 1, 3, ..., 11).
STRESS_PROFILE = [
    {"stage": stage, "from": rate, "to": rate, "seconds": 60}
    for stage, rate in zip((1, 3, 5, 7, 9, 11), (10, 20, 40, 60, 80, 100), strict=True)
]


def test_k6_stage_results_cover_only_the_seconds_an_aborted_run_lasted() -> None:
    """At 80 it/s the target fails 40% of requests; k6 aborts 15 s into that step, at 255 s of 360."""
    metrics: dict[str, Any] = {}
    for stage, rate in ((1, 10), (3, 20), (5, 40), (7, 60)):
        metrics |= _stage_metrics(stage, rate * 60)
    metrics |= _stage_metrics(9, 80 * 15, errors=0.4, p95=900.0)
    metrics |= _stage_metrics(11, 0)
    result = _stage_results(STRESS_PROFILE, metrics, 255.0)

    assert (result["planned_duration_s"], result["aborted"]) == (360, True)
    stages = {s["stage"]: s for s in result["stages"]}
    assert (stages[7]["status"], stages[7]["achieved_iterations_per_s"], stages[7]["delivered"]) == ("complete", 60, 1)
    # The step the run stopped in: every offered iteration started, while it lasted.
    cut = stages[9]
    assert (cut["status"], cut["covered_seconds"], cut["offered_iterations"]) == ("cut_short", 15, 1200)
    assert (cut["achieved_iterations_per_s"], cut["requests_per_s"], cut["delivered"]) == (80, 640, 1)
    assert (cut["error_rate"], cut["latency_ms"]) == (0.4, {"p50": 10, "p95": 900, "p99": 1800})
    # A step the run never reached is not a result.
    never = stages[11]
    assert (never["status"], never["covered_seconds"], never["offered_iterations"]) == ("not_reached", 0, 0)
    assert never["achieved_iterations_per_s"] is None
    assert never["delivered"] is None
    # k6 reports its selectors with zero counts: no error rate or latency to report.
    assert (never["error_rate"], never["latency_ms"]) == (None, None)


def test_k6_stage_results_of_a_run_cut_short_in_a_ramp() -> None:
    """PERF-04 shape: idle 10 s, then a 5 s ramp to 60 it/s; the run stops 2.5 s into the ramp."""
    profile = [
        {"stage": 0, "from": 0, "to": 0, "seconds": 10},
        {"stage": 1, "from": 0, "to": 60, "seconds": 5},
        {"stage": 2, "from": 60, "to": 60, "seconds": 30},
    ]
    metrics = _stage_metrics(0, 0) | _stage_metrics(1, 37) | _stage_metrics(2, 0)
    result = _stage_results(profile, metrics, 12.5)
    idle, ramp, held = result["stages"]
    # The idle stage offered and sent nothing.
    assert (idle["status"], idle["achieved_iterations_per_s"], idle["delivered"]) == ("complete", 0, None)
    assert (idle["error_rate"], idle["latency_ms"]) == (None, None)
    # 0 to 30 it/s over 2.5 s offered 37.5 iterations.
    assert (ramp["status"], ramp["offered_iterations"], ramp["delivered"]) == ("cut_short", 38, 0.987)
    assert (held["status"], held["error_rate"], held["latency_ms"]) == ("not_reached", None, None)


# PERF-04 at SPIKE_RATE=5: idle 10 s, a 5 s ramp to 5 it/s, 30 s held, and a 10 s
# ramp down, 55 s in all. The offered total is 187.5 iterations.
SPIKE_5_PROFILE = [
    {"stage": 0, "from": 0, "to": 0, "seconds": 10},
    {"stage": 1, "from": 0, "to": 5, "seconds": 5},
    {"stage": 2, "from": 5, "to": 5, "seconds": 30},
    {"stage": 3, "from": 5, "to": 0, "seconds": 10},
]


def test_k6_stage_results_of_a_run_that_ends_before_its_planned_end_with_its_last_iteration() -> None:
    """k6 starts iteration k once the offered load reaches k, and ends the run after the last whole one.

    At SPIKE_RATE=5 the 187th is due 1.4 s before the planned end: k6 1.3.0
    ended such a run, with every iteration started, at 53.6 s.
    """
    metrics = _stage_metrics(0, 0) | _stage_metrics(1, 12) | _stage_metrics(2, 150) | _stage_metrics(3, 25)
    result = _stage_results(SPIKE_5_PROFILE, metrics, 53.6)
    assert (result["planned_duration_s"], result["aborted"]) == (55, False)
    assert [(s["status"], s["covered_seconds"], s["delivered"]) for s in result["stages"][2:]] == [
        ("complete", 30, 1),
        ("complete", 10, 1),
    ]
    assert [s["status"] for s in result["stages"][:2]] == ["complete", "complete"]

    # Stopped before the last iteration was due: the ramp down was cut short.
    metrics |= _stage_metrics(3, 23)
    stopped = _stage_results(SPIKE_5_PROFILE, metrics, 52.0)
    assert stopped["aborted"] is True
    assert [s["status"] for s in stopped["stages"]] == ["complete", "complete", "complete", "cut_short"]


def test_k6_stage_results_of_a_complete_run_use_whole_stages() -> None:
    metrics: dict[str, Any] = {}
    for stage, rate in zip((1, 3, 5, 7, 9, 11), (10, 20, 40, 60, 80, 100), strict=True):
        metrics |= _stage_metrics(stage, rate * 60)
    # k6 waits for the last iterations after the planned 360 s.
    result = _stage_results(STRESS_PROFILE, metrics, 362.4)
    assert result["aborted"] is False
    assert [(s["status"], s["achieved_iterations_per_s"], s["delivered"]) for s in result["stages"]] == [
        ("complete", rate, 1) for rate in (10, 20, 40, 60, 80, 100)
    ]


def _accepts_connections(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
            return True
    except OSError:
        return False


@pytest.mark.parametrize("stop", [signal.SIGINT, signal.SIGTERM], ids=["ctrl_c", "sigterm"])
def test_stopping_the_server_by_signal_still_prints_the_exit_report(stop: signal.Signals) -> None:
    """SIGTERM that reaches the server (kill, timeout) prints the report and the PERF-06 verdict, as Ctrl+C does."""
    assert REPO_ROOT is not None
    with socket.socket() as free:
        free.bind(("127.0.0.1", 0))
        port = free.getsockname()[1]
    # The harness's own serve(), followed by what main() does after it: the report, then the verdict.
    driver = textwrap.dedent(f"""
        import importlib.util, sys
        from starlette.applications import Starlette
        spec = importlib.util.spec_from_file_location("perf_server", {str(REPO_ROOT / "perf" / "server.py")!r})
        server = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(server)
        try:
            server.serve(Starlette(), {port})
        finally:
            print("exit report", flush=True)
        sys.exit("PERF-06 verdict")
    """)
    proc = subprocess.Popen(  # noqa: S603 (fixed interpreter and script)
        [sys.executable, "-c", driver], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    try:
        deadline = time.monotonic() + 60
        while not _accepts_connections(port):
            assert proc.poll() is None, proc.communicate()
            assert time.monotonic() < deadline, "the server did not start listening"
            time.sleep(0.05)
        proc.send_signal(stop)
        out, err = proc.communicate(timeout=60)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()
    # Exited with the verdict (status 1), not killed by the signal (a negative status).
    assert proc.returncode == 1, (proc.returncode, out, err)
    assert "exit report" in out
    assert "PERF-06 verdict" in err


@pytest.fixture(scope="module")
def dec001_proof() -> ModuleType:
    assert REPO_ROOT is not None
    path = REPO_ROOT / "proofs" / "dec-001" / "proof.py"
    if not path.is_file():
        pytest.skip("proofs/dec-001 was deleted; its README says to, once DEC-001 is resolved.")
    spec = importlib.util.spec_from_file_location("dec001_proof", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.db
def test_dec001_probe_counts_database_clients_but_not_its_own_sampler(dec001_proof: ModuleType, db: str) -> None:
    """A hosted server's admin URL often names the probed database itself (Supabase's ``postgres``).

    The sampler's own connection is then a client of that database too. Counted,
    it would satisfy connection_bound's ``0 < observed`` without any API connection.
    """
    name = f"proof_sampler_{uuid.uuid4().hex[:10]}"
    url = urlunparse(urlparse(db)._replace(path=f"/{name}"))
    superuser_query(db, f'CREATE DATABASE "{name}"')
    try:
        # Two clients, as the API's pools would hold, then the sampler on the same database.
        with (
            psycopg.connect(url),
            psycopg.connect(url),
            dec001_proof.connection_sampler(url, None, name) as peaks,
        ):
            deadline = time.monotonic() + 10
            while peaks["postgres_backends"] < 2:
                assert time.monotonic() < deadline, "the sampler took no sample"
                time.sleep(0.02)
        assert peaks["postgres_backends"] == 2
    finally:
        superuser_query(
            db,
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()",
            (name,),
        )
        superuser_query(db, f'DROP DATABASE IF EXISTS "{name}"')


def test_dec001_env_writes_the_public_key_a_preview_can_use(dec001_proof: ModuleType, tmp_path: Path) -> None:
    """A preview's CLERK_JWT_KEY comes from jwt-public.pem: proof.env's one-line copy ends in a ``\\n`` escape.

    The web app's configuration check (which `next build` runs) refuses that
    one-line form, quoted or not; both apps accept the multi-line PEM.
    """
    keys = tmp_path / "keys"
    dec001_proof.cmd_env(
        argparse.Namespace(
            out=str(tmp_path / "proof.env"),
            keys=str(keys),
            port=3910,
            database_url="postgresql://unused@127.0.0.1:1/unused",
        )
    )
    public = (keys / "jwt-public.pem").read_text(encoding="utf-8")
    private = serialization.load_pem_private_key((keys / "jwt-private.pem").read_bytes(), password=None)
    assert public == (
        private.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    assert is_rsa_public_key_pem(normalize_pem(public))
    # The same key the local run configures, so one proof.json's tokens verify against either.
    assert dec001_proof.read_env(tmp_path / "proof.env")["CLERK_JWT_KEY"] == public
