# Evidence: Full Review and Fixes (2026-10-15)

| Field | Value |
|---|---|
| Scope | Every commit up to `158194b`, including the DEC-001 proof work, plus the uncommitted work of the other session (committed by that session as `be844fa`, `966d3b3`, `158194b`) |
| Method | Five independent adversarial reviews (database and transactions; authentication, HTTP, and configuration; billing and LLM; web, scripts, and CI; DEC-001 proof and performance harness). Each finding was reproduced or checked against the code before it was fixed. Most fixes have a regression test that was **mutation-checked**: reverting the fix makes the test fail |
| Environment | Apple M5, macOS; Python 3.12 (uv), psycopg 3.3.6, PyJWT 2.15.1; Node 25, Next.js 16; PostgreSQL 17.6 in Docker (local) |
| Integration mode | **mocked** for every external provider. No `.env` exists |
| Remote actions | **None.** No push, deployment, publication, or remote setting change |

## Gates (after the last code change)

| Check | Command | Result |
|---|---|---|
| Lint, format, import contracts | `pnpm lint` | PASS (8 import contracts kept) |
| Strict types | `pnpm typecheck` | PASS (mypy strict, 84 files; tsc) |
| API and web tests | `pnpm test` | PASS: **344** API tests (was 286); **146** web tests (was 74), 1 skipped (the lint-guard wiring test, see below) |
| E2E, account-free | `pnpm test:e2e` | PASS: 20; 18 skipped (sandbox only) |
| Contract drift | `pnpm contracts:check` | PASS |
| Template init, placeholders | `pnpm test:init-template`, `pnpm check:placeholders` | PASS |
| Module generator | `pnpm test:new-module` | PASS, including 24 refusal cases |
| Build and bundle scan | `pnpm build && pnpm check:bundle-secrets`, plus `--self-test` | PASS. The build ran with a placeholder `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`, which the web configuration now requires |
| Dependency audit | `pnpm security:audit` | PASS: no known vulnerabilities |
| Licenses | `pnpm security:licenses`, plus `--self-test` | PASS: 134 npm, 40 PyPI, with reviewed exceptions (owner review pending) |

## Defects found and fixed

### Database, transactions, outbox, jobs

1. **A request could report success for work that was rolled back.** After a block raised inside a request transaction, later blocks silently opened a new transaction. A service that caught the error kept going, and earlier writes were lost behind a 2xx. Now later blocks are refused, and a handler that still returns fails with 500. (`db.py`; `test_request_scope.py`)
2. **A caught SQL error turned COMMIT into a silent ROLLBACK.** PostgreSQL reports no error in that case. Every transaction, scoped or not, now refuses to commit after a failed statement; savepoints still work. (mutation-checked)
3. **`release()` inside an open block** returned the connection to the pool while the block kept using it, without the role or tenant context. Now refused.
4. **A request waiting on Clerk held an admission place.** With slow Clerk calls, personal routes queued behind them (against §16.4). `release()` now ends the scope and gives the place back. (mutation-checked)
5. **A cancelled request could skip its commit.** The commit is now shielded from cancellation.
6. **An outbox event whose worker kept dying was never dead-lettered** and stayed at the head of the queue. Its batch-mates also lost attempts. Events whose lease expires on the last attempt are now dead-lettered, and events are leased one at a time. Fenced completions are counted as `superseded`. (both mutation-checked)
7. **Jobs were not bounded per run** (ARCH-16):
   - `reconcile-billing` walked every customer;
   - pruning ran one unbounded DELETE;
   - `drain-outbox` handled only one batch a day.

   Now reconciliation checks a rotating batch (`BILLING_RECONCILE_BATCH_SIZE`). Pruning deletes in batches, and draining continues until nothing is due. Both stop within a 60 s budget.
8. **Migrations:**
   - the local-only guard trusted the URL host, but libpq `host`, `hostaddr`, `service`, and `PG*` variables override it, so `reset` could reach a remote database;
   - concurrent runs failed with a raw unique violation;
   - a migration numbered before an applied one was applied silently;
   - the ledger in `public` had RLS off, which exposes it through Supabase's Data API.

   All fixed. `seed` and `reset` also require `APP_ENV` local or test (ENV-05).
9. **Connection details:** a sub-second connect timeout was truncated to 0 (libpq's "no limit"). TCP keepalives are now set.
10. **DB-07 coverage:** wrong-context and missing-context tests added for every insert policy.

### Authentication, HTTP, configuration

1. **Rotated or revoked Clerk keys stayed trusted until restart.** `PyJWKClient(cache_keys=True)` caches keys with no expiry. Only the key set is cached now, for an hour. (mutation-checked)
2. **Unhandled exceptions escaped the request context.** The 500 had no request ID and no security headers, and uvicorn printed the raw exception message unredacted. They are now handled inside `RequestContextMiddleware`. Sentry events are tagged with the request ID. (mutation-checked)
3. **Probing unknown organization IDs** turned every request into a Clerk call. A miss within 30 s of a full read is now answered from the mirror.
4. **An older membership read could resurrect a removed membership.** Reads are stamped before the Clerk call, and refreshes for one user are serialized behind a per-user watermark (`users.memberships_verified_at`). (mutation-checked)
5. **Malformed Svix headers or non-UTF-8 bodies** returned 500 (Sentry-quota abuse). They now return 400. (mutation-checked)
6. **Settings:**
   - `APP_ENV=test` accepted live Clerk keys;
   - `CLERK_JWT_KEY` was not validated (it now also accepts escaped newlines);
   - a chunked body over the limit returned 400 instead of 413 (mutation-checked).

### Billing and LLM

1. **`pnpm billing:reconcile` crashed on any divergence**, after committing with `--apply`.
2. **Reconciliation could overwrite a newer webhook result**, for example keeping Pro after a cancellation. With `--apply`, it now reads Stripe under the per-customer lock and records the plan change.
3. **Webhook bursts could take every worker connection**, so `/ready` and rate-limited routes failed. Synchronization now runs in `DATABASE_WORKER_POOL_MAX_SIZE - 1` slots.
4. **Flagged events were never reconciled** (BILL-19). Reconciliation now links customers through Stripe metadata and resolves their ledger rows. A permanent Stripe rejection is acknowledged and flagged (BILL-11), not retried for days.
5. **Logging:**
   - `trialing` is now logged as an anomaly (§9.5);
   - more than one entitling subscription per user is logged.
6. **New tests** for distinct concurrent events (the per-customer lock had no test that failed without it), adoption, reconciliation races, the CLI, and job rotation.
7. **LLM:**
   - errors use only the LLM-02 codes;
   - retries happen only when the provider provably did not accept the request (LLM-03);
   - there is a per-instance concurrency bound (LLM-05, `LLM_MAX_CONCURRENT_CHECKS`).

### Web, scripts, CI

1. **License gate:** it was a denylist that passed a source-available licence (FSL). It is now an SPDX allowlist with reviewed exceptions, case-sensitive expression parsing, and a self-test.
2. **Web configuration (ENV-03):** `src/lib/env.ts` and `env.server.ts` now validate at startup. `next.config.ts` loads only the variables the web app needs, instead of every API secret.
3. **Pre-commit secret scanning (SEC-05):** `scripts/git-hooks/pre-commit` and `pnpm hooks:install`. The gitleaks path exemptions for web tests were removed.
4. **`init-template`** accepted names that broke `openapi.json` or Python, and silently ignored a second, different name.
5. **`new:module`** generated colliding schema names, Python keywords, `bootstrap.py` shadowing, and PostgreSQL reserved table names. Its check also re-synced the working tree's `.venv`.
6. **Bugs fixed:**
   - checkout polling continued after unmount;
   - projects-panel errors bypassed `presentError`;
   - the API client had no timeout, and a 2xx with a non-JSON body resolved to `null`;
   - PostHog remote-controlled capture was not pinned off;
   - analytics identity followed `isSignedIn`, not `userId`;
   - the bundle scan had parser gaps;
   - `.gitignore` missed `.env 2`-style copies;
   - CI had no format check, and the web job's PostgreSQL had no health check;
   - `db:reset` baked in `--yes`.
7. **Performance harness:**
   - preview runs could not authenticate;
   - PERF-06 never delivered duplicates concurrently;
   - user selection was unseeded.

   The earlier PERF-06 result (`2026-10-14-diagnostics-perf-local.md`) tested duplicates arriving one after another, so it must be re-recorded.

### DEC-001 proof (fixed by the other session)

The review's 17 proof findings were fixed in `966d3b3` and `158194b`. Among them: `run.sh` could `rm -rf` a user-chosen directory, and the harness did not assert most results. See the Review section of [`dec-001-vercel-python.md`](dec-001-vercel-python.md).

## Not fixed: owner decisions and limits

- **Stricter lint guards are written but not enabled.** `apps/web/eslint.guards.mjs` and its tests exist. Enabling them means editing `apps/web/eslint.config.mjs`, which this repository's tooling forbids agents to change. The wiring test is skipped until the owner applies it (spec §23, next step 4).
- **License exceptions** need owner review (§23, next step 5).
- **DEC-013** (recorded): preventing a second subscription while one is unsettled.
- **Webhook synchronization still calls Stripe inside a worker transaction**, as BILL-10 requires. Bounded slots limit the impact. Reading outside the transaction with a freshness guard would need an ADR.
- **COMMIT outcome unknown:** a deadline that passes during COMMIT returns 503 even if the server committed. This is documented in `docs/architecture.md`.
- **Single event loop assumed:** the admission gate uses one `anyio.Semaphore` per process. ASGI servers, including Vercel's runtime, run one event loop per process.
- **Integrations nav link:** still visible to every signed-in user (hiding it needs an API capability flag). Its not-found state no longer names internal configuration.
- **Not verified here:** the pre-commit hook (gitleaks is not installed on this machine), and anything that needs a sandbox, a preview deployment, or remote CI (DEC-001, DEC-006).
