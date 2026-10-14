# SaaS Template

An open-source SaaS starter: a **Next.js** (TypeScript, strict) web app and a **Python (FastAPI)** API, with **Clerk** authentication and organizations, **Stripe** Free/Pro billing (monthly and yearly), **Supabase/PostgreSQL** with row-level security, **PostHog** analytics, **Sentry** error monitoring, and a provider-agnostic **LLM** connectivity check.

It is secure by default: every API operation is authorized on the server, tenants are isolated by the database as well as the application, and Pro access is granted only from verified Stripe data.

**Billing model.** Subscriptions belong to individuals. A user's Pro plan covers their own actions in their personal workspace and in every organization they belong to. It is never shared with other members, and organization roles neither grant nor remove it ([ADR-0002](dev-docs/adr/0002-organization-entitlements-contacts-adopter-decisions.md)).

A minimal UI exercises every integration:

- sign-in and organizations;
- a per-tenant project list;
- Free/Pro billing and a Pro-only page;
- an **Integrations** page (`/integrations`) that shows each service's mode and runs a live check against it.

> **Status:** pre-release. Local development and the automated test suites work. Deployment to Vercel has **not** been verified yet (DEC-001): a local proof packages and runs the API with Vercel's tooling, but nothing has been deployed. External integrations have been exercised only with mocks. See [Verification status](#verification-status).

## Architecture

```
Browser ──► Next.js (apps/web) ──/api/v1/* rewrite──► FastAPI (apps/api) ──► PostgreSQL (schema "app", RLS)
              │ Clerk UI                                  │ verifies Clerk session tokens
              │ PostHog / Sentry (browser)                ├─► Clerk Backend API (memberships)
                                                          ├─► Stripe (checkout, portal, webhooks)
                                                          ├─► LLM provider (connectivity check)
                                                          └─► PostHog / Sentry (server)
```

- **Web app:** presentation only, organized by feature (`src/features/*`). It never talks to the database or to Stripe with secret keys, and it calls the API through a client generated from the OpenAPI contract.
- **API:** a modular monolith. A shared kernel (`app/kernel`) provides tenancy, permissions, transactions, the outbox, jobs, and the HTTP platform. Feature modules (`app/modules/{identity,billing,projects,llm,diagnostics}`) all share the same layers: domain, ports, service, adapters, routes, and wiring. Boundaries are enforced in CI by import contracts and architecture tests.
- **Data:** every transaction runs as a non-privileged role (`app_api` or `app_worker`) with forced row-level security. Database-only requests run in one request-scoped transaction. Side effects go through a transactional outbox, and maintenance runs as scheduled jobs.
- **Details:** [docs/architecture.md](docs/architecture.md), with C4 diagrams, module anatomy, request lifecycle, data access, events, jobs, scaling, and fitness functions.

**Add a feature:** `pnpm new:module invoices` generates a tenant-scoped module end to end: API layers, a migration with RLS, tests, a web feature, typed client methods, and wiring.

The authoritative engineering specification is [`dev-docs/engineering-implementation-plan.md`](dev-docs/engineering-implementation-plan.md). Decisions are recorded in [`dev-docs/adr/`](dev-docs/adr/).

## Prerequisites

| Tool | Version |
|---|---|
| Node.js | 22.12+ (24 LTS recommended; see `.nvmrc`) |
| pnpm | 9.15 (`corepack enable`) |
| Python | 3.12 (installed automatically by uv) |
| [uv](https://docs.astral.sh/uv/) | 0.11+ |
| Docker | Recent, for the local PostgreSQL container |

You also need your **own** accounts for the services you enable. The automated tests need none of them.

## Quick start

```bash
pnpm bootstrap             # install JS and Python dependencies from lockfiles
pnpm init-template --name "Your Product" --slug your-product \
  --security-contact security@your-product.example \
  --conduct-contact conduct@your-product.example   # rename, fill in contacts, create .env
                           # add --db-port 54330 to run several projects side by side
cp -n .env.example .env    # if you are only trying the template out
pnpm db:up                 # start local PostgreSQL (127.0.0.1:54329)
pnpm db:migrate            # apply migrations
pnpm hooks:install         # optional: scan staged changes for secrets before each commit
```

`pnpm hooks:install` points git at `scripts/git-hooks`, whose pre-commit hook runs [gitleaks](https://github.com/gitleaks/gitleaks) (install it first, for example `brew install gitleaks`) on the staged changes with the same rules as CI (`.gitleaks.toml`). It refuses the commit when it finds a possible secret. Undo with `git config --unset core.hooksPath`.

Edit `.env`. At minimum, set your Clerk **development** instance keys (`NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`, `CLERK_SECRET_KEY`); everything else can stay in `mock` or `disabled` mode. [docs/integrations.md](docs/integrations.md) explains each service, its callback URLs, and how to switch it to `sandbox`.

```bash
pnpm dev                   # API on :8000, web on :3000
```

Open http://localhost:3000. The API refuses to start if configuration is missing or inconsistent, and the error names the variables involved without printing their values.

Then open **http://localhost:3000/integrations** and select **Run all server checks**. Each service reports one of:

- **Passed**: the provider confirmed the request.
- **Sent (unconfirmed)**: handed to the SDK, but the provider does not confirm delivery.
- **Failed**: with a reason.
- **Skipped**: mock or disabled; nothing was contacted.

Diagnostics are open to any signed-in user only when `APP_ENV=local` and `DIAGNOSTICS_OPERATOR_IDS` is empty. Everywhere else, list operator Clerk user IDs in `DIAGNOSTICS_OPERATOR_IDS`. In production they are off unless `DIAGNOSTICS_ENABLED=true`.

## Commands

| Command | Purpose |
|---|---|
| `pnpm dev` | Run the API and web app with reload |
| `pnpm test` | API tests (needs `pnpm db:up`) and web unit tests |
| `pnpm test:e2e` | Playwright tests. Account-free checks run by default; set `E2E_SANDBOX=1` for browser and signed-in flows |
| `pnpm lint` / `pnpm typecheck` | ESLint + Ruff + import contracts / tsc + mypy (strict) |
| `pnpm build` | Production build of the web app |
| `pnpm contracts` | Regenerate the OpenAPI document and TypeScript API types |
| `pnpm contracts:check` | Fail if generated contracts are stale |
| `pnpm check:bundle-secrets` | Fail if server-only values appear in the browser bundle (after `pnpm build`) |
| `pnpm security:audit` | Dependency vulnerability audit (pnpm + pip-audit) |
| `pnpm db:migrate` | Apply pending migrations (local databases only unless `--allow-remote`) |
| `pnpm db:seed` | Load deterministic synthetic data (local only) |
| `pnpm db:reset --yes` | Drop, migrate, and seed the **local** database. Refuses to run without `--yes` (pass it after the script name, not after `--`) |
| `pnpm db:restore-drill` | Dump the local database, restore it into a scratch database, and verify it |
| `pnpm test:init-template` | Check that `init-template` leaves no original identifiers (runs on temporary copies) |
| `pnpm new:module <name>` | Scaffold a tenant-scoped feature module end to end ([docs/architecture.md](docs/architecture.md#adding-a-feature)) |
| `pnpm test:new-module` | Check that a freshly generated module passes every gate (runs on a temporary copy) |
| `pnpm check:placeholders` | Fail if `SECURITY.md` or `CODE_OF_CONDUCT.md` still has a contact placeholder after the project was renamed |
| `pnpm perf:server` / `pnpm perf:run` | Local performance server and k6 scenarios ([perf/README.md](perf/README.md)) |
| `pnpm billing:reconcile` | Compare billing records with Stripe (dry run; add `--apply`) |
| `cd apps/api && uv run python -m app.cli job [name]` | List or run scheduled jobs (`drain-outbox`, `prune-expired`, `reconcile-billing`, `reconcile-memberships`) |

## Testing

- **API (`apps/api/tests`)**:
  - unit tests (entitlement matrix, authorization policy, configuration safety, token verification, adapters, redaction);
  - integration tests against a throwaway PostgreSQL database (row-level security, negative security tests NT-01 to NT-12, webhooks, concurrency, resilience, telemetry leakage, migration safety, query counts, integration diagnostics).
- **Web (`apps/web`)**: Vitest component and library tests; Playwright end-to-end tests with axe accessibility checks.

Tests never use your `.env`. Database tests create and drop their own database, and refuse non-local servers.

## Verification status

| Area | Status |
|---|---|
| Unit, integration, and security tests | Run locally (see the latest evidence in `dev-docs/evidence/`) |
| Clerk, Stripe, Supabase, PostHog, Sentry, LLM | **Mocked only.** Sandbox verification is pending (DEC-006); the `/integrations` page is the tool for it |
| Vercel deployment (Python runtime) | **Not verified on Vercel** (DEC-001). Local proof only: packaging, Vercel's runtime shim, Services routing, transaction pooling, and database faults pass ([evidence](dev-docs/evidence/dec-001-vercel-python.md)) |
| Performance | PERF-01 to PERF-07 recorded locally, for relative comparison only. Targets are **yours to set** ([perf/run-plan-template.md](perf/run-plan-template.md)) |
| Backup and restore | Local restore drill only. Objectives and the hosted restore test are **yours to set** ([checklist](docs/operations.md#backup-and-restore)) |
| Data retention, deletion, export | No deletion or export feature. Policy is **yours to set** ([checklist](docs/data-inventory.md#adopter-decision-checklist)) |

## Before you launch

The template leaves these decisions to each adopter ([ADR-0002](dev-docs/adr/0002-organization-entitlements-contacts-adopter-decisions.md)):

1. Security and conduct contacts: `init-template` asks for them, and CI fails while placeholders remain.
2. Data retention, deletion, export, and analytics consent: [docs/data-inventory.md](docs/data-inventory.md#adopter-decision-checklist).
3. Backup objectives, plus a timed hosted restore test: [docs/operations.md](docs/operations.md#backup-and-restore).
4. Performance targets, from your own preview baseline: [perf/run-plan-template.md](perf/run-plan-template.md).
5. Sandbox verification of every integration: [docs/integrations.md](docs/integrations.md), plus the `/integrations` page.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Invalid configuration: ...` on API start | Set the named variables in `.env`; see `.env.example` comments |
| Database tests are skipped | `pnpm db:up`, or set `TEST_DATABASE_URL` to a **local** server |
| `/api/v1/*` returns 500 in the browser | The API is not running on `API_ORIGIN`; run `pnpm dev` |
| Signed in but API returns 401 | Add your web origin to `CLERK_AUTHORIZED_PARTIES` |
| Organization pages return 404 | You are not a member in Clerk, or the API cannot reach Clerk; check `CLERK_SECRET_KEY` |
| Upgrade succeeded but plan still Free | Webhooks are not reaching the API; see the Stripe CLI section in docs/integrations.md |
| Integrations page says diagnostics are not available | Add your Clerk user ID to `DIAGNOSTICS_OPERATOR_IDS`, or use `APP_ENV=local` |
| A check is "Skipped" | That integration is in `mock` or `disabled` mode; set it to `sandbox` in `.env` |

## Contributing and security

See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md). Licensed under [Apache-2.0](LICENSE).
