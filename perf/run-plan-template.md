# Performance Run Plan and Targets Worksheet

Performance targets are an **adopter decision** (DEC-003, [ADR-0002](../dev-docs/adr/0002-organization-entitlements-contacts-adopter-decisions.md)). The template ships the harness, not targets. Copy this file to `dev-docs/evidence/` for each deployed run, fill it in, and get approval **before** the run.

## 1. Run plan (approve before running)

| Field | Value |
|---|---|
| Environment | Isolated **preview** deployment URL. Production is prohibited without a separate safety plan. |
| Commit SHA | |
| Database | Non-production project with synthetic data only |
| Dataset | `perf/server.py`-equivalent sizes: users, organizations, projects per tenant, Pro share |
| Authentication for load | How synthetic users get tokens (must comply with ENV-08: no test-only path in the deployed app) |
| Scenarios | PERF-01 to PERF-07; `SCENARIO=` values and parameters |
| Request mix and think time | Default: every endpoint class once per iteration, no think time |
| Rate cap | Maximum iterations/s, and `MAX_VUS` |
| Duration cap | Per scenario, and in total |
| Abort criteria | Error rate above 5% (built in); provider rate-limit warnings; spend above the cost threshold |
| Cost acknowledgement | Expected platform, database, and provider cost; who accepts it |
| External providers | Stripe and the LLM stay mocked; webhooks use synthetic events signed with a test-only secret |
| Approved by / date | |

Run against a deployed URL with `BASE_URL=https://... PERF_ALLOW_REMOTE=1 SCENARIO=... pnpm perf:run`, only after the plan is approved.

## 2. Results to record (spec §15.2)

For each scenario, record:

- p50, p95, and p99 latency per endpoint class;
- throughput and error rate by class;
- concurrency;
- cold-start count and latency;
- function duration and memory;
- database connections and query time;
- duration, dataset, request mix, commit, deviations, and limitations.

## 3. Targets worksheet

Propose targets from the observed baseline (PERF-01) and saturation point (PERF-03), plus expected usage. A target counts only after the adopter approves it. Never report a target as met before then.

| Endpoint class | Metric | Percentile | Threshold | Environment | Workload | Observed (evidence link) | Approved |
|---|---|---|---|---|---|---|---|
| `health` | Latency | p95 | | Preview | | | |
| `auth_read` | Latency | p95 | | Preview | | | |
| `tenant_list` / `org_list` | Latency | p95 | | Preview | | | |
| `billing_status` | Latency | p95 | | Preview | | | |
| `entitlement_gated` | Latency | p95 | | Preview | | | |
| `auth_write` | Latency | p95 | | Preview | | | |
| `webhook` | Latency; duplicate handling | p99; 0 double-processed | | Preview | | | |
| All | Error rate | — | | Preview | | | |
| Frontend (FE-12) | LCP, INP, CLS, JS bundle size | p75 | | Preview | | | |
