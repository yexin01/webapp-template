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

  Only then set `PERF_ALLOW_REMOTE=1`.
- **Production load testing is prohibited** without explicit authorization and an approved safety plan.
- **Every scenario aborts automatically** when more than 5% of requests fail (`http_req_failed`, evaluated after 10 s).
- **Raw output goes to `perf/results/`**, which is git-ignored. Summaries go to `dev-docs/evidence/`.

## How it works

`perf/server.py` runs the real API (routes, services, repositories, PostgreSQL with row-level security) against a dedicated local database, `app_perf`. The database is dropped, re-created, migrated, and seeded on every start, and every external integration is mocked: Stripe, Clerk directory, LLM, and telemetry.

**Authentication (ENV-08).** The launcher lives outside the application package and is never deployed. It composes the app with a token table that maps `perf-token-perf_user_NNNN` to synthetic users, the same technique the test suite uses. The application itself has no test-only authentication path.

**Dataset.** The dataset is fully deterministic: identifiers derive from indexes, and there is no randomness.

- Defaults: 200 users, 20 organizations, and 50 projects per tenant, which comes to 11,000 projects.
- Every user is a member of one organization.
- Even-numbered users have an active Pro subscription.
- Change the size with `--users`, `--orgs`, and `--projects`.

**Load generator.** k6 1.3.0 runs in Docker (`grafana/k6:1.3.0`), so nothing needs to be installed.

**Request mix.** Each iteration hits every endpoint class once, as a random synthetic user, with no think time:

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
| Load | PERF-02 | `SCENARIO=load RATE=20 pnpm perf:run` | Steps to `RATE` iterations/s over 4.5 min |
| Stress | PERF-03 | `SCENARIO=stress MAX_RATE=100 STEP=1m pnpm perf:run` | Six steps up to `MAX_RATE`; aborts on errors |
| Spike | PERF-04 | `SCENARIO=spike SPIKE_RATE=60 pnpm perf:run` | Idle, then a jump to `SPIKE_RATE` for 30 s |
| Soak | PERF-05 | `SCENARIO=soak RATE=10 DURATION=30m pnpm perf:run` | Constant moderate load |
| Webhooks | PERF-06 | `SCENARIO=webhooks WEBHOOK_EVENTS=100 DUPLICATES=3 pnpm perf:run` | Each signed event delivered `DUPLICATES` times by concurrent VUs |
| Degradation | PERF-07 | Restart the server with a knob below, then `SCENARIO=baseline INCLUDE_LLM=1 LABEL=<knob> pnpm perf:run` | Compare against a control run with no knobs |

**PERF-07 server knobs.** All of them are off by default:

| Knob | Effect |
|---|---|
| `--db-latency-ms 20` | Every API-to-PostgreSQL round trip passes through a loopback proxy that adds 20 ms, like a remote database |
| `--provider-latency-ms 200` | The mocked identity directory, Stripe API, and LLM provider wait 200 ms per call |
| `--telemetry [--traces-sample-rate 0.1]` | The real PostHog and Sentry SDKs run, sending to a loopback sink. Nothing leaves the machine; the sink counts uploads on exit |

Pass knobs after `--`, for example `pnpm perf:server -- --db-latency-ms 20`.

Stop the server with Ctrl+C. It then prints the peak number of database connections and the webhook ledger counts, which verify that each event was processed once.

## Reading results

- Each run writes `perf/results/<scenario>-<timestamp>.json`. It contains p50/p90/p95/p99/max latency per class, throughput, error rate, check pass rate, and dropped iterations.
- k6 runs on the same machine as the server. A local result reflects the contention between them and is useful only for relative comparisons.

## Not covered yet

- **Deployment measurements:**
  - cold starts;
  - function duration and memory.

  These need the DEC-001 preview environment and an approved run plan.
