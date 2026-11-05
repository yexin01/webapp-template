# Evidence: Readiness Audit and the First Remote CI Run (2026-11-02)

| Field | Value |
|---|---|
| Scope | An audit of `6f9f33f` (`main`, equal to `origin/main`): is the template ready to run on localhost and to deploy; which integrations it has and whether they work; whether edge cases are covered; whether the code is modular and scalable. It also reads the **first remote CI run** (2026-11-01, the owner had pushed `main`), which earlier records list as never run. The fixes are on the branch `claude/template-readiness-audit` |
| Method | Local `pnpm verify` of the untouched tree, then four read-only reviewers (billing and Stripe; identity, tenancy, database and migrations; the web app; deployment, operations, telemetry and jobs) that ran no suite and touched no database, then the GitHub Actions results read through the GitHub API (runs, jobs, logs). Each finding was checked against the code before it was acted on. Each new test was checked by **mutation**: the guarded code was broken, the test was seen failing, the code was restored |
| Environment | Linux (Ubuntu 24.04, 4 CPUs, a cloud VM). Node 22.23.4 by default, and Node 24.22.1 (what CI runs, installed from the npm package `node@24.22.1` into a scratch directory) for the web job and for the Sentry test. pnpm 9.15.0, uv 0.11.32, Python 3.12.3. PostgreSQL 17.6 from `pnpm db:up` (the Docker daemon was started by hand in the VM). The Supabase image was `public.ecr.aws/supabase/postgres:17.6.1.106`. **Chromium:** the VM carries Chrome for Testing 153 (revision 1243). Playwright 1.64.0 asks for revision 1248 (Chromium 156), so the pre-installed browser was linked under that name outside the repository. The end-to-end result is `local` with that difference |
| Integration mode | `mocked` (test doubles, loopback stubs) and `local` (commands on this machine against local services). The GitHub Actions results are labeled **remote CI**: they prove what a GitHub runner did with a commit, never a provider's behavior. **Nothing is `sandbox` or `live`.** No Clerk, Stripe, Supabase, Vercel, PostHog, Sentry or LLM account was contacted |
| Remote actions | Read-only GitHub API calls (workflow runs, jobs, logs, pull requests). One push of the branch `claude/template-readiness-audit` to `origin`, which the owner authorized on 2026-11-02 ("you can commit and push them"). No pull request, merge, deployment, release or setting was created or changed |

## Labels

- `mocked`: a test double stands in for the provider. It proves behavior against our model of the provider, never connectivity.
- `local`: a command run on one machine against local services. It is not `sandbox`.
- `remote CI`: a GitHub Actions run, read through the API. It is not provider evidence.
- `NOT RUN`: a test or command exists and was not run. `NOT VERIFIED`: no sufficient evidence exists. The two are never PASS.

## Verdict

| Question | Answer | Evidence |
|---|---|---|
| Ready on localhost? | **Yes, after one human step.** `pnpm bootstrap`, `pnpm db:up`, `pnpm db:migrate`, `pnpm check:setup` need no account. `pnpm dev` stops with one line until the two keys of a Clerk development instance (Organizations enabled, Membership optional) are pasted into `.env`, by design: the web app shows no page without Clerk. A page in a browser with real keys was **not seen** here | `pnpm check:setup` and `pnpm dev` run here with the placeholder keys (`local`); the account-free browser tier passed (56 passed, 14 skipped; `local`) |
| Ready to deploy? | **Not proven.** The packaging builds (`pnpm test:vercel-build`, and the CI job `deploy-config` passed remotely), but nothing was deployed, Services is Beta, and the first remote CI run **failed** (below). DEC-001 stays OPEN | Remote CI; `docs/limitations.md` |
| Integrations | Six: Clerk, Stripe, Supabase/PostgreSQL, PostHog, Sentry, and a provider-agnostic LLM check (plus Vercel as the platform). **All are `mocked` or `local`; none is `sandbox` or `live`.** The billing reviewer read the Stripe adapter against the installed SDK (stripe 16.0.0, API 2026-09-30.endive): every field, method and event name it uses exists, and `_get` works on real SDK objects (the card-only `allowed_payment_method_types` parameter was also checked here). That is a read of the SDK, not a call to Stripe | The billing reviewer; `docs/integrations.md` |
| Edge cases | Broad and enforced: 2335 API tests and 1021 web tests (passing, after the fixes), RLS tests, negative security tests, fault injection for the database, webhook replay and concurrency tests, and mutation-checked guards. The audit found **no critical or high defect**. It found the gaps listed below | The four reviewers; this record |
| Modular and scalable? | **Modular: yes, and enforced.** Eight import contracts, per-layer import allowlists, a route-manifest test, and a generator whose output passes every gate (`pnpm test:new-module` passed in 9 min 53 s). **Scalable: by design, unmeasured on the platform.** Stateless API, bounded pools sized for a transaction pooler, `SKIP LOCKED` outbox, PostgreSQL counters. The limits are the ones in `docs/limitations.md`: no platform measurement, Postgres as queue and rate limiter, daily crons on Hobby | `pnpm verify`; `docs/architecture.md` |

## The first remote CI run

Run `41301628568`, the push of `6f9f33f` to `main`, 2026-11-01. Read through the GitHub API. Earlier records say remote CI "has never run"; it ran, and it failed.

| Job | Result | Where it stopped |
|---|---|---|
| Secret scan | success | The pinned gitleaks flagged a planted secret (self-test) and found none in the history reachable from the commit |
| Deployment configuration (`vercel build`, no deployment) | success | The pinned CLI built the root `vercel.json`, and the 13 output checks passed |
| API: lint, types, tests, audit | **failure** | `actions/setup-node`: `Unable to locate executable file: pnpm`. Nothing after it ran |
| API: Supabase Postgres image | **failure** | The same step and message |
| Web: lint, types, tests, build, e2e | **failure** | `web-tests`: `src/sentry-canary.test.ts` failed (1 of 1022 tests): `expected ... to have a length of 2 but got 3`. The 12 steps after it (`contracts` to `e2e`) did not run |
| CodeQL (its own workflow, run `41301628547`) | success | |

CI also failed on all nine Dependabot pull requests whose results were read (#7 to #15); for #9 (an `upload-artifact` bump) the failing jobs and steps are the same as on `main`, the others were not opened. CodeQL failed on #8 (`codeql-action/init` bumped alone); the ops reviewer read the message in its log: `Loaded a configuration file for version '4.39.1', but running version '4.38.3'`. CodeQL passed on the other pull requests and on `main`.

### Causes

1. **`setup-node` looked for pnpm.** Its package-manager cache is on by default and follows `"packageManager": "pnpm@9.15.0"` in the root `package.json`. The two API jobs never install pnpm (the web and `deploy-config` jobs install it first). The step is not testable on a laptop, so no earlier check could see it.
2. **The Sentry test depended on the machine's release variable.** GitHub sets `GITHUB_SHA`. The Sentry Node SDK takes it as the release, and then sends a release-health `session` envelope as soon as an error is captured: three envelopes, not two. Reproduced on Node 24.22.1 by setting any one of `GITHUB_SHA`, `COMMIT_REF`, `VERCEL_GIT_COMMIT_SHA`, `SENTRY_RELEASE` (4 of 4 failed with the same assertion); unset, 3 of 3 tests pass. Vercel sets `VERCEL_GIT_COMMIT_SHA`, and a local run with it set produced the same third envelope, so the web server on Vercel should send session envelopes too (not observed there). The one captured here holds the release, the environment and `user_agent: Node.js/24`, no personal data.
3. **The Dependabot configuration proposed `init` alone.** The two actions must run the same version.

## Baseline: `pnpm verify` on the untouched tree

`6f9f33f`, working tree clean, Node 22.23.4, 18 min 30 s. `local` and `mocked`.

| Result | Steps |
|---|---|
| PASS (22) | `api-lint`, `api-format`, `api-imports` (8 contracts kept), `api-migrations`, `api-types`, `api-audit`, `web-format`, `web-lint`, `web-types`, `web-tests` (1021 passed, 1 skipped), `contracts`, `scripts-tests` (128), `init-template`, `placeholders`, `links`, `new-module` (9 min 53 s), `build`, `audit-self-test`, `web-audit`, `licenses-self-test`, `licenses`, `e2e` (56 passed, 14 skipped: the sandbox tier) |
| FAIL (1) | `api-tests`: 1 failed, 2311 passed, 9 skipped (6 min 11 s): `test_a_database_that_stops_responding_fails_within_bounds` |
| SKIPPED (1) | `secrets`: gitleaks is not installed here (the remote secret scan passed) |

The one failure is a race in the test, not in the code. It passed 6 of 6 alone. A probe showed why: after the timed-out call the pool discards the connection and opens a replacement in the background, so the proxy's connection count rose from 1 to 2 within a second while the pool's own checkout count rose by exactly 1 (`returns_bad` 1). The assertion read the proxy's count and so won or lost a race.

## Findings by disposition

Counted from the four reviewers' reports and this audit's own checks. No finding is critical or high. "Fixed" means code or tests changed on the branch and the new test was seen failing against the old behavior.

### Fixed in code, tests or configuration

| # | Finding | Change | Check |
|---|---|---|---|
| 1 | Both API CI jobs stop at `setup-node` (remote run) | `package-manager-cache: false` in both jobs of `.github/workflows/ci.yml`; `test_setup_node_does_not_look_for_a_pnpm_the_job_never_installed` (every workflow) and its control `test_the_setup_node_check_flags_the_two_jobs_that_failed_remotely` | The test flags `['api', 'api-supabase']` on the unfixed file and passes on the fixed one. Re-run on GitHub: **NOT RUN** (a push to a branch starts no workflow; a pull request would) |
| 2 | The web CI job fails on a machine that has a release variable | `sentry-canary.test.ts` counts the envelopes that carry an error or a span; session envelopes are still searched for the canary | 3 of 3 pass with each of the four variables set and with none, on Node 24.22.1 and 22.23.4 |
| 3 | `test_a_database_that_stops_responding_fails_within_bounds` races the pool's background reconnect | The assertion counts pool checkouts (`get_stats()["requests_num"]`), which is one per attempt | 5 of 5 runs pass; with the retry rule of `Database.begin` broken (a timed-out connection retried) the test fails with `assert (3 - 1) == 1` |
| 4 | `/api/v1/ready` answered `200 ready`, and the PostgreSQL check passed ("row-level security enforced on every app table"), on a database with **no schema `app`** (found by running the API against an unmigrated local database). Nothing migrates on deploy, so a forgotten migration reads as healthy | `PgKernelStore.ping()` fails when the kernel's tables (`job_runs`, `outbox`, `rate_limit_counters`) are missing and logs `database_not_migrated`; `describe_connection()` returns `missing_tables`; the database probe fails first with code `not_migrated`. Tests: `test_unmigrated_database.py` (5), `test_a_database_nothing_was_migrated_into_fails_whatever_else_it_reports` | With `ping()` back to `SELECT 1`, the readiness test fails (`assert 200 == 503`); with the probe branch off, the unit test fails |
| 5 | A `CRON_SECRET` with a pasted space or newline never matches the header (HTTP trims it), so every cron call answers 401 with no error at startup | The settings validation refuses surrounding whitespace (`CRON_SECRET must not start or end with whitespace`); `test_a_cron_secret_with_surrounding_whitespace_is_refused_at_startup` (4 cases) | With the rule off, 4 of 4 fail |
| 6 | The JSON log line printed a value of a type `redact` does not walk (an exception, a set, bytes) unredacted, through `json.dumps(default=str)` | `default=_printable` redacts the text; `password` and `x-vercel-protection-bypass` join the sensitive query names (the second sits in the webhook URLs of a protected preview) | With `default=str` restored, 3 of 3 type cases fail (the tuple and string controls pass); with the two names removed, 3 of 3 fail |
| 7 | Dependabot proposes `codeql-action/init` and `analyze` separately; a lone bump fails every CodeQL job | `groups: codeql-action` in `.github/dependabot.yml` | YAML parses; takes effect at the next Dependabot run |

### Fixed in the documents

| # | Finding | Change |
|---|---|---|
| 8 | The documented residual risk of `SET ROLE app_worker` said "read across tenants". The worker's policies are `FOR ALL ... USING (true) WITH CHECK (true)` and its grants include INSERT, UPDATE and DELETE (memberships), INSERT and UPDATE (subscriptions, users, organizations) and INSERT (audit log, no UPDATE or DELETE): arbitrary SQL can also **write** across tenants (add itself to an organization as admin, mark a subscription active, append audit entries). Read from `20261014000001_core_schema.sql` | DB-11 in the specification, ADR-0004 §3, `docs/architecture.md`, `docs/integrations.md` and `docs/operations.md` now say read and write. No grant changed |
| 9 | The documents said remote CI had never run | The specification (§18, §21, §23), `docs/limitations.md` and this record |
| 10 | The readiness and `CRON_SECRET` changes | `docs/operations.md`, `docs/deployment.md` |

### Not changed, and why

| Area | Finding (reviewer's confidence) | Why left, and the suggested step |
|---|---|---|
| Billing | `billing.entitlement_changed` and the `plan_changed:free` analytics event are not emitted when Pro ends by time (the portal's end-of-period cancellation, the normal churn path): `_store` evaluates "before" and "after" at the same `now`, so both are Free. Confirmed offline | A billing behavior change with a design choice (evaluate "before" at the previous sync, or persist the last evented plan): the owner decides (§17, rule 5). The only consumer in the template is the PostHog event |
| Billing | `payment_pending` (an `incomplete` subscription) has no exit in the UI: no Upgrade, no Manage billing | Defensive state by design (BILL-07, DEC-013 note); since API version 2025-03-31.basil Stripe creates the subscription after payment, so it should not occur. Needs the sandbox run |
| Billing | `summarize()` and `checkout_block` disagree for an unpaid, paused or trialing subscription past its own end (Upgrade shown beside a payment warning); an `incomplete_expired` history reads "Your Pro plan has ended" | Rare, transient states; fix is `pending` from `live_subscriptions`. Left with the billing item above |
| Billing | Events for Stripe customers this database does not know each cost a Stripe read (a shared Stripe account) | Cost and design; pre-filter on the event's metadata or cache negative lookups |
| Web | `?from=portal` re-read is lost in `next dev` (StrictMode runs the effect twice); sign-in is a same-tab link, so FE-04's "keeps form state" does not hold; keyboard focus is dropped when a focused button becomes disabled; a dead API reads "Something went wrong"; `error.digest` is read from a value typed `unknown`; no `h1` in loading and error states; `upgrade-insecure-requests` on `http://localhost` in Safari (speculative) | Low severity, UX or development-only; two are pinned by existing tests as chosen copy (`errors.test.ts`, `state-views.test.tsx`) |
| Web | A failed entitlement lookup shows the "Free" badge | A documented choice (`workspace.tsx`: "shown as Free; the server still decides access") |
| Identity and database | `app_worker` can read `app.projects` of every tenant although no worker code does (and so does every generated tenant table, through the generator template); a session token's lifetime is not bounded (`exp - iat`); no `idle_in_transaction_session_timeout`; denied project deletes and denied diagnostics runs write audit rows without a daily cap and `audit_log` has no retention; `CLERK_API_URL` accepts `http://`; `redirect_slashes` is on | Each needs a migration, a Clerk sandbox check, or a retention decision (DEC-004 is adopter-owned); a grant change is the owner's. A signed-in user can add up to 20 (diagnostics) or 60 (projects) such rows a minute, bounded by the per-user limits |
| Operations | `drain()` can claim a failed event again inside one run once its backoff elapses, so `OUTBOX_MAX_ATTEMPTS` can be spent in a few daily runs, and each attempt is a `logger.exception` (a Sentry event; the OBS-07 throttle covers only the HTTP error handler). Confirmed by a simulation | Changes the outbox engine; suggested step: claim each event at most once per drain and throttle that log |
| Operations | `db-migrate.yml` dispatched from a non-default branch skips its job silently; `OUTBOX_LEASE_SECONDS` may be set below a function's duration with no startup check; `next dev` binds all interfaces (the API origin check still refuses other origins); `duration_ms` includes after-response work; the LLM timeout (120 s allowed) exceeds the web client's 90 s deadline; the `pnpm/action-setup` pin comment says v4.4.0 | Low; the migration workflow is protected (SEC-12) and the Environment already restricts the branch |

## Verification of the fixes

Run on the finished tree before the commits (the working tree had the fixes uncommitted, so each summary names `6f9f33f` with "uncommitted changes"). `local` and `mocked`.

| Check | Result |
|---|---|
| `pnpm verify` (Node 22.23.4, 18 min 5 s) | **PASS: 23 passed, 1 skipped** (`secrets`: the gitleaks binary is not installed). `api-tests` 2335 passed, 9 skipped; `web-tests` 1021 passed, 1 skipped; `new-module` 10 min 4 s; `e2e` 56 passed, 14 skipped |
| gitleaks, run as the pre-commit hook does, from the pinned container image over the history | 290 commits scanned, no leaks (`local`) |
| `pnpm verify --job api` with `CI=true GITHUB_ACTIONS=true GITHUB_SHA=<the commit>`, on the tree before the log-redaction change | PASS: 7 of 7; `api-tests` 2326 passed, 9 skipped |
| `pnpm verify --job web` on Node 24.22.1 (what CI runs) with the same variables, on the tree before the Sentry filter was narrowed to event, transaction and span items | PASS: 16 of 16 (`new-module` 9 min 40 s) |
| `pnpm verify --job web --skip new-module` on Node 24.22.1 with the same variables, on the finished tree | PASS: 15 passed, 1 skipped by choice (`web-tests` 1021 passed) |
| `pnpm test:supabase` (Supabase image 17.6.1.106) on the finished tree | PASS: 859 passed, 5 skipped (the five need a superuser administrator) |
| The Sentry test on Node 24.22.1 with each of four release variables set, and with none; and on Node 22.23.4 | 3 of 3 pass each time; with the span item dropped from the filter, 2 of 3 fail |

## Not run

| What | Why | What would verify it |
|---|---|---|
| The fixed workflow on GitHub | A push to a branch starts no workflow (CI runs for pull requests and pushes to `main`) | A pull request from the branch, then read the run. The `api-supabase` job and the 12 web steps after `web-tests` have never run on a runner |
| Every sandbox suite, the Vercel preview, hosted Supabase, the live providers, real PostHog and Sentry delivery | No accounts or keys | The steps in `docs/setup.md` and `docs/deployment.md` |
| gitleaks in `pnpm verify` | Not installed here | Install it; the remote secret scan passed on `6f9f33f` |
| `proofs/dec-001/run.sh` | Needs about ten minutes and Docker Desktop | Run it once on a quiet machine |
| A page of the web app in a browser with real Clerk keys | No Clerk instance | The sandbox run |
| The strict web lint guards | Wiring them means editing `eslint.config.mjs`, which agents may not change (§23, step 4) | The owner applies it |
