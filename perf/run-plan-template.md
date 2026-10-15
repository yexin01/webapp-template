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
read -rs VERCEL_AUTOMATION_BYPASS_SECRET && export VERCEL_AUTOMATION_BYPASS_SECRET   # protected previews only
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
  3. Check every token before the run. The API accepts a token only if its issuer is the preview's Clerk Frontend API origin (the API derives it from `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`; there is no separate issuer setting), its `azp` is listed in `CLERK_AUTHORIZED_PARTIES`, and it has not expired. `GET /api/v1/me` should return `200` for each one.
- **Token lifetime.** Clerk session tokens are short-lived by default (about 60 seconds), and the harness does not refresh them. The run stops at the first `401`. Issue tokens that outlive the duration cap, or split the run into shorter runs, and record the method in the plan.
- Never add a test-only authentication path to the deployed app to get tokens (ENV-08). Delete the file after the run.

**`PERF_WEBHOOK_SECRET`** (PERF-06). It must equal the preview's `STRIPE_WEBHOOK_SECRET`. With Stripe mocked, Stripe does not issue this value:

1. Generate a test-only value for the run, for example `whsec_$(openssl rand -hex 24)`. Never reuse a production secret or a signing secret that Stripe issued.
2. Set it as `STRIPE_WEBHOOK_SECRET` on the preview environment only, and redeploy the preview.
3. Export it as shown above, then remove or rotate it after the run.

The events name the customers of the odd-numbered users in `perf/server.py`'s dataset: `cus_perf0001`, `cus_perf0003`, and so on. Seed those billing customers in the preview database, each linked to a Free user with no subscriptions. The preview's mocked Stripe starts empty, so an event for an unknown customer, or for a customer with a stored subscription that is not final, comes back `needs_reconciliation`, and the run fails. Event IDs carry a per-run identifier (`evt_perf_<run>_00000` onwards), so runs never collide in the `app.billing_events` ledger.

PERF-06 against a deployed target measures latency and acknowledgements only. It **cannot show double processing**: the local server proves exactly-once by counting the mocked Stripe API's re-reads, and a preview offers no such count. Use the local result for that claim.

**`VERCEL_AUTOMATION_BYPASS_SECRET`** (previews behind Vercel Deployment Protection). Deployment Protection answers `401` before the API sees a request, and k6 then stops with a "Deployment Protection" message. With this variable set, every request carries the `x-vercel-protection-bypass` header.

1. In the Vercel project's Deployment Protection settings, create a **Protection Bypass for Automation** secret, with the owner's approval. On Vercel, deployments receive it as `VERCEL_AUTOMATION_BYPASS_SECRET`.
2. Export it as shown above. `pnpm perf:run` passes it to Docker by name only. It is never printed or written to the results, which record only `protection_bypass: true`.
3. Revoke or regenerate it after the run if it was created only for the run.

## 2. Results to record (spec §15.2)

For each scenario, record:

- p50, p95, and p99 latency per endpoint class;
- throughput and error rate by class;
- achieved against offered rate per stage (`stages` in the results file), with each stage's error rate and latency, and whether the offered load was delivered (`offered_load_delivered`). Never report a whole-run average as a sustained rate;
- for a run that stopped early (`aborted`), where it stopped (the `cut_short` stage) and why (`error_threshold_crossed`, or the `401` or interruption in k6's output). Only `complete` stages are results;
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
