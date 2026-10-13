# SaaS Template

An open-source SaaS starter: a **Next.js** (TypeScript, strict) web app and a **Python (FastAPI)** API, with **Clerk** authentication and organizations, **Stripe** Free/Pro billing (monthly and yearly), **Supabase/PostgreSQL** with row-level security, **PostHog** analytics, **Sentry** error monitoring, and a provider-agnostic **LLM** connectivity check.

It is secure by default: every API operation is authorized on the server, tenants are isolated by the database as well as the application, and Pro access is granted only from verified Stripe data.

> **Status:** pre-release. Local development and the automated test suites work. Deployment to Vercel has **not** been verified yet (DEC-001), and external integrations have been exercised only with mocks. See [Verification status](#verification-status).

## Architecture

```
Browser ──► Next.js (apps/web) ──/api/v1/* rewrite──► FastAPI (apps/api) ──► PostgreSQL (schema "app", RLS)
              │ Clerk UI                                  │ verifies Clerk session tokens
              │ PostHog / Sentry (browser)                ├─► Clerk Backend API (memberships)
                                                          ├─► Stripe (checkout, portal, webhooks)
                                                          ├─► LLM provider (connectivity check)
                                                          └─► PostHog / Sentry (server)
```

- The web app contains presentation only. It never talks to the database or to Stripe with secret keys.
- The API is layered: `domain` (pure rules) → `application` (use cases) → `ports` (interfaces) ← `infrastructure` (adapters) and `http` (FastAPI). Import boundaries are enforced in CI.
- Every database transaction runs as a non-privileged role (`app_api` or `app_worker`), with forced row-level security.

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
pnpm init-template --name "Your Product" --slug your-product   # optional: rename, creates .env
cp -n .env.example .env    # if you skipped init-template
pnpm db:up                 # start local PostgreSQL (127.0.0.1:54329)
pnpm db:migrate            # apply migrations
```

Edit `.env`. At minimum, set your Clerk **development** instance keys (`NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`, `CLERK_SECRET_KEY`); everything else can stay in `mock` or `disabled` mode. [docs/integrations.md](docs/integrations.md) explains each service, its callback URLs, and how to switch it to `sandbox`.

```bash
pnpm dev                   # API on :8000, web on :3000
```

Open http://localhost:3000. The API refuses to start if configuration is missing or inconsistent, and the error names the variables involved without printing their values.

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
| `pnpm db:reset` | Drop, migrate, and seed the **local** database |
| `pnpm billing:reconcile` | Compare billing records with Stripe (dry run; add `--apply`) |

## Testing

- **API (`apps/api/tests`)**:
  - unit tests (entitlement matrix, authorization policy, configuration safety, token verification, adapters, redaction);
  - integration tests against a throwaway PostgreSQL database (row-level security, negative security tests NT-01 to NT-12, webhooks, concurrency, resilience, telemetry leakage, migration safety).
- **Web (`apps/web`)**: Vitest component and library tests; Playwright end-to-end tests with axe accessibility checks.

Tests never use your `.env`. Database tests create and drop their own database, and refuse non-local servers.

## Verification status

| Area | Status |
|---|---|
| Unit, integration, and security tests | Run locally (see the latest evidence in `dev-docs/evidence/`) |
| Clerk, Stripe, Supabase, PostHog, Sentry, LLM | **Mocked only.** Sandbox verification is pending (DEC-006) |
| Vercel deployment (Python runtime) | **Not verified** (DEC-001) |
| Performance targets | **Not defined** (DEC-003) |

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Invalid configuration: ...` on API start | Set the named variables in `.env`; see `.env.example` comments |
| Database tests are skipped | `pnpm db:up`, or set `TEST_DATABASE_URL` to a **local** server |
| `/api/v1/*` returns 500 in the browser | The API is not running on `API_ORIGIN`; run `pnpm dev` |
| Signed in but API returns 401 | Add your web origin to `CLERK_AUTHORIZED_PARTIES` |
| Organization pages return 404 | You are not a member in Clerk, or the API cannot reach Clerk; check `CLERK_SECRET_KEY` |
| Upgrade succeeded but plan still Free | Webhooks are not reaching the API; see the Stripe CLI section in docs/integrations.md |

## Contributing and security

See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md). Licensed under [Apache-2.0](LICENSE).
