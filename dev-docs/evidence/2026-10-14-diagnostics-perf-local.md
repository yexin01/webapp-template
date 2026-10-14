# Evidence: Diagnostics, Query Counts, Template Init, Local Performance, Restore Drill (2026-10-14)

| Field | Value |
|---|---|
| Commits | `57200cd` (query counts), `1b7b4eb` and `0422335` (diagnostics and operator access), `89b5ee3` (init-template), `0aaf248` (perf harness), `39a2b67` (inventory and restore drill), `f0a2ac9` (docs), `085a7d6` (gitleaks) |
| Environment | Apple M5 (10 cores, 16 GB), macOS 26.6.2; Node 25.8.2; Python 3.12.13 (uv 0.11.1); FastAPI 0.143.0, uvicorn 0.54.0 (one process), psycopg 3.3.6; PostgreSQL 17.6 in Docker 29.8.0 (local); k6 1.3.0 in Docker |
| Integration mode | **mocked** for every external provider. No `.env` existed, so no sandbox or live service was contacted. |
| Remote actions | **None.** No push, publication, deployment, or repository-setting change. |

## Results

### Functional and security checks

| Check | Command | Result | Requirements |
|---|---|---|---|
| Lint, format, import contracts (4) | `pnpm lint` | PASS | ARCH-01, ARCH-02, FE-02 |
| Strict type checks | `pnpm typecheck` | PASS (web tsc; mypy, 44 files) | — |
| API tests | `pnpm test:api` | PASS: 210 tests (up from 167) | See below |
| Web unit tests | `pnpm test:web` | PASS: 43 tests (up from 37) | FE-03, FE-10 |
| Contract drift | `pnpm contracts:check` | PASS | DEC-007 |
| Template init | `pnpm test:init-template` | PASS | REPO-07 |
| Production build and bundle scan | `pnpm build && pnpm check:bundle-secrets` | PASS | ENV-07 |
| E2E, account-free | `pnpm test:e2e` | PASS: 20 (10 checks on desktop and mobile); 18 skipped (sandbox only) | FE-05, API-04 |
| Dependency audit | `pnpm security:audit` | PASS: no known vulnerabilities as of this date | SEC-04, SEC-07 |
| Licenses | `pnpm security:licenses` | PASS: 132 npm, 40 PyPI packages; `tzdata` needs manual review, as before | SEC-10 |
| Secret scan, history | gitleaks v8.28.0 `git` mode with `.gitleaks.toml` and `.gitleaksignore` | PASS: 0 findings in 11 commits after one reviewed historical false positive was recorded (see the [gitleaks note](#gitleaks-note)) | SEC-05, REPO-10 |
| Secret scan, tracked files | gitleaks `dir` mode on a copy of `git ls-files` | PASS: 0 findings | SEC-05 |
| Clean-clone onboarding | `git clone` → `pnpm bootstrap` → `pnpm test`, `lint`, `typecheck`, `contracts:check`, `test:init-template` | PASS at `085a7d6`: 66 s, including bootstrap; 210 API and 43 web tests (shares this machine's pnpm store, uv cache, and database container) | REPO-11 |

**New API tests (43):**

- **Diagnostics, integration (21):**
  - Authentication is required.
  - **Non-operators get `404`**, and with no operators configured diagnostics are closed outside `APP_ENV=local`.
  - The report contains no secret values and no connection strings.
  - Mock and disabled integrations are reported as **skipped**, and nothing is contacted.
  - The database check confirms the `app_api` role, row-level security, and the tenant context.
  - Clerk directory success and outage.
  - The Stripe price check passes, flags misconfigured prices (interval, archived, live mode, mixed currency), and reports an unknown price.
  - LLM provider failure.
  - The PostHog event is allowlisted and an export failure is detected.
  - The Sentry check returns `sent` with a reference.
  - Per-user rate limit, `404` when disabled, rejection of an unknown integration, and the production-off default.
- **Diagnostics, unit (7):**
  - unsafe sessions: wrong role, row-level security off, missing tenant context;
  - the operator policy;
  - organization context denied.
- **Adapters against loopback HTTP stubs (10):**
  - Stripe `describe_price`: field mapping; `401`, `403`, and `404` mapped to specific errors without echoing the key.
  - PostHog: a successful export carries only allowlisted properties; a failed export is reported; a non-allowlisted event is never sent.
  - Sentry: none when uninitialized; a synthetic error is scrubbed (no `user`, tagged).
- **Query counts, ARCH-08 (4):** project list (personal and organization), billing status, and organization entitlement. Statement counts are identical when data grows from 1 to 60 projects, 1 to 41 subscriptions, and 1 to 51 memberships.
- **Authorization (1):** the personal-context action set is explicit, not `frozenset(Action)`.

### Mutation checks (tests can fail)

- Removing the mock/disabled skip, the currency check, or the PostHog export check makes a diagnostics test fail.
- Removing the database role check: the integration tests could not detect this, because a correct database always reports `app_api`. A unit test with a stub unit of work was added and now fails under this mutation.
- A simulated N+1 in `list_projects` (one extra statement per row) fails both project-list query-count tests.
- The restore drill reports `FAIL` (`projects` mismatched) when a row is deleted from the restored copy.

### Security review finding (addressed)

An automated review of commit `1b7b4eb` reported broken access control: diagnostics were available to every signed-in user, because the personal-context policy granted every `Action`. It was fixed in `0422335`:

- Diagnostics are operator-only (`DIAGNOSTICS_OPERATOR_IDS`, or any signed-in user only when `APP_ENV=local` with no operators listed).
- Non-operators and organization contexts get `404`.
- The personal action set is explicit and guarded by a test.

### Local performance baseline (PERF-01, PERF-03, PERF-06)

> **Local only.** k6 and the server shared one laptop, and all integrations were mocked. These numbers are for relative comparison and must not be presented as deployment performance (spec §15.2). No targets exist (DEC-003), so nothing here passes or fails a target.

**Workload:**

- The dataset is deterministic: 200 users, 20 organizations, 11,000 projects, and 100 active Pro subscriptions.
- Server: one uvicorn process with a database pool of 10.
- Mix: the 7 endpoint classes in `perf/README.md`, with no think time.
- Commands: `pnpm perf:server` and `SCENARIO=<name> pnpm perf:run`, with the overrides noted below.
- Raw JSON is in `perf/results/` (git-ignored, not committed).

**PERF-01 baseline:** 5 iterations/s (about 40 requests/s) for 60 s, run twice. Each run had 2,408 requests, 0% failed, and 100% of checks passed. Latency is in milliseconds; values are run 1 / run 2.

| Class | p50 | p95 | p99 |
|---|---|---|---|
| health | 1.0 / 1.1 | 1.6 / 1.6 | 2.6 / 2.3 |
| auth_read (`/me`) | 4.3 / 4.1 | 6.0 / 6.6 | 7.8 / 8.7 |
| tenant_list | 6.9 / 6.8 | 8.9 / 12.7 | 17.8 / 19.0 |
| org_list | 10.0 / 9.8 | 12.7 / 17.4 | 21.4 / 21.7 |
| billing_status | 4.9 / 5.1 | 7.6 / 10.4 | 12.9 / 14.2 |
| entitlement_gated | 4.5 / 4.4 | 7.3 / 11.9 | 12.4 / 14.0 |
| auth_write (create, delete) | 5.0 / 5.0 | 8.6 / 12.4 | 14.7 / 17.5 |

**Run-to-run variance.**

- p50 values agreed within 5%.
- p95 and p99 values varied by up to about 60%, from roughly 300 samples per class per run.
- Longer runs are needed before quoting tail latency.

**PERF-03 stress** (`MAX_RATE=100 STEP=20s`): six 20-second steps up to 100 iterations/s (about 800 requests/s offered).

| Run | Throughput | Failed | Dropped iterations | Latency in the final steps |
|---|---|---|---|---|
| 1 | about 212 requests/s | 0% | Not recorded | Not recorded |
| 2 | about 226 requests/s | 0% | 1,741 (the 300-VU cap was reached) | p50 340–560 ms; p95 1.15–1.29 s, depending on class |

The local saturation point is therefore about 210–230 requests/s for this mix on this machine. The bottleneck has **not been profiled**. The likely candidates are:

- the single server process;
- the threadpool for synchronous handlers;
- the 10-connection pool.

The peak observed connection count to the perf database was 11. That total includes all connections, and the pool maximum is 10.

**PERF-06 webhooks:** 100 unique signed events, each delivered 3 times by 20 concurrent VUs (300 deliveries).

- All 300 responses were `200`.
- The ledger showed exactly **100 events, all `processed`, with 300 recorded delivery attempts**: duplicates did not cause reprocessing.
- Latency: p50 31 ms, p95 77 ms, p99 113 ms.

A first attempt used k6's per-VU `__ITER`, which yielded only 16 unique IDs. It was discarded and fixed to use the global iteration counter; only the corrected run is reported.

**Not run:** PERF-02 (load), PERF-04 (spike), PERF-05 (soak), and PERF-07 (degradation). Cold starts and function memory need the DEC-001 preview environment.

### Restore drill (supports DEC-005; does not resolve it)

`pnpm db:restore-drill` ran against the local seeded database:

- dump 228 ms (37,872 bytes);
- restore 213 ms;
- verification of row counts, content checksums, RLS flags, and the migration ledger: **PASS**;
- the scratch database was dropped afterwards.

The negative check (one row deleted after restore) produced `FAIL`. This says nothing about hosted backups, recovery objectives, or point-in-time recovery.

### Data inventory (DEC-004 evidence)

`docs/data-inventory.md` lists each personal-data element in:

- the database;
- external services;
- logs;
- browser storage.

It records current retention behavior and open questions. It is a review artifact, not a policy.

## Gitleaks note

The `git` history scan flags `self._secret, tolerance=_SIGNATURE_TOLERANCE_SECONDS` in commit `cdaaa7d` as `generic-api-key`. It is a variable reference, annotated `gitleaks:allow` in the following commit. Its fingerprint is now listed in `.gitleaksignore` with that justification.

A working-tree `dir` scan of everything on disk, including git-ignored build output, also reports:

- Next.js per-build preview keys in `.next/`;
- vendor strings in source maps;
- the **public** Clerk publishable key inlined in the browser bundle. That key is public by design; this build used a placeholder.

None of these files are tracked.

## Not verified by this record

- Any sandbox or live provider behavior. The `/integrations` page is ready for it, but no keys exist yet (DEC-006).
- Browser rendering, accessibility, and signed-in flows, including the new integrations page e2e. They need a Clerk development instance.
- Remote CI, including the new `test:init-template` step and the gitleaks history scan with `.gitleaksignore`.
- Deployment (DEC-001), performance targets (DEC-003), retention policy (DEC-004), and recovery objectives (DEC-005).
