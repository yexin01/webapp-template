# Performance Run Plan and Targets Worksheet

Performance targets are an **adopter decision** (DEC-003, [ADR-0002](../dev-docs/adr/0002-organization-entitlements-contacts-adopter-decisions.md)). The template ships the harness, not targets. Copy this file to `dev-docs/evidence/` for each deployed run, fill it in, and get approval **before** the run.

## 1. Run plan (approve before running)

| Field | Value |
|---|---|
| Environment | Isolated **preview** deployment URL. Production is prohibited without a separate safety plan. |
| Commit SHA | |
| Database | Non-production project with synthetic data only |
| Dataset | `perf/server.py`-equivalent sizes: users, organizations, projects per tenant, Pro share |
| Authentication for load | How the synthetic users' session tokens in `PERF_USERS_FILE` were issued, and their lifetime (see [Credentials](#credentials-for-a-deployed-run); ENV-08: no test-only path in the deployed app) |
| Scenarios | PERF-01 to PERF-07; `SCENARIO=` values and parameters |
| Request mix and think time | Default: every endpoint class once per iteration, no think time |
| Rate cap | Maximum iterations/s, and `MAX_VUS` |
| Duration cap | Per scenario, and in total |
| Abort criteria | Error rate above 5% (built in); provider rate-limit warnings; spend above the cost threshold |
| Cost acknowledgement | Expected platform, database, and provider cost; who accepts it |
| External providers | Stripe and the LLM stay mocked; webhooks use synthetic events signed with a test-only secret (`PERF_WEBHOOK_SECRET`) |
| Request seed | `SEED` value (default `1`) |
| Approved by / date | |

Run against a deployed URL only after the plan is approved:

```bash
export PERF_USERS_FILE=/path/outside/the/repo/perf-users.json
read -rs PERF_WEBHOOK_SECRET && export PERF_WEBHOOK_SECRET   # PERF-06 only; stays out of shell history
BASE_URL=https://<preview-host> PERF_ALLOW_REMOTE=1 SEED=1 SCENARIO=... pnpm perf:run
```

### Credentials for a deployed run

The built-in tokens and webhook secret work only with the local `perf/server.py`. For a deployed target, the run refuses to start until you supply the values below. `pnpm perf:run` never prints them or puts them on a command line. Never commit them.

**`PERF_USERS_FILE`** (every scenario except PERF-06). The file must live outside the repository; `pnpm perf:run` refuses one inside it. It is a JSON array with one entry per synthetic user, in a fixed order:

```json
[
  { "token": "<session token>", "org": "<Clerk organization ID>", "pro": true },
  { "token": "<session token>", "org": "<Clerk organization ID>", "pro": false }
]
```

- `org` is an organization the user belongs to (`org_list`). `pro` says whether the seeded dataset gives the user an active Pro subscription (`entitlement_gated` expects `200` or `403`).
- `USERS` defaults to the number of entries; a smaller value uses the first entries. Keep the order fixed between runs: together with `SEED`, it decides which user sends each request.
- To obtain the tokens:
  1. Create the synthetic users and organizations in the preview's Clerk **development** instance, never in a production instance. Seed the matching rows in the isolated preview database.
  2. Sign each user in on the preview frontend and read its session token, for example with `@clerk/testing` as `apps/web/e2e/authenticated.spec.ts` does, followed by `await window.Clerk.session.getToken()` in the page. Clerk's Backend API can also create a session token for an active session.
  3. Check every token before the run. The API accepts a token only if its issuer is the preview's `CLERK_ISSUER`, its `azp` is listed in `CLERK_AUTHORIZED_PARTIES`, and it has not expired. `GET /api/v1/me` should return `200` for each one.
- **Token lifetime.** Clerk session tokens are short-lived by default (about 60 seconds), and the harness does not refresh them. The run stops at the first `401`. Issue tokens that outlive the duration cap, or split the run into shorter runs, and record the method in the plan.
- Never add a test-only authentication path to the deployed app to get tokens (ENV-08). Delete the file after the run.

**`PERF_WEBHOOK_SECRET`** (PERF-06). It must equal the preview's `STRIPE_WEBHOOK_SECRET`. With Stripe mocked, Stripe does not issue this value:

1. Generate a test-only value for the run, for example `whsec_$(openssl rand -hex 24)`. Never reuse a production secret or a signing secret that Stripe issued.
2. Set it as `STRIPE_WEBHOOK_SECRET` on the preview environment only, and redeploy the preview.
3. Export it as shown above, then remove or rotate it after the run.

The events name the customers that `perf/server.py` seeds: `cus_perf0000`, `cus_perf0002`, and so on (even user indexes). Seed those billing customers in the preview database, or the events do no synchronization work. Event IDs are fixed (`evt_perf_00000` onwards), so start each PERF-06 run with an empty `app.billing_events` ledger.

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
