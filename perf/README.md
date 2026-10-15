# Performance tests

Scripts for the scenarios in spec §15.2 (PERF-01 to PERF-07). Read the safety rules before running anything.

> **Performance targets are an adopter decision (DEC-003, [ADR-0002](../dev-docs/adr/0002-organization-entitlements-contacts-adopter-decisions.md)).** The template ships none. Results are baselines and observed limits. Never present local numbers as deployment performance. Use [run-plan-template.md](run-plan-template.md) to plan deployed runs and to propose targets.

## Safety rules

- **Local by default.** `pnpm perf:run` refuses any target other than `localhost`, `127.0.0.1`, or `host.docker.internal`.
- **Deployed environments** (preview only) need an approved run plan first ([run-plan-template.md](run-plan-template.md)). The plan states:
  - rate and duration caps;
  - abort criteria;
  - cost acknowledgement;
  - the isolated database and its synthetic dataset.

  Only then set `PERF_ALLOW_REMOTE=1`. A deployed target also needs the synthetic users' session tokens (`PERF_USERS_FILE`) and, for PERF-06, its test-only webhook secret (`PERF_WEBHOOK_SECRET`). A preview behind Vercel Deployment Protection also needs `VERCEL_AUTOMATION_BYPASS_SECRET`. The run plan explains how to get them. Without the tokens or the webhook secret, the run refuses to start: the built-in ones work only with `perf/server.py`. None of these values is ever printed, and a `BASE_URL` with a user name or password is refused.
- **Production load testing is prohibited** without explicit authorization and an approved safety plan.
- **Every scenario aborts automatically** when more than 5% of requests fail (`http_req_failed`, evaluated after 10 s). The rate is cumulative over the run, so late in a long soak a short surge may stay under it; check the per-stage results. A `401` or a rejected webhook signature stops the run at once. A `401` from `/health` or the webhook route on a deployed target means Deployment Protection, not a bad token. Session tokens are short-lived (about 60 s by default), so a deployed run longer than their lifetime stops at the first `401` (see the run plan).
- **Raw output goes to `perf/results/`**, which is git-ignored. Summaries go to `dev-docs/evidence/`.

## How it works

`perf/server.py` runs the real API (routes, services, repositories, PostgreSQL with row-level security) against a dedicated local database, `app_perf`. The database is dropped, re-created, migrated, and seeded on every start, and every external integration is mocked: Stripe, Clerk directory, LLM, and telemetry.

**Authentication (ENV-08).** The launcher lives outside the application package and is never deployed. It composes the app with a token table that maps `perf-token-perf_user_NNNN` to synthetic users, the same technique the test suite uses. The application itself has no test-only authentication path.

**Dataset.** The dataset is fully deterministic: identifiers derive from indexes, and there is no randomness.

- Defaults: 200 users, 20 organizations, and 50 projects per tenant, which comes to 11,000 projects.
- Every user is a member of one organization and has a billing customer (`cus_perfNNNN`).
- Even-numbered users have an active Pro subscription. Odd-numbered users have none; PERF-06 sends its events for their customers.
- Change the size with `--users`, `--orgs`, and `--projects`.

**Load generator.** k6 1.3.0 runs in Docker (`grafana/k6:1.3.0`), so nothing needs to be installed.

**Docker Desktop is required** (macOS or Windows). The perf server listens on `127.0.0.1` only, and k6 in its container reaches it through `host.docker.internal`, which Docker Desktop routes to the host's loopback. Docker Engine on Linux defines no `host.docker.internal`, and even with `--add-host=host.docker.internal:host-gateway` it cannot reach a port bound to `127.0.0.1`. On Linux, run k6 on the host network instead of `pnpm perf:run`, with the same variables:

```bash
docker run --rm --network host -v "$PWD/perf/k6:/scripts:ro" -v "$PWD/perf/results:/results" \
  -e BASE_URL=http://127.0.0.1:8001 -e SCENARIO=baseline grafana/k6:1.3.0 run --quiet /scripts/scenarios.js
```

This skips `pnpm perf:run`'s target and credential guards, so use it for the local server only.

**Request mix.** Each iteration hits every endpoint class once as one synthetic user, with no think time. The user is a hash of `SEED` (default `1`) and the iteration number, so a rerun with the same `SEED` sends the same requests in the same order. Only the timing varies. Each results file records the seed.

| Class | Request |
|---|---|
| `health` | `GET /health` |
| `auth_read` | `GET /me` |
| `tenant_list` | `GET /projects?limit=20` |
| `org_list` | `GET /orgs/{org}/projects?limit=20` |
| `billing_status` | `GET /billing/status` |
| `entitlement_gated` | `GET /pro/insight` (`200` for Pro users, `403` expected for Free users) |
| `auth_write` | `POST /projects`, then `DELETE` |
| `llm_check` | `POST /llm/check` (mocked provider). Only with `INCLUDE_LLM=1` |

## Commands

```bash
pnpm db:up
pnpm perf:server                      # terminal 1: seeds app_perf and serves on 127.0.0.1:8001
SCENARIO=baseline pnpm perf:run       # terminal 2
```

| Scenario | ID | Command | Default shape |
|---|---|---|---|
| Baseline | PERF-01 | `SCENARIO=baseline pnpm perf:run` | 5 iterations/s (about 40 requests/s) for 60 s |
| Load | PERF-02 | `SCENARIO=load RATE=20 pnpm perf:run` | Steps of 1 min at `RATE`/4 and `RATE`/2, 2 min at `RATE`, then 30 s down to 0 (4.5 min) |
| Stress | PERF-03 | `SCENARIO=stress MAX_RATE=100 STEP=1m pnpm perf:run` | Six steps of `STEP` each, held at 10, 20, 40, 60, 80, and 100% of `MAX_RATE`; aborts on errors |
| Spike | PERF-04 | `SCENARIO=spike SPIKE_RATE=60 pnpm perf:run` | Idle, then a jump to `SPIKE_RATE` for 30 s |
| Soak | PERF-05 | `SCENARIO=soak RATE=10 DURATION=30m pnpm perf:run` | Constant moderate load |
| Webhooks | PERF-06 | `SCENARIO=webhooks WEBHOOK_EVENTS=100 DUPLICATES=3 EVENTS_PER_CUSTOMER=4 pnpm perf:run` | The `DUPLICATES` copies of each signed event arrive at the same time and contend for its ledger row. Each customer gets `EVENTS_PER_CUSTOMER` distinct events at the same time, which contend for the customer lock. 20 VUs (`WEBHOOK_VUS`, at least `DUPLICATES`). Every delivery must come back `processed`. See [PERF-06](#perf-06-webhooks) |
| Degradation | PERF-07 | Restart the server with a knob below, then `SCENARIO=baseline INCLUDE_LLM=1 LABEL=<knob> pnpm perf:run` | Compare against a control run with no knobs |

`RATE`, `MAX_RATE`, `SPIKE_RATE`, `MAX_VUS`, and `WEBHOOK_VUS` are whole numbers; `DURATION` and `STEP` are durations such as `90s` or `5m`. `MAX_VUS` caps the VUs of every arrival-rate scenario.

**PERF-07 server knobs.** All of them are off by default:

| Knob | Effect |
|---|---|
| `--db-latency-ms 20` | Every API-to-PostgreSQL round trip passes through a loopback proxy that adds 20 ms, like a remote database |
| `--provider-latency-ms 200` | The mocked identity directory, Stripe API, and LLM provider wait 200 ms per call |
| `--telemetry [--traces-sample-rate 0.1]` | The real PostHog and Sentry SDKs run, sending to a loopback sink. Nothing leaves the machine; the sink counts uploads on exit |

Pass server options directly, without `--`: `pnpm perf:server --db-latency-ms 20`. With pnpm 9, `pnpm perf:server -- --db-latency-ms 20` passes the `--` on, and the server rejects it.

**Pools.** `--pool-size` sets the request pool (default 10 here; the app default is 5). `--worker-pool-size` sets the system pool (the app default, 3, when omitted). Billing synchronization runs `worker pool - 1` webhooks at a time (the sync slots), each holding a worker connection. Other deliveries queue for a slot for up to `DATABASE_CONNECT_TIMEOUT_SECONDS`. The server prints all three numbers when it starts and when it stops.

Stop the server with Ctrl+C. It then prints the peak number of database connections, the pool sizes, telemetry sink counts, and the PERF-06 webhook report, and exits non-zero if the report shows a PERF-06 failure. SIGTERM does the same, but only when it reaches the server process. Whether SIGTERM sent to the `pnpm perf:server` process gets there depends on the shell pnpm runs scripts with. On macOS it stops pnpm alone, and the server keeps running in the background, with no report. To stop a scripted run, start the server with `cd apps/api && uv run python ../../perf/server.py` and the same options instead. uv passes SIGTERM on and exits with the server's status, so stop it with `kill`, or run it under `timeout --preserve-status <duration>`.

### PERF-06 webhooks

- **What proves exactly-once.** Each synchronization re-reads the customer's subscriptions from the mocked Stripe API once. So the server's exit report compares `stripe_resyncs` (those re-reads; `stripe_calls` lists every mocked Stripe call) with `events_processed` (ledger rows with status `processed`). If they differ, it prints `PERF-06 FAILED` and exits non-zero. The ledger counts alone (`billing_events`: events and `claims` by status) cannot show double processing: a duplicate that ran the synchronization again leaves the same final row. A failed synchronization that is retried also adds a re-read, but its delivery got a non-200, which k6 reports.
- **A deployed target cannot measure double processing.** Only the local server counts the mocked Stripe calls. Against a preview, PERF-06 measures latency and acknowledgements only.
- **Every delivery must do synchronization work.** k6 counts the webhook responses by result (`webhooks.results` in the results file) and fails the run unless all of them are `processed`. The events name the odd-numbered users' customers (`cus_perf0001`, `cus_perf0003`, and so on), which have no subscription, so each synchronization needs only an empty listing from the mocked Stripe API. A deployed preview, whose mocked Stripe starts empty, therefore processes them as the local server does. An event comes back `needs_reconciliation` for a customer the server did not seed (for example when k6's `USERS` exceeds the server's `--users`), and for a customer with a stored subscription that is not final, which an empty mock cannot return. A delivery that waited too long for a synchronization slot gets `503` (`busy`); Stripe would retry it, k6 does not.
- **What the sync slots cap.** At most `worker pool - 1` deliveries synchronize at once, so that is how many copies of an event, or events for one customer, contend in the database at the same time. The rest wait for a slot, not for the ledger row or the customer lock. With the app default (2 slots), at most two copies of an event reach the database at a time. To make all `DUPLICATES` copies contend there together, start the server with `--worker-pool-size` of at least `DUPLICATES + 1`, or `WEBHOOK_VUS + 1` for every delivery in flight at once.
- **Default pool, recorded 2026-10-15 (local, mocked):** two runs of the default PERF-06 shape (20 VUs, 100 events × 3 copies, `USERS=40 ORGS=4`) against `perf/server.py --users 40 --orgs 4 --projects 2` with the default pools (2 sync slots) each processed all 300 deliveries (0 `busy`), with 100 Stripe re-reads for 100 processed events. Those runs predate the change of PERF-06's customers to ones without a subscription, so each synchronization then also stored one subscription.
- **Event IDs are new on every run** (`evt_perf_<run>_00000` onwards), so a rerun exercises first deliveries as well as duplicates. The server's report covers every run since it started.

## Reading results

- Each run writes `perf/results/<scenario>-<timestamp>.json`. It contains p50/p90/p95/p99/max latency per class, throughput, error rate, check pass rate, and dropped iterations.
- **Achieved against offered load.** For the arrival-rate scenarios, `stages` lists each stage's offered iterations/s, the iterations started in it, the achieved iterations/s, the requests/s those iterations sent, and their error rate and p50/p95/p99 latency. Report saturation from these per-stage figures, error rate and latency included: a stage that answers quickly with errors still shows full delivery. Iterations are whole, so a stage can start one iteration more or fewer than it offered, which shows only in small stages. The whole-run `http_reqs` rate includes ramps and idle stages, so it is not a sustained rate.
- **Runs that stop early.** A run that stops before its last whole iteration is due (more than 5% of requests failed, a `401`, or Ctrl+C) has `aborted: true`, and `error_threshold_crossed` says whether the error criterion tripped. Each stage's figures cover only the seconds it ran (`covered_seconds`): the stage the run stopped in is `cut_short`, and later stages are `not_reached`. Those are not results; report saturation from `complete` stages, and name the `cut_short` stage as where the run stopped. k6 starts iteration k once the offered load reaches k and ends the run after the last whole one, so a complete run that ends in a ramp down can stop a few seconds before `planned_duration_s` (PERF-04 at an odd `SPIKE_RATE`).
- **Dropped iterations.** k6 drops an iteration when the target is too slow or `MAX_VUS` too low. Baseline, load, and soak fail (k6 exit code 99) when more than 1% of the offered iterations were dropped: the target did not receive the load they describe. Stress and spike only report it. `offered_load_delivered` is `true` only when the run was not cut short (`aborted` is `false`) and dropped at most 1%.
- k6 runs on the same machine as the server. A local result reflects the contention between them and is useful only for relative comparisons.

## Not covered yet

- **Deployment measurements:**
  - cold starts;
  - function duration and memory.

  These need the DEC-001 preview environment and an approved run plan.
