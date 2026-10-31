# Limitations

What is verified, what is only assumed, what was deferred, and what the template deliberately does not do. Status on 2026-10-31; update this page when you record your own evidence.

## Verification status

| Area | Status |
|---|---|
| Unit, integration and security tests | Run locally with `pnpm verify`. Evidence is `mocked` (fakes and loopback stubs) or `local` (real software on one machine) |
| Clerk, Stripe, PostHog, Sentry, LLM provider, hosted Supabase | **Mocked only.** No provider account has been contacted. The per-provider table, the tests behind it, and the command that upgrades each row: [integrations.md](integrations.md#status-of-each-integration) |
| Vercel deployment | **Not deployed and not verified.** Packaging, routing and the function bundle are built with the pinned Vercel CLI and checked locally (`pnpm test:vercel-build`), which is not platform evidence |
| Sandbox end-to-end tests (signed-in browser flows) | Written and not run: they need a Clerk development instance |
| Stripe sandbox suite | Written and not run: it needs your sandbox key |
| Performance | Recorded locally for relative comparison only. Targets are yours to set ([perf/run-plan-template.md](../perf/run-plan-template.md)) |
| Backup and restore | The local restore drill only. Objectives and the hosted restore test are yours ([operations.md](operations.md#backup-and-restore)) |
| Data retention, deletion, export | No deletion or export feature. The policy is yours ([data-inventory.md](data-inventory.md#adopter-decision-checklist)) |
| Test coverage measurement | NOT VERIFIED: no coverage tool is installed (TEST-03) |

## Assumptions not verified

Each is true by design or by documentation and has not been observed. What would verify it is named. The first deployment you authorize ([deployment.md](deployment.md#verify-the-first-preview)) settles most of them.

**Platform (Vercel)**

- **Services accepts this configuration.** Services is in Beta on all plans, and the project's Framework Preset must be **Services**. Verify: create the project and deploy a preview.
- **The Next.js proxy runs inside the web service.** Vercel documents that middleware is not supported inside a service; a local build shows the proxy emitted as a Node.js function. If the proxy does not run, a signed-out visitor to a protected page gets the page shell and the browser-side page gate redirects after load, and the proxy's `azp` check is not active either. The API authorizes every operation and enforces `azp` on every call regardless, so this affects redirects only. Verify: `curl -sI https://<preview-host>/dashboard` signed out; `307` means the proxy ran, `200` means it did not.
- **The router sends `/api/v1/*` to the API before the catch-all**, with raw request bodies for webhooks. Verify: `curl -s https://<preview-host>/api/v1/health`; a Stripe test delivery.
- **The four crons register from the root `vercel.json`** and call the routes. Crons run on production deployments only. Verify: a production deployment.
- **The API function runs in the configured region** (the build output only carries the field), and **cold starts, duration, memory, payload and the 500 MB bundle limit** hold on the platform (the local bundle is 74.5 MiB). Instant Rollback restores web, API and cron definitions together.
- **Shared environment.** What the web process can read of the API's secrets on the platform is not observed ([deployment.md](deployment.md#variables-which-service-reads-what)).
- **`VERCEL_URL` and `VERCEL_BRANCH_URL` reach the web build**, and a Vercel custom environment reports `VERCEL_ENV=preview`. Verify: sign in on a preview URL and open `/dashboard`.
- **A Vercel build installs the Python dependencies like the local `uv` install.**
- **After-response work completes before the instance is frozen:** the telemetry flush and the outbox dispatch. If it does not, errors and events can be late or lost; `drain-outbox` delivers the outbox events at its next run. Verify: trigger an error and a checkout start on an idle preview; both must arrive.
- **Providers accept a webhook URL with a query string** (the Protection Bypass parameter). Verify: register a protected preview's endpoint and send a test delivery.

**Providers**

- **Hosted Supabase:** the pooler's user routing (`app_login.<project-ref>`), SCRAM verifiers through the shared pooler, and the hosted image's extensions. The Supabase image tested is `17.6.1.106`; the newer `17.11.0.004` was not pulled (try it with `SUPABASE_POSTGRES_IMAGE`). Five database tests are skipped on that image because they create a superuser role, which `postgres` there cannot do.
- **Stripe:** the sandbox suite; `pnpm setup:stripe`; the portal check against a real portal configuration (field names come from the installed SDK's types and loopback stubs); expiring real Checkout sessions; the restricted-key permission needed to read portal configurations.
- **Clerk:** the sign-in-token flow of the sandbox end-to-end run, user creation through the Backend API, and that Device Trust does not affect a token sign-in (taken from Clerk's documentation); the `currentTask` property names used to detect a pending session (taken from clerk-js's types); Clerk's real session behavior around sign-out and organization switches (the page-gate tests mock Clerk); the Clerk CLI route to Membership optional. The sandbox run must show that signing out from a protected page lands on the home page and that switching organizations does not reload the page.
- **PostHog and Sentry:** real delivery, the managed proxy with this template, and source-map upload.
- **A page opened in a browser with the placeholder Clerk pair:** no page of the web app was seen in a browser without a Clerk instance (command-line checks show the handshake redirect).

<!-- template-only:start -->
**Template maintenance.** The local DEC-001 proof (`proofs/dec-001/run.sh`) was last run before the cycle 1 changes to the database login, the root `vercel.json` and the Services layout. It was not re-run: it needs Docker Desktop and about ten minutes.
<!-- template-only:end -->

## Deferred review findings

Ten findings from the architecture review were deferred, with the reason. What to do if one matters to you:

| Finding | Reason it was deferred | If it matters to you |
|---|---|---|
| Provider-call limits are 13 hand-written mechanisms in services, not at ports, and provider calls share the request threadpool (`api-arch-bulkheads-at-ports`) | A large kernel refactor across every provider call path; re-evaluate in cycle 2 | When you add a provider, follow an existing bounded call: a concurrency bound, and its threads in `reserved_request_threads` ([operations.md](operations.md#connection-pools)). The architecture tests refuse two settings that read one variable |
| Generated modules copy tenancy, audit and pagination code that is kept in step with `projects` by hand (`api-arch-generated-crud-forks-security-code`) | Needs kernel helpers and a generator rewrite; re-evaluate in cycle 2 | After you change a security-relevant rule in `projects`, apply it to modules generated earlier. `pnpm test:new-module` checks fresh output only |
| Three files exceed ARCH-06 with mixed concerns, outbox and audit SQL live in the connection layer, and some port methods are dead (`api-arch-oversized-units`) | A mechanical split with no behavior gain | Split them when you next change them |
| Outbox handlers receive untyped payload dicts, and event schemas have no version (`api-arch-typed-events`) | Low severity | Validate the keys you read in your handler; version event names yourself |
| No shared data layer: each web feature hand-rolls fetch, pagination and mutation state, and the generator copies a 265-line state machine that has drifted (`web-arch-data-layer-copied-per-feature`) | A large redesign; re-evaluate in cycle 2 or 3 | Treat each panel's state handling as its own code: when you fix one, check the others and the generator template |
| No forms foundation: API field errors are dropped, error codes are untyped, and there are no field or dialog primitives (`web-arch-forms-errors-foundation`) | Forms are product-specific | Build your forms on the API error type and map field errors in `apps/web/src/lib/api/errors.ts` |
| No reusable test kit: shared helpers live inside test modules, generated tests copy them, and cleanup uses a fixed table list (`testing-adopter-test-kit`) | Low severity | When you add a table, add it to the cleanup list in `apps/api/tests/conftest.py` |
| Organizations are invisible to analytics: neither app sends PostHog groups (`obs-no-organization-groups`) | Groups are an adopter analytics choice | Add group calls through the event allowlist, and update [data-inventory.md](data-inventory.md): it is a privacy decision |
| Every migration runs in a transaction, so no `CREATE INDEX CONCURRENTLY`; the schema lint is `squawk` with that rule switched off (`db-runner-transaction-only-no-online-index`) | The proposed safeguards are unsound through poolers | Build the index by hand with `CONCURRENTLY`, then add the migration with `IF NOT EXISTS` ([operations.md](operations.md#migrations)) |
| Organization flows (create, switch, member role, removal) have no automated sandbox test (`clerk-org-lifecycle-no-sandbox-test`) | Cannot be written or verified without Clerk sandbox keys | Do the manual check in [integrations.md](integrations.md#checking-an-integration) and record it |
| Installing dependencies runs their install scripts and accepts versions published minutes ago (`secarch-supply-chain-gates`) | Release-age limits need pnpm 10.16 or newer; the repository pins pnpm 9 | Installs are frozen to the lockfile and Dependabot proposes updates: review them. Upgrade pnpm in your copy to add release-age limits |

## Known limits by area

**Sign-in, pages and authorization**

- Page protection is presentation (FE-05): the proxy and the page gate in the root layout read one list; the API is the only gate. A visitor with JavaScript off gets a placeholder. An unknown URL sends a signed-out visitor to sign-in, and a crawler gets `307` for a dead link.
- A signed-in visitor sees "Loading..." instead of the page frame until Clerk loads.
- Opening the app at an origin that `APP_BASE_URL` and `CLERK_AUTHORIZED_PARTIES` do not name loops between `/sign-in` and the page. IDN origins must be listed in punycode. A custom domain assigned to a branch must be listed or be `APP_BASE_URL`.
- What the UI offers (`can` on each list) is as fresh as the membership mirror (at most 300 s); the routes re-check. A new action needs its flag added by hand. A screen with no list response has no flag.
- Impersonation is audited only where a use case writes an audit entry (organization project deletes, diagnostics); the `actor_session` log line records the rest.
- Diagnostics operators are authenticated by a listed Clerk user ID and a valid session; no second factor is required. They are off in production unless `DIAGNOSTICS_ENABLED=true`, and on for listed operators elsewhere; every run and refusal is audited. The capability is registered as DEC-014 (option B adopted by delegation, awaiting the owner's confirmation; [ADR-0004 §15](../dev-docs/adr/0004-cycle-1-adopted-decisions.md)). Require multi-factor authentication for every listed operator in Clerk. If you enable diagnostics in production, choose option (A) and have the API check the session's second factor (`fva`); that check is not built.

**Billing**

- Plans are Free and Pro with two price IDs (and a database `CHECK` on the plan key). Subscriptions are matched by price, so a price change needs a retained price. Granting Pro by Stripe product instead, and feature entitlements beyond the binary Pro check (BILL-01), are open (spec §23, step 7).
- The sandbox suite does not cover retry exhaustion. A DB-level race between two checkouts of one user in about a second is closed after the fact (the loser's session is expired), not prevented.
- With `DATABASE_WORKER_POOL_MAX_SIZE=2` user re-reads are always refused (`503`) and the page falls back to polling.
- A completed checkout leaves its row in `app.billing_checkout_sessions` until the user's next checkout.
- The webhook endpoint is not checked by `/integrations`.

**Database**

- `app_login` can still `SET ROLE app_worker`: see [integrations.md](integrations.md#supabase-and-postgresql). Row-level security is not a defense against SQL injection.
- Rotating `app_login` is not zero-downtime: the old password stops at once. The login check runs on a process's first transaction; a refused process keeps refusing until restarted.
- The migration lint skips the first eight migrations by exact name; squashing or removing migrations needs an edit to `.squawk.toml`. It fails if every migration is excluded.
- A `service=` entry's `sslmode` is invisible to the TLS default, so such connections are left unchanged.

**Telemetry**

- An opaque string in the path of an unmatched URL is sent as the transaction name and `request.url`: emails and token-shaped strings are redacted, other text cannot be recognized. Exception and log messages are only pattern-redacted.
- The browser allowlist keeps any `$`-prefixed property name (the SDK's own), and drops other properties that are not allowlisted, including a plain `utm_source` key.
- The error throttle is per instance and in memory: a fleet of N instances can report one cause N times a minute. A Stripe rejection and a Stripe outage are one error type in Sentry.
- A sampled trace of a request can be lost when an instance is frozen; errors are not.

**Setup and tooling**

- `pnpm dev` is macOS and Linux only (WSL on Windows). Its API health probe is fixed to `127.0.0.1:8000` with a 60 s limit (`API_START_TIMEOUT_MS` in `scripts/dev.mjs`).
- `pnpm check:setup` does not detect a Clerk publishable key and secret key from two instances, and does not check webhook endpoints.
- `CLERK_JWT_KEY` is an API setting. The web app validates its format but uses nothing from it: `@clerk/nextjs` 7.9.15 does not read it.
- `scripts/templates/adopter/AGENTS.md` can drift from `AGENTS.md`; keep them in step.

**CI**

- `scripts/npm-audit-bulk.mjs` audits the production tree installed on the machine it runs on, so native packages for other platforms are not checked (CI audits linux x64, which is what Vercel builds). `pnpm audit --dev` still uses the legacy endpoint.
- Dependabot does not read the gitleaks version pins (in `ci.yml` and in the pre-commit hook), nor the Supabase image tag in `scripts/test-supabase-image.mjs`. Bump them by hand; a test keeps the two gitleaks versions equal.
- The ESLint guards in `apps/web/eslint.guards.mjs` (including the ban on importing `posthog-js` outside `src/lib/analytics.ts`) are tested but not loaded by `eslint.config.mjs`: an owner-applied step (spec §23, step 4). Until it lands, `src/no-client-roles.test.ts`, `src/lib/page-access.test.ts`, `src/app/layout.test.tsx` and `src/rendering-baseline.test.ts` carry the web fitness checks.
- No `CODEOWNERS` file ships (SEC-12 relies on one for `supabase/migrations/` and `.github/workflows/`). Add yours.

## Follow-ups in the code

Found while writing these documents or listed in the package notes, and not done. They need a code change:

- Run `check-bundle-secrets.mjs --require-values` inside every Vercel build (chain it into the web `build` script), so the web service is checked with the real values. Today it runs in `pnpm test:vercel-build` with canary values.
- A per-user limit on ordinary writes (creating and deleting projects, and every generated module), and a guard against concurrent or repeated runs of a job through `GET /api/v1/jobs/{name}`: a leaked `CRON_SECRET` can start `reconcile-billing` repeatedly, at up to `BILLING_RECONCILE_BATCH_SIZE` Stripe calls a run. Until then: the WAF rule (step D7) and rotating `CRON_SECRET`.
- Wire `apps/web/eslint.guards.mjs` into `eslint.config.mjs` (a lint-config edit that an owner applies).
- A validator message for driver-only URL parameters that names the parameter instead of blaming percent-encoding.
- `scripts/init-template.mjs` and the messages of `apps/api/app/doctor.py` and `scripts/lib/preflight.mjs` still say "see docs/integrations.md"; the setup guide is [setup.md](setup.md) (the `#clerk` anchor they use is kept).
- A Clerk key-pair check in `IdentityProbe` (compare `GET /v1/jwks` with `<issuer>/.well-known/jwks.json`); a doctor check for stray env files; an optional pass-through proxy so `pnpm dev` runs without Clerk keys; refusing the placeholder pair when `APP_ENV=preview`.
- A test that the docs list exactly `SYNC_EVENT_TYPES`, and a `stripe-events` command to feed `stripe listen --events`; a webhook-endpoint check and a "last verified webhook" line on `/integrations`.
- A second, worker-only login role and pool; detection of `supabase_migrations.schema_migrations` in the runner; a no-transaction migration mode for `CREATE INDEX CONCURRENTLY`.
- Naming unmatched transactions `unmatched` and dropping `request.url` for them (OBS-07).
- An `ErrorCode` enum with exhaustive web typing; coverage tooling (TEST-03); the "migrate twice" check as a pytest test.
- A web-side `CLERK_JWT_KEY` wired into the proxy (it needs a path through `next.config.ts`, because `proxy.ts` may not read `CLERK_*` variables).

## What the template deliberately does not do

These choices were adopted by delegation of the owner and are reversible; [ADR-0004](../dev-docs/adr/0004-cycle-1-adopted-decisions.md) records each with its alternative.

- **No first-party relay for PostHog or Sentry.** Browser events go straight to the providers, so blockers drop some. The vendors' default relay recipes (a Sentry `tunnelRoute`, a PostHog `/ingest` rewrite) forward every cookie, Clerk's `__session` included, which the API accepts as a bearer token. The supported fix is PostHog's managed proxy ([integrations.md](integrations.md#posthog)).
- **No nonce-based Content Security Policy.** The CSP is static and keeps `'unsafe-inline'` in `script-src` and `style-src`, because Next.js inlines its bootstrap scripts and Clerk needs inline styles. An injected inline script is not blocked by the CSP; defense rests on React's escaping, `react/no-danger`, the lint guards and `frame-ancestors`. A nonce policy needs a per-request header and makes every page dynamic. There is no CSP violation reporting.
- **No impersonation by default.** A session someone else started as a user (an `act` claim) gets `403` unless `CLERK_ALLOW_IMPERSONATION=true`; when allowed, the actor is recorded.
- **No Customer Portal configuration.** The portal stays in the Stripe Dashboard (ADR-0001); the template reads it and reports what is missing.
- **No keyless Clerk mode.** Both keys are required: the API derives the token issuer from the publishable key and reads memberships with the secret key.
- **No server-rendered data.** Every page is a static shell that loads its data in the browser through the typed client. Server-side fetching, Route Handlers and Server Actions are not part of the baseline; adding them is an architectural change that needs an ADR ([architecture.md](architecture.md#frontend)).
- **No organization plan.** Subscriptions belong to individuals (ADR-0002); a Team tier is a scope change.
- **No account deletion or export, no recovery objectives, no performance targets, no analytics consent banner.** These are adopter decisions ([deployment.md](deployment.md#before-you-launch)).
- **No application built on the LLM.** The integration is a connectivity check; `LLM_MODE=disabled` is a supported mode.
