# ADR-0003 — Modular Monolith, Request-Scoped Transactions, Outbox, Jobs, and Generator

- **Status:** Accepted
- **Date:** 2026-10-14
- **Decided by:** Repository owner. The owner selected these options; their technical design is recorded below.
- **Relates to:** ARCH-01 to ARCH-12, DEC-007, DEC-008, DEC-011 (no new infrastructure), D-001
- **Amended by:** [ADR-0004](0004-cycle-1-adopted-decisions.md) §1: the cron schedule lives in the repository-root `vercel.json` (decision 4 names `apps/api/vercel.json`, which no longer exists); §3: the API's database login is `app_login`

## Context

The API was layered horizontally (`domain`, `application`, `ports`, `infrastructure`, `http`) with one repository interface for every concern. Adding a feature touched about eight shared files, and nothing kept features apart. Local PERF-07 measurements showed that database round trips dominate latency once the database is remote. Scheduled work (billing reconciliation, pruning) existed only as CLI commands, and side effects such as analytics ran inline in use cases.

The owner asked for a template that is modular, scalable, and technically rigorous, and chose these changes:

- backend: modular monolith, request-scoped transactions, domain events with an outbox, scheduled jobs;
- frontend: feature folders, typed API client;
- developer experience: module generator, architecture documentation.

TanStack Query was offered and not selected.

## Decisions

### 1. Modular monolith

- **`app/kernel`** is the shared kernel. It holds:
  - tenancy types and a generic `Permission` model;
  - errors, pagination, domain events, the job registry, and the diagnostics probe contract;
  - the database layer (roles, pools, transactions, outbox store), the HTTP platform (app factory, error contract, middleware, health and jobs routes), and observability.

  It never imports a module.
- **`app/modules/<name>`** has the same layers in every module: `domain`, `ports`, `service`, `adapters`, `probe`, `routes`, `api`, `settings`, and `module` (wiring).
  - Modules depend on the kernel.
  - A module uses another module only through that module's `api.py`. The usual mechanism is a kernel contract passed in at composition: `AuthDeps` for authentication and context, `IntegrationProbe` for diagnostics, and events.
- **`app/settings.py`** composes the module settings. **`app/bootstrap.py`** is the composition root.
- **Enforcement:**
  - import-linter contracts for a framework-free kernel core, the layer stack (`exhaustive = true`), pure domains, framework-free services and ports, routes without SDKs, and acyclic modules;
  - `tests/architecture` for api-only cross-module imports, module shape, unique permissions, and event-type prefixes.
- **Modules:** identity, billing, projects, llm, diagnostics.
- The public HTTP contract is unchanged.

### 2. Data access: generic transactions, one-statement setup, request scope, two pools

- `Transactions[R]` builds each module's repository per transaction. `Tx` adds `record(event)`, `audit(entry)`, and `lock_my_role(org_id)` on the same connection.
- **Setup.** Each transaction sets the role, the statement timeout, and the **full** tenant context in one `SELECT set_config(...)` statement. Every context key is set, empty when unused, so no value leaks from an earlier block.
- **Request-scoped transactions.** They are opt-in per router, for database-only routes: one `app_api` transaction per request.
  - It opens lazily, and the context is re-applied only when it changes.
  - A block that raises rolls back.
  - It commits before the response is sent, using FastAPI `Depends(..., scope="function")`.
  - `release()` commits early, before external calls; the identity resolver releases before contacting Clerk.
  - Routes with external calls (checkout, portal, LLM, diagnostics, webhooks) keep short explicit transactions.
- **Two connection pools per process**, `app_api` and `app_worker`. A request that holds an API connection and needs worker work (rate limits, membership refresh) never waits on its own pool.
- **Thread starvation guard,** added after an independent review and confirmed by measurement. A scoped request holds its connection across several threadpool hops, and the commit itself needed a thread from the shared pool. Under load, threads waiting for connections could starve the holders: locally, a 5-connection pool under stress gave a 55 s worst case and 3% failures. The fixes:
  - an asynchronous admission gate of `DATABASE_POOL_MAX_SIZE` scoped requests;
  - commits on a one-off thread limiter, and no thread hop when nothing was opened.

  After the fixes: no failures, a 1.6 s worst case, and 2.7 times the throughput. Nested blocks inside a request transaction are now refused.

### 3. Domain events with a transactional outbox (no new infrastructure)

- Migration `20261014000002_outbox.sql` creates `app.outbox` with forced RLS. `app_api` may only append, and only with a user context; `app_worker` dispatches.
- Recording happens in the same transaction as the change.
- Dispatch is at least once:
  - after the response, for the request's own events, through a request-scope dependency;
  - from the `drain-outbox` job, for leftovers.
- Leases use `FOR UPDATE SKIP LOCKED` and expire. Completion and failure are fenced to the current lease, by status and attempt. Retries use exponential backoff, and events are marked `dead` after `OUTBOX_MAX_ATTEMPTS`.
- Processed events are pruned after `OUTBOX_RETENTION_DAYS`.
- The application clock drives every outbox timestamp, which keeps behavior deterministic in tests.
- **Initial events:**
  - `projects.created`: product analytics moved out of the request path;
  - `billing.entitlement_changed`: emitted on a plan change during Stripe synchronization, and drives a new allowlisted `plan_changed` analytics event.

### 4. Scheduled jobs

- `JobRegistry` holds four jobs: `drain-outbox`, `prune-expired` (rate-limit windows and processed outbox events), `reconcile-billing` (reports only, unless `BILLING_RECONCILE_APPLY=true`), and `reconcile-memberships`.
- **Triggers:**
  - the CLI (`app.cli job [name]`);
  - `GET /api/v1/jobs/<name>` with `Authorization: Bearer <CRON_SECRET>`, compared in constant time. The endpoint returns 404 when `CRON_SECRET` is unset, and is excluded from OpenAPI.
- `apps/api/vercel.json` schedules the jobs daily, so it deploys on any plan. A test keeps it identical to the registry.

### 5. Frontend

- **Feature folders.** `src/app` holds routes, `src/features/<feature>` has a public `index.ts`, `src/ui` holds shared presentation, and `src/lib` holds infrastructure.
- **Boundary rules.** ESLint `import/no-restricted-paths` (`apps/web/eslint.boundaries.mjs`) enforces them: `lib` may not import `ui`, `features`, or `app`; `ui` may not import `features` or `app`; a feature reaches another only through its `index.ts`. `src/boundaries.test.ts` (23 tests) lints real snippets through the real configuration. Applied on 2026-10-14 with the owner's approval; it had first been prepared as a patch because a configuration-protection hook blocks agent edits to `eslint.config.mjs`.
- **Typed API client.** `openapi-fetch` 0.17.0 (MIT) over the generated `paths` type. Paths, parameters, and bodies are compile-time checked. It adds about 6.7 KiB of uncompressed client JavaScript per route.

### 6. Developer experience

- `pnpm new:module <name>` scaffolds a tenant-scoped module:
  - all API layers;
  - a migration with forced RLS;
  - integration tests;
  - a web feature with a component test;
  - typed client methods;
  - the wiring in `bootstrap.py`, through explicit markers.
- `pnpm test:new-module` (in CI) generates a module in a copy of the repository. It runs lint, strict types, the import contracts, the architecture tests, the generated API tests (it fails if they are skipped), the contract export, web types and lint, and the component test.
- `docs/architecture.md` documents the architecture: C4 context, containers and components, module anatomy, request lifecycle, data access, events, jobs, scaling, and fitness functions.

## Consequences

- **Positive:**
  - Features are isolated and generated consistently.
  - Boundaries fail CI when they are crossed.
  - Fewer round trips per request.
  - Durable, decoupled side effects.
  - Maintenance jobs are scheduled.
- **Costs:**
  - more files per feature;
  - router factories (closures) instead of module-level routes;
  - outbox handlers must be idempotent;
  - request scope must not wrap external calls, which is the reason for opt-in and `release()`;
  - two pools double the per-instance connection ceiling.
- **Not verified** (needs DEC-001):
  - Vercel Cron delivery and plan limits;
  - whether the platform keeps the function alive for after-response dispatch. If it does not, the `drain-outbox` job delivers the events, later.
- **Open:** applying the frontend ESLint boundary patch (owner).
