# Evidence: Modular Architecture, Request-Scoped Transactions, Outbox, Jobs, Generator, Frontend Features (2026-10-14)

| Field | Value |
|---|---|
| Requirements and decisions | ARCH-01, ARCH-02, ARCH-08, ARCH-13 to ARCH-17, FE-13, FE-14; ADR-0003; deviation D-002 |
| Commits | `f0449c8` (API modular refactor), `d927fb6` and `16fc39b` (web feature folders and typed client), `5bd866e` (generator, outbox retention), plus the documentation commit that adds this record |
| Environment | Same machine as the earlier 2026-10-14 records: Apple M5, macOS; Python 3.12 (uv), FastAPI 0.143, psycopg 3.3, import-linter 2.15; Node 25, Next.js 16.4, openapi-fetch 0.17.0; PostgreSQL 17 in Docker (local); k6 1.3.0 in Docker |
| Integration mode | **mocked** for every external provider. No `.env` exists. |
| Remote actions | **None.** No push, publication, deployment, or repository-setting change. |

## Functional, security, and architecture checks

All checks ran after the final code change.

| Check | Command | Result |
|---|---|---|
| Lint, format, import contracts | `pnpm lint` | PASS. **8 import-linter contracts** (was 4): framework-free kernel core, kernel independent of modules, layered modules (`exhaustive`), pure domains, framework-free services and ports, routes without SDKs, acyclic modules |
| Strict type checks | `pnpm typecheck` | PASS (mypy strict, 84 files; tsc) |
| API and web tests | `pnpm test` | PASS. **274** API tests (was 226) and **51** web tests (was 43) |
| E2E, account-free | `pnpm test:e2e` | PASS: 20; 18 skipped (sandbox only) |
| Contract drift | `pnpm contracts:check` | PASS. The refactor kept every path, schema, operation ID, tag, and response identical. Only the `IntegrationKey` description changed |
| Template init, placeholders | `pnpm test:init-template`, `pnpm check:placeholders` | PASS |
| **Module generator** | `pnpm test:new-module` | PASS. It generates `time_entries` in a copy of the repository, then runs ruff, mypy, import contracts, architecture tests, the generated API tests (asserted to run, not skip; 17 passed), OpenAPI export, web types, lint, and the component test (3 passed). It also rejects bad and reserved names, refuses to generate a module twice, and a dry run writes nothing |
| Build and bundle scan | `pnpm build && pnpm check:bundle-secrets` | PASS. `CRON_SECRET` added to the server-only list. Client JavaScript: 22 files, 1.19 MB raw, 362 KB gzip (was 1.18 MB / 360 KB) |
| Dependency audit | `pnpm security:audit` | PASS: no known vulnerabilities as of this date |
| Licenses | `pnpm security:licenses` | PASS: 134 npm (adds `openapi-fetch`, MIT), 40 PyPI; `tzdata` needs manual review, as before |

**New API tests (48):**

- **Request scope (16):**
  - one transaction for each database-only route;
  - a write commits;
  - **a failed commit returns 503 before any response is sent**, and the row is absent;
  - a raising block rolls back pending work;
  - worker blocks never join and commit independently;
  - the context is fully re-applied, with no leak between blocks;
  - `release()` commits early and reopens;
  - the resolver releases before calling Clerk;
  - nested blocks are refused;
  - there is no commit and no thread hop when nothing was opened;
  - admission is bounded by the API pool.
- **Outbox and jobs (22):**
  - an event is recorded with its change and dispatched after the response;
  - no event without the change;
  - the request role can only append, and needs a user context;
  - retries with backoff, then dead-lettering;
  - an expired lease is reclaimed;
  - `billing.entitlement_changed` fires only on a real change;
  - the job endpoint is off without a secret, rejects wrong, missing, Basic, or unprefixed secrets, and returns 404 for unknown jobs;
  - jobs are excluded from OpenAPI;
  - the drain, prune-retention, membership-reconcile, and billing-reconcile jobs (report-only by default) work;
  - `vercel.json` matches the registry;
  - the CLI lists the jobs;
  - completion is fenced to the current lease.
- **Architecture (9):**
  - cross-module imports only through `api.py`;
  - every module has the required shape and a `build_*` wiring function;
  - permission names are unique;
  - event types are unique and prefixed by their module.
- **One-statement setup test (1):** the existing test, adapted to two pools.

**Web (8 new):** typed-client tests, including compile-time `@ts-expect-error` checks on a misspelled path and a wrong parameter.

### Mutation checks (the guards can fail)

| Guard | Planted violation | Result |
|---|---|---|
| Import contracts | `import fastapi` in `projects/domain.py`; `projects/ports.py` importing `projects/service` | 4 of 8 contracts BROKEN; restored, 8 kept |
| Architecture test | `projects/service.py` importing `billing.service` | FAIL, naming the import; restored, PASS |
| Generator check | Wrong return type in the service template | FAIL with the mypy errors; restored, PASS |

### Defects found and fixed during this work

| Defect | How it surfaced | Fix |
|---|---|---|
| `REPO_ROOT` one directory too shallow after the move, so migrations were not found | Tests: `schema "app" does not exist` | Correct depth, plus a startup guard that fails loudly when the layout changes |
| Request-scope context variable reset in a worker thread | Tests: `Token was created in a different Context` | Reset in the event loop; commit alone runs in the threadpool |
| FastAPI resolved function-local dependency aliases as query parameters under postponed annotations | Tests: 422 `query.ctx missing` | No `from __future__ import annotations` in router-factory files (documented in each) |
| Outbox availability used the database clock, dispatch used the application clock | Tests: events stayed `pending` | One clock (the application's) for every outbox timestamp |
| `INSERT ... RETURNING` needs a SELECT policy that the append-only API role must not have | Design review | Event IDs generated in the application |

## Performance (local, relative comparison only; not targets)

PERF-07 shape: 5 iterations/s for 60 s (about 45 req/s) with the mocked LLM check, on the same machine as the API and PostgreSQL. The "after setup fix" columns are from [2026-10-14-adr-0002-perf07-local.md](2026-10-14-adr-0002-perf07-local.md).

**p50 latency in ms:**

| Class | Control, after setup fix | **Control, modular** | DB +20 ms, after setup fix | **DB +20 ms, modular (request scope)** |
|---|---|---|---|---|
| `auth_read` | 3.1 | 3.2 | 91 | 90 |
| `tenant_list` | 4.9 | 3.9 | 180 | **135** |
| `org_list` | 6.5 | 4.8 | 270 | **180** |
| `billing_status` | 3.9 | 2.8 | 180 | **135** |
| `entitlement_gated` | 3.8 | 2.7 | 179 | **134** |
| `auth_write` (create also writes an outbox event) | 4.5 | 3.7 | 179 | **152** |
| `llm_check` (not request-scoped by design) | 6.1 | 4.5 | 270 | 269 |
| Throughput / errors / dropped | 45.0 / 0% / 0 | 45.1 / 0% / 0 | 43.9 / 0% / 1 | 44.2 / 0% / 0 |

Observations:

- At 20 ms RTT, request scope cuts p50 by 25% (personal list) and 33% (organization list). This matches the round-trip arithmetic: a personal list goes from 8 to 6 round trips, and an organization list from 12 to 8.
- Write latency improved despite the extra outbox insert. Analytics now runs after the response, not on the request path.
- Peak database connections stayed within the pools: 3 in control, 10 at 20 ms RTT.

## Independent review and stress test (after the commits above)

An independent read-only review of the transaction, outbox, and jobs code found one load-dependent defect and two latent hazards. All three are fixed, with tests.

| Finding | Severity | Fix | Evidence |
|---|---|---|---|
| Request-scoped requests hold a pooled connection across threadpool hops, and the commit needed a shared thread. Under load, threads waiting for connections starve the holders | Important | Async admission gate (`DATABASE_POOL_MAX_SIZE` scoped requests); commit on a one-off thread limiter; no hop when nothing was opened | PERF-03 shape, local, pool 5, `MAX_RATE=100`, `STEP=20s` (below) |
| A nested block inside a request transaction would switch context under the outer block, and its rollback would release a connection still in use | Latent (no caller) | Nested blocks raise `RuntimeError` | `test_nested_blocks_are_refused_inside_a_request_transaction` |
| Outbox completion was not fenced to the lease, so a stale worker could overwrite a newer outcome | Low (within at-least-once) | `complete_outbox` and `fail_outbox` require `status='processing' AND attempts=<lease>` | `test_completion_is_fenced_to_the_current_lease` |

The review also confirmed that the following hold:

- tenant context never leaks (transaction-local GUCs, fully re-applied);
- worker blocks never join a request transaction;
- the job secret check is constant-time and comes before the job lookup;
- the outbox RLS is append-only for `app_api`;
- all SQL uses bound parameters.

**Stress test before and after the starvation fix** (local, same machine, perf server with `--pool-size 5`):

| Metric | Before | After |
|---|---|---|
| Requests completed | 15,146 | 34,240 |
| Throughput | 100.9 req/s | 277.2 req/s |
| Failed requests | 3.08% | **0.00%** |
| Dropped iterations | 3,157 | 969 |
| `health` p95 / max (no database) | 16,924 ms / 35,102 ms | 14 ms / 190 ms |
| `tenant_list` p95 / max | 35,051 ms / 50,116 ms | 993 ms / 1,553 ms |
| Worst case across classes | 55,298 ms | 1,583 ms |
| Peak database connections | — | 8 (5 request + 3 worker pool) |

Before the fix, even the database-free health check stalled, because the whole threadpool was blocked. After it, saturation shows up as bounded queueing at the admission gate instead of collapse.

## Not verified by this record

- **Vercel (DEC-001):**
  - Vercel Cron delivery and plan limits;
  - whether after-response dispatch completes before the function freezes (fallback: `drain-outbox`);
  - cold starts.
- **Frontend boundary lint rules:** prepared and validated (23 tests) but **not applied**. A configuration-protection hook in the owner's environment blocks edits to `eslint.config.mjs`. See `dev-docs/pending/`.
- Real provider behavior: every integration is still **mocked** (DEC-006).
- Remote CI, including the new `test:new-module` step.
