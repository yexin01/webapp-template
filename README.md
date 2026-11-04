# SaaS Template

An open-source SaaS starter: a **Next.js** (TypeScript, strict) web app and a **Python (FastAPI)** API, with **Clerk** authentication and organizations, **Stripe** Free/Pro billing (monthly and yearly), **Supabase/PostgreSQL** with forced row-level security, **Resend** transactional email, **PostHog** analytics, **Sentry** error monitoring, and a provider-agnostic **LLM** connectivity check, meant to deploy on **Vercel**.

It is secure by default: every API operation is authorized on the server, tenants are isolated by the database as well as the application, and Pro access is granted only from verified Stripe data. Subscriptions belong to individuals: a user's Pro plan covers their own actions in every workspace they belong to and is never shared ([ADR-0002](dev-docs/adr/0002-organization-entitlements-contacts-adopter-decisions.md)). A minimal UI exercises every integration, including an **Integrations** page (`/integrations`) that shows each service's mode and runs a live check against it.

The authoritative engineering specification is [`dev-docs/engineering-implementation-plan.md`](dev-docs/engineering-implementation-plan.md). Decisions are recorded in [`dev-docs/adr/`](dev-docs/adr/).

<!-- template-only:start -->
> **Status:** pre-release. Local development and the automated test suites work. Nothing has been deployed (DEC-001), and no external integration has been run against a real provider: see [docs/limitations.md](docs/limitations.md).
<!-- template-only:end -->

## Quick start

Prerequisites: Node.js 24 LTS (22.12 or newer works), pnpm 9.15, [uv](https://docs.astral.sh/uv/) 0.11 or newer, and Docker. Versions, install notes and what to do on Node 25: [docs/setup.md](docs/setup.md#prerequisites).

```bash
git clone <repository-url> your-product && cd your-product
pnpm init-template --name "Your Product" --slug your-product \
  --security-contact security@your-product.example \
  --conduct-contact conduct@your-product.example   # renames the template and creates .env
                           # add --db-port 54330 to run several projects side by side
pnpm bootstrap             # install JavaScript and Python dependencies from the lockfiles
pnpm db:up && pnpm db:migrate   # local PostgreSQL in Docker (127.0.0.1:54329), then the schema
pnpm check:setup           # validate the configuration; no account needed
pnpm dev                   # API on :8000, web on :3000
```

Everything up to `pnpm check:setup` needs no account and no key. `pnpm dev` needs the two keys of a free Clerk development instance, with Organizations enabled and **Membership optional**: until you paste them it stops with a message, because the web app cannot show a page without a Clerk instance. Then open http://localhost:3000 and http://localhost:3000/integrations.

The full guide is [docs/setup.md](docs/setup.md). It starts with one checklist of the steps only a person can do (accounts, keys, dashboard-only settings, authorizations); everything else is a command.

## Commands

Tests never use your `.env`. Database tests create and drop their own database, and refuse non-local servers.

**Develop**

| Command | Purpose |
|---|---|
| `pnpm bootstrap` | Install JavaScript and Python dependencies from the lockfiles |
| `pnpm api:lock` | Update `apps/api/uv.lock` after you edit a Python dependency (`pnpm install` updates the JavaScript lockfile) |
| `pnpm dev` | Check the configuration, then run the API and web app with reload (starts the Stripe CLI listener when billing is in sandbox mode). macOS and Linux only: on Windows use WSL, or run `pnpm dev:api` and `pnpm dev:web` in separate terminals |
| `pnpm build` | Production build of the web app from your `.env`. The placeholder Clerk keys of `.env.example` build; a build you deploy needs your own |
| `pnpm build:check` | Account-free production build (it gets a minimal environment, not your shell's, and reads no `.env`; the scan reads both, to look for their values) plus the bundle secret scan: what CI runs |
| `pnpm format` | Format the web app (Prettier) and the API and perf code (Ruff) |
| `pnpm contracts` | Regenerate the OpenAPI document and the TypeScript API types |
| `pnpm new:module <name>` | Scaffold a tenant-scoped feature module end to end ([docs/architecture.md](docs/architecture.md#adding-a-feature)) |
| `pnpm hooks:install` | Scan staged changes for secrets before each commit ([gitleaks](https://github.com/gitleaks/gitleaks), or its pinned container image when Docker runs). Undo with `git config --unset core.hooksPath` |
| `pnpm perf:server` / `pnpm perf:run` | Local performance server and k6 scenarios ([perf/README.md](perf/README.md)) |

**Check**

| Command | Purpose |
|---|---|
| `pnpm check:setup [--send-test-events]` | Validate the configuration and run the checks of the Integrations page without signing in |
| `pnpm verify` | The checks of CI's api, web and secrets jobs, in CI's order, ending with a PASS/FAIL table; a step that cannot run is SKIPPED, and a run in which nothing passed ends NOTHING RAN (`--help` lists the options). It stops first at a file git does not track yet (`git add -A`: the generator step copies tracked files). CI's other jobs are `pnpm test:vercel-build` and `pnpm test:supabase` |
| `pnpm verify:quick` | Lint, format, types, and the API and web tests |
| `pnpm verify:sandbox` | The checks that need provider accounts, each when its keys are in `.env`; ends with an evidence record. It creates a test user in your Clerk development instance and objects in your Stripe sandbox, so it needs the owner's go-ahead (A3 in the [checklist](docs/setup.md#manual-steps-checklist)) |
| `pnpm test` | API tests (needs `pnpm db:up`) and web unit tests |
| `pnpm test:e2e` | Playwright tests. Account-free pages, axe and keyboard checks run by default; `E2E_SANDBOX=1` adds the signed-in flows, which create a Clerk test user (A3) |
| `pnpm test:coverage` | Coverage of the API (`pytest-cov`, with branches) and the web app (`@vitest/coverage-v8`), printed in the terminal; needs `pnpm db:up`. It reports only: no threshold fails it ([limitations](docs/limitations.md#what-the-template-deliberately-does-not-do)) |
| `pnpm test:stripe-sandbox` | The Stripe lifecycle suite against your sandbox (needs keys; changes the sandbox) |
| `pnpm lint` / `pnpm typecheck` | ESLint, the Prettier check, Ruff (lint and format check), import contracts and migration lint / `tsc` and mypy (strict) |
| `pnpm contracts:check` | Fail if the generated contracts are stale |
| `pnpm check:bundle-secrets` | Fail if server-only values appear in the browser bundle (after `pnpm build`) |
| `pnpm check:links` | Fail if a relative Markdown link, its `#heading` fragment, or a `docs/…#heading` pointer in code does not resolve |
| `pnpm test:scripts` | The tests of the scripts in `scripts/` |

**Database**

| Command | Purpose |
|---|---|
| `pnpm db:up` / `pnpm db:down` | Start or stop the local PostgreSQL 17 container |
| `pnpm db:migrate` | Apply pending migrations (local databases only unless `--allow-remote`) |
| `pnpm db:api-login` | Give the runtime role `app_login` a password for a hosted database (`--allow-remote`; [docs/setup.md](docs/setup.md#supabase-optional-hosted-database)) |
| `pnpm db:seed --yes` | Load deterministic synthetic fixtures into the **local** database. They are not Clerk accounts, so they never appear when you sign in. Refuses without `--yes` |
| `pnpm db:reset --yes` | Drop schema `app` and the migration ledger, re-apply the migrations and seed (**local** only). Objects a migration creates outside `app` survive it; for an empty start use `docker compose down -v && pnpm db:up && pnpm db:migrate`. Pass `--yes` after the script name, not after `--`. Both seed and reset need `APP_ENV=local` or `test` set explicitly (`.env` from `.env.example` does), and a local host or a Unix socket in a standard local directory |
| `pnpm db:restore-drill` | Dump the local database, restore it into a throwaway PostgreSQL container, verify it, and remove the container ([details](docs/operations.md#backup-and-restore)) |

**Billing**

| Command | Purpose |
|---|---|
| `pnpm setup:stripe --currency usd --monthly 900 --yearly 9000` | Create the Pro product and its two prices in a Stripe **sandbox** and print the `.env` lines (amounts in minor units) |
| `pnpm billing:reconcile` | Compare billing records with Stripe (dry run; add `--apply`) |
| `cd apps/api && uv run python -m app.cli job [name]` | List or run scheduled jobs (`drain-outbox`, `prune-expired`, `reconcile-billing`, `reconcile-memberships`) |

**Deploy and CI**

| Command | Purpose |
|---|---|
| `pnpm test:vercel-build` | Build the root `vercel.json` with the pinned Vercel CLI and assert the output; deploys nothing |
| `pnpm test:supabase` | Run the API tests on Supabase's PostgreSQL image (needs Docker) |
| `pnpm security:audit` | Dependency vulnerability audit: the web production tree and the API lockfile fail on any advisory; the web development tree is report only |
| `pnpm security:licenses` | Fail if a production dependency's license is not on the allowlist in `scripts/check-licenses.mjs` |

**Template**

| Command | Purpose |
|---|---|
| `pnpm init-template` | Rename the template, fill in the community contacts, and create `.env` (see [docs/setup.md](docs/setup.md#steps)) |
| `pnpm test:init-template` | Check that `init-template` leaves no original identifiers (runs on temporary copies of the tracked files; `git add` new files to include them) |
| `pnpm test:new-module` | Check that freshly generated modules pass every gate, their own tests included (same copy rule, but it stops at any file git does not track yet: `git add -A` first, as after `pnpm new:module`) |
| `pnpm check:placeholders` | Fail if `SECURITY.md` or `CODE_OF_CONDUCT.md` still has a contact placeholder after the project was renamed |

## Repository map

- `apps/web/`: the Next.js app. Routes in `src/app`, feature UI in `src/features/*`, presentational components in `src/ui`, infrastructure in `src/lib`.
- `apps/api/`: the FastAPI app. A shared kernel (`app/kernel`) and feature modules (`app/modules/{identity,billing,projects,llm,diagnostics}`), each with domain, ports, service, adapters, routes and wiring. Tests in `tests/`.
- `packages/contracts/`: the generated OpenAPI document, and the fixtures both apps' configuration tests share.
- `supabase/`: the migrations (applied by `pnpm db:migrate`, not by the Supabase CLI), `seed.sql`, and `restore/`.
- `scripts/`: the commands behind the `pnpm` scripts.
- `docs/`: the documentation below. `dev-docs/`: the specification, the ADRs and the evidence records.
- `perf/`: k6 scenarios and the local performance server.
<!-- template-only:start -->
- `proofs/`, `docs-notes/`: the template maintainers' records; `pnpm init-template` removes them from your copy.
<!-- template-only:end -->
- `.github/`: CI, CodeQL, the **Database migrate** workflow, and the issue and pull request templates.
- `vercel.json`: one Vercel project with Services (web and API together, and the crons). `docker-compose.yml`: the local database. `.env.example`: every setting, in tiers.

## Documentation

| Read | For |
|---|---|
| [docs/setup.md](docs/setup.md) | Clone to a running app, a configured sandbox, and a first preview. Starts with the checklist of manual steps |
| [docs/integrations.md](docs/integrations.md) | Each provider: variables, checks, failure modes, and what is verified |
| [docs/deployment.md](docs/deployment.md) | Vercel, GitHub and production: the variable map, previews, rollback, and what to decide before launch |
| [docs/operations.md](docs/operations.md) | Key rotation, migrations, jobs, incidents, backups, outages |
| [docs/limitations.md](docs/limitations.md) | What is not verified, what was deferred, and what the template deliberately does not do |
| [docs/architecture.md](docs/architecture.md) | Architecture, module anatomy, how to add a feature |
| [docs/data-inventory.md](docs/data-inventory.md) | The personal data the template handles, and the retention and deletion checklist |
| [AGENTS.md](AGENTS.md), [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md) | Rules for agents and contributors, and how to report a vulnerability |

## Contributing and security

See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md). Licensed under [Apache-2.0](LICENSE).
