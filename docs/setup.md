# Setup

A linear guide from `git clone` to a running local app, a configured sandbox environment, and a first preview deployment. Do the parts in order; each ends with a command that says whether you are done.

1. [Part 1: Local, no accounts](#part-1-local-no-accounts). Tests, build and API run with no account and no key.
2. [Part 2: Sandbox accounts and keys](#part-2-sandbox-accounts-and-keys). The web app needs a free Clerk account; every other provider is optional.
3. [Part 3: First preview deployment](#part-3-first-preview-deployment). Summary; the detail is in [deployment.md](deployment.md).

Reference material is elsewhere: what each provider does and how it fails is in [integrations.md](integrations.md); every variable is in [`.env.example`](../.env.example); procedures after launch are in [operations.md](operations.md); what is not verified is in [limitations.md](limitations.md).

## Manual-steps checklist

Everything in this guide is a command, except the steps below. Only a person can do these: create an account, copy a key, change a dashboard-only setting, or authorize a change to a remote service. Everything else is automated: the template renames itself, creates `.env`, migrates the database, creates the Stripe product and prices, starts the Stripe listener, and verifies each integration. The commands that change a provider account or a hosted database also need the owner's authorization (A3).

Each row has an ID. The step in the guide that does it carries a bold tag made of `YOU` and the same ID, so the checklist and the guide cannot drift. To check that they agree, run from the repository root (it prints nothing but the confirmation when every ID appears once in the checklist and once in the guide):

```bash
diff <(grep -ohE '\*\*\[YOU [A-Z][0-9]+\]\*\*' docs/setup.md docs/deployment.md | grep -oE '[A-Z][0-9]+' | sort) \
     <(grep -ohE '^\| [A-Z][0-9]+ ' docs/setup.md | grep -oE '[A-Z][0-9]+' | sort) && echo "checklist and guide agree"
```

### Needed to run locally

| ID | Step | Guide |
|---|---|---|
| L1 | Install Node.js, pnpm, uv and Docker. No account, no key | [Prerequisites](#prerequisites) |

### Needed for sandbox

| ID | Step | You get | Guide |
|---|---|---|---|
| S1 | Create a Clerk account and an application; use its Development instance | A Clerk instance | [Clerk](#clerk) |
| S2 | Enable Organizations with **Membership optional** | The personal workspace appears | [Clerk](#clerk) |
| S3 | Copy the publishable and secret key into `.env` | `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`, `CLERK_SECRET_KEY`; `pnpm dev` starts | [Clerk](#clerk) |
| S4 | Create a Stripe sandbox and put its `sk_test_` key in `.env` | `STRIPE_SECRET_KEY` | [Stripe](#stripe) |
| S5 | Save the Customer Portal settings and turn off bank debits in the Stripe Dashboard (test mode) | The portal check passes; Pro is not granted before funds arrive | [Stripe](#stripe) |
| S6 | Set Billing > Revenue recovery > Retries to cancel or mark unpaid | Pro does not last forever on `past_due` | [Stripe](#stripe) |
| S7 | Install the Stripe CLI | Local webhooks | [Stripe](#stripe) |
| S8 | Optional: create a Supabase project and copy its two pooler strings | A hosted database | [Supabase](#supabase-optional-hosted-database) |
| S9 | Create a PostHog project; copy the project token; enable "Discard client IP data" | `NEXT_PUBLIC_POSTHOG_KEY` | [PostHog](#posthog) |
| S10 | Create the Sentry project(s); copy the DSNs; enable "Prevent storing of IP addresses" | `SENTRY_DSN`, `NEXT_PUBLIC_SENTRY_DSN` | [Sentry](#sentry) |
| S11 | Set `LLM_MODE=disabled`, or create a provider key with a spending limit | `LLM_*` | [LLM provider](#llm-provider) |

### Needed to deploy

| ID | Step | Guide |
|---|---|---|
| D1 | Create the Vercel project from the Git repository | [Set up Vercel](deployment.md#set-up-vercel) |
| D2 | Set the Framework Preset to Services; keep system environment variables exposed | [Set up Vercel](deployment.md#set-up-vercel) |
| D3 | Set the function region to your database's region | [Set up Vercel](deployment.md#set-up-vercel) |
| D4 | Add the environment variables for Preview and Production | [Variables](deployment.md#variables-which-service-reads-what) |
| D5 | Decide Deployment Protection; create the bypass secret for webhooks | [Set up Vercel](deployment.md#set-up-vercel) |
| D6 | Choose the Vercel plan (Hobby is non-commercial) | [Set up Vercel](deployment.md#set-up-vercel) |
| D7 | Add the platform rate-limit rule | [Set up Vercel](deployment.md#set-up-vercel) |
| D8 | Create the GitHub Environments and their `MIGRATION_DATABASE_URL` secret | [Set up GitHub](deployment.md#set-up-github) |
| D9 | Create the branch ruleset with the required checks | [Set up GitHub](deployment.md#set-up-github) |
| D10 | Turn on push protection and private vulnerability reporting | [Set up GitHub](deployment.md#set-up-github) |
| D11 | Decide on CodeQL (a license for private repositories) | [Set up GitHub](deployment.md#set-up-github) |
| D12 | Create the Clerk and Stripe webhook endpoints of each deployed environment | [Preview deployments](deployment.md#preview-deployments) |
| D13 | Set up the Clerk production instance (domain, DNS, certificates, OAuth, Paths, webhook, live keys) | [Clerk production instance](deployment.md#clerk-production-instance) |
| D14 | Repeat the Stripe setup in live mode | [Stripe live mode](deployment.md#stripe-live-mode) |
| D15 | Create the PostHog and Sentry production projects | [PostHog and Sentry production](deployment.md#posthog-and-sentry-production) |
| D16 | Put your Clerk user ID in `DIAGNOSTICS_OPERATOR_IDS` | [Verify the first preview](deployment.md#verify-the-first-preview) |
| D17 | Make the adopter decisions (contacts, data policy, backups, performance targets) | [Before you launch](deployment.md#before-you-launch) |

### Authorizations only the owner can give

| ID | Step | Guide |
|---|---|---|
| A1 | Push to a remote repository, or change its settings | [Authorizations](deployment.md#authorizations) |
| A2 | The first preview deployment, with sandbox credentials | [Authorizations](deployment.md#authorizations) |
| A3 | Commands that change a provider account or a hosted database (`pnpm setup:stripe`, `--send-test-events`, `pnpm test:stripe-sandbox`, `--allow-remote`, the Database migrate workflow) | [Authorizations](deployment.md#authorizations) |
| A4 | A production release | [Authorizations](deployment.md#authorizations) |
| A5 | Live verification in production | [Authorizations](deployment.md#authorizations) |

An agent working in this repository must not do an A step on its own (AGENTS.md rule 1). In Parts 1 to 3, a command that needs one says so.

---

## Part 1: Local, no accounts

You get: dependencies installed, a migrated local database, a passing test suite, an account-free production build, and a running API. The web app itself needs the Clerk keys of Part 2: [why](#what-the-placeholder-clerk-keys-do).

### Prerequisites

**[YOU L1]** Install:

| Tool | Version | Where it is pinned | Check |
|---|---|---|---|
| Node.js | 24 LTS recommended; 22.12 or newer works | `.nvmrc` (`24`), `engines.node` in `package.json` (`>=22.12`) | `node --version` |
| pnpm | 9.15.0 | `packageManager` in `package.json` | `pnpm --version` |
| [uv](https://docs.astral.sh/uv/) | 0.11 or newer (CI uses 0.11.1) | `.github/workflows/ci.yml` | `uv --version` |
| Python | 3.12 (`>=3.12,<3.13`); uv installs it | `requires-python` in `apps/api/pyproject.toml`, `apps/api/.python-version` | After `pnpm bootstrap`: `cd apps/api && uv run python --version` |
| Docker | Recent; runs the local PostgreSQL 17 container | `docker-compose.yml` | `docker compose version` |
| git | Any recent version | | `git --version` |

- Get pnpm with `corepack enable` on Node 24 and earlier. Node 25 and later no longer distribute Corepack ([Node.js 25.0.0 release notes](https://nodejs.org/en/blog/release/v25.0.0)): run `npm install -g corepack` first, or install pnpm directly with `npm install -g pnpm@9.15.0`.
- Optional, installed when you reach the step that needs them: the Stripe CLI (S7), Playwright's Chromium for the end-to-end tests (`pnpm --filter web exec playwright install chromium`), and `gitleaks` for the pre-commit hook (the hook falls back to a pinned container image when Docker is running).
- macOS and Linux. On Windows use WSL, or run `pnpm dev:api` and `pnpm dev:web` in two terminals.

### Steps

1. **Get the code.**

   ```bash
   git clone <repository-url> your-product && cd your-product
   ```

   Success: the folder holds `package.json`, `apps/`, `docs/`.

2. **Rename the template.** Run this once, before your own changes:

   ```bash
   pnpm init-template --name "Your Product" --slug your-product \
     --security-contact security@your-product.example \
     --conduct-contact conduct@your-product.example
   ```

   Add `--db-port <port>` to move the local PostgreSQL port (to run several projects side by side). Add `--dry-run` to list what would change.

   Success: it prints a JSON report with `"envCreated": true`. It renamed the product, filled in `SECURITY.md` and `CODE_OF_CONDUCT.md`, named the Docker Compose project after the slug, created `.env` from `.env.example` (no credential is copied), and removed the template's own records (`proofs/`, `docs-notes/`, the template's evidence files). The specification and the ADRs stay as inherited reference. Pass `--keep-template-material` to keep the removed records.

   Otherwise: the error names the option and its rule (a name of up to 60 characters; a lowercase slug; both contacts while the placeholders remain). Re-running with the same values changes nothing; re-running with different values is refused. To try the template without renaming it, run `cp .env.example .env` instead of this step.

3. **Install dependencies.**

   ```bash
   pnpm bootstrap
   ```

   Success: it ends without an error, and `apps/api/.venv` exists. It installs the JavaScript packages from the lockfile (`pnpm install --frozen-lockfile`) and the Python packages with `uv sync --frozen`. Otherwise: a version or lockfile error means a prerequisite is older than required; do not edit the lockfiles.

4. **Start the database and apply the migrations.**

   ```bash
   pnpm db:up
   pnpm db:migrate
   ```

   Success: `pnpm db:up` returns once the container is healthy. `pnpm db:migrate` prints `{"applied": [...]}` with the versions it applied; a second run prints `{"applied": []}`. The database listens on the port in `docker-compose.yml`, which is also in `DATABASE_URL`.

   Otherwise: if Docker is not running, start it. If the port is taken, move it with `--db-port` in step 2, or edit the port in `docker-compose.yml` and in `DATABASE_URL`.

   Optional: `pnpm db:seed --yes` loads deterministic synthetic rows. They are fixtures, not Clerk accounts: signing in creates your own user row, so the seeded users and organizations never appear in the app. `pnpm db:reset --yes` drops the schema `app` and the migration ledger, re-applies the migrations and seeds; objects a migration creates outside `app` survive it. Both refuse unless `APP_ENV` is `local` or `test` and the database is local; `pnpm db:reset` deletes local data, so ask first if it is not yours.

5. **Optional: scan commits for secrets.** `pnpm hooks:install` points git at `scripts/git-hooks`. Its pre-commit hook runs gitleaks on the staged changes with the CI rules (`.gitleaks.toml`), using `gitleaks` on your PATH, else a pinned container image when Docker is running, else it refuses the commit and says so. Undo with `git config --unset core.hooksPath`.

6. **Check the setup.**

   ```bash
   pnpm check:setup
   ```

   Success: exit status 0. It validates the configuration, then prints a result per integration, an evidence block, and "Manual steps still needed". With the shipped values the database passes and everything else is skipped. It names Clerk as not configured: a warning here, a stop for `pnpm dev`. Nothing is sent to a provider in `mock` or `disabled` mode.

   Otherwise: it lists each problem by variable name, never its value. Edit `.env` and run it again. The API does not reload `.env`: restart `pnpm dev` after every edit.

7. **Run the checks.**

   ```bash
   pnpm verify:quick
   ```

   Success: it ends with a table in which no step failed (lint, format, types, API tests against the database, web tests). Otherwise: its preflight prints the fix command for a missing tool, dependency or database (`pnpm db:up`).

8. **Run the API.**

   ```bash
   pnpm dev:api
   ```

   In another terminal: `curl -s http://127.0.0.1:8000/api/v1/health` answers `{"status":"ok"}`, and `curl -s http://127.0.0.1:8000/api/v1/ready` answers `{"status":"ready","checks":{"database":"ok"}}`. The OpenAPI document is at `/api/v1/openapi.json`. Stop it with Ctrl-C.

### What the placeholder Clerk keys do

`.env.example` ships a Clerk key pair that is well formed and fake: the publishable key names the host `clerk.not-configured-yet.invalid`, a reserved domain nobody can register, so it can never match a real instance, and the secret key is `sk_test_clerk_not_configured_yet`. With it:

- The builds, the tests, the API and `pnpm check:setup` work with no account. The API reports "Clerk: mock, Not configured".
- `pnpm dev` stops before it starts anything: "Clerk is not configured ... The web app cannot load a page without a Clerk instance". Reason: with a development publishable key, Clerk's middleware sends every page request to Clerk's Frontend API first, and the placeholder's host cannot be reached. So no page of the web app, and no sign-in, is available until you paste real keys (Part 2, Clerk).
- Production refuses it: both apps reject a `pk_test_` key when `APP_ENV=production`.

### You are done with Part 1 when

`pnpm check:setup` exits 0 and `pnpm verify:quick` ends without a failed step. For everything CI runs (add the account-free end-to-end tests in a browser, the production build and the audits), install Chromium once and run `pnpm verify`; it takes several minutes. `pnpm verify --help` lists its options.

---

## Part 2: Sandbox accounts and keys

Use test or development accounts only. Do the providers you want; Clerk is the one `pnpm dev` needs. After each provider, run `pnpm check:setup` and read its row. Commands marked **(A3)** change a provider account or a hosted database: run them yourself, or give the owner's go-ahead to an agent.

### Clerk

Sign-in, sessions and organizations. Use a **development** instance for everything in this part.

1. **[YOU S1]** Create an account and an application at [dashboard.clerk.com](https://dashboard.clerk.com). Work in its Development instance.
2. **[YOU S2]** Enable Organizations with **Membership optional**. In the Clerk Dashboard open the **Organization settings** page, select **Enable Organizations**, choose **Membership optional**, then **Enable**. If Organizations is already enabled with Membership required, open **Organization settings** and select **Membership optional**. Keep the creator role **Admin** (`org:admin`): the API maps it to admin and every other role to member.

   Why this is not optional: Clerk's default for applications created since 2025-08-22 is **Membership required**. Every session then stays `pending` until the user picks an organization, the API answers `401`, and the template's personal workspace (and personal Pro plan) never appears; the sandbox end-to-end run stops at its first step. Membership optional turns on Personal Accounts ([Clerk: configure Organizations](https://clerk.com/docs/guides/organizations/configure)). Applications created before 2025-08-22 do not show the Membership required setting.

   If you start `pnpm dev` first, Clerk may prompt you in the app to enable Organizations ([Clerk changelog](https://clerk.com/changelog/2025-11-24-enable-organizations-prompt)). Make sure its personal-accounts toggle is on. The command-line route, after `clerk auth login`, is `npx clerk@latest enable orgs` without `--force-selection` (`--force-selection` means membership required); NOT VERIFIED here.
3. **[YOU S3]** In the Dashboard open **API keys** and copy the two keys into `.env`:

   ```
   NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_test_...
   CLERK_SECRET_KEY=sk_test_...
   ```

   Do not use Clerk's keyless mode: the API verifies tokens against the issuer in the publishable key and reads memberships with the secret key, so both keys are required.
4. Start the apps.

   ```bash
   pnpm dev
   ```

   Success: the API on `:8000` and the web app on `:3000`, with the line "dev: the API is up. Web: http://localhost:3000". Open http://localhost:3000, sign up, then open `/dashboard`: the heading is "Personal workspace" with an empty project list. `/billing` shows the Free plan. `/integrations` shows each service's mode.

   Otherwise: the preflight lists the problem by variable name and nothing starts. A sign-in loop or `401` after signing in: open the app at the origin in `APP_BASE_URL`; see [Troubleshooting](#troubleshooting).
5. Check: `pnpm check:setup` shows Clerk with the secret key accepted ("no session token was verified"). With the app running, select **Run all server checks** on `/integrations`: with a session, the Clerk check shows the session token verified. With `APP_ENV=local` and no operators listed, any signed-in user may use the page.

The membership webhook (instant removal of a member's access) needs a public URL, so it belongs to a deployed environment ([D12](deployment.md#preview-deployments)). Locally, memberships are re-verified every 300 s.

### Stripe

Billing with Free and Pro. Use a Stripe **sandbox** (test mode).

1. **[YOU S4]** Create a Stripe account and a sandbox. Put the sandbox secret key from Stripe's API keys page in `.env` as `STRIPE_SECRET_KEY=sk_test_...`. It must be an unrestricted `sk_test_` key for the next command. `APP_ENV` must be `local` (the default in `.env`).
2. **(A3)** Create the Pro product and its two prices:

   ```bash
   pnpm setup:stripe --currency usd --monthly 900 --yearly 9000
   ```

   Amounts are in minor units (900 is 9.00 in USD): pick your own; all three flags are required. Success: it prints `Product prod_pro: created` or `already exists`, one line per price (`Price pro_monthly: price_... (created)`), then the lines to set. It is safe to re-run: it finds the prices by lookup key and creates only what is missing. It stops with nothing created if a price it finds differs in amount, currency, interval or state (Stripe prices cannot be edited), or if the two prices belong to different products. It never writes `.env` and never prints your key.
3. Paste the printed lines into `.env`: `BILLING_MODE=sandbox` and the three IDs, `STRIPE_PRICE_PRO_MONTHLY=price_...`, `STRIPE_PRICE_PRO_YEARLY=price_...` and `STRIPE_PRODUCT_PRO=prod_...`. The product is the one both prices belong to: any monthly or yearly price of it grants Pro. If you made the prices in the Dashboard instead, use the ID of their product.
4. **[YOU S5]** Save the Customer Portal in the Stripe Dashboard, in test mode: **Settings > Billing > Customer portal**. Turn on: updating payment methods, viewing invoices, cancellation at the end of the billing period, and switching between exactly the two Pro prices. The template reads the portal and never changes it; `pnpm setup:stripe` and the `/integrations` Stripe check list what is missing. Then open **Settings > Payment methods** and turn off the bank debits and bank transfers (SEPA, ACH, BECS, Bacs and the like): Pro follows a subscription's status, and a bank debit reads `active` before the funds arrive ([limitations](limitations.md#known-limits-by-area)). Checkout already offers cards only; which methods the portal's payment-method update offers is NOT VERIFIED, so do not leave a delayed method on.
5. **[YOU S6]** In the Dashboard open **Billing > Revenue recovery > Retries** and set what happens when all retries fail to cancel the subscription or mark it unpaid. Leaving it past due would keep Pro indefinitely (DEC-010).
6. **[YOU S7]** Install the [Stripe CLI](https://docs.stripe.com/stripe-cli). `pnpm dev` then runs `stripe listen --forward-to http://127.0.0.1:8000/api/v1/webhooks/stripe` for you, and gives the API the signing secret for that run only (it is never written to `.env`), when `BILLING_MODE=sandbox`, the key is an `sk_test_` key and the CLI is installed. You need no `stripe login`. Without the CLI, `pnpm dev` prints one line and goes on without a listener: a test checkout stays Free until `pnpm billing:reconcile`.
7. Commands that load the full API configuration without `pnpm dev` (`pnpm billing:reconcile`, `cd apps/api && uv run python -m app.cli job <name>`) read `STRIPE_WEBHOOK_SECRET` from `.env`, and refuse to start without a `whsec_...` value once `BILLING_MODE` is `sandbox`. Set it once: run `stripe login`, then `stripe listen --print-secret`, and put the `whsec_...` it prints in `.env`.
8. Restart `pnpm dev`. Check: `pnpm check:setup` shows the Stripe row Passed for the two prices, their product, reading subscriptions and the portal. Open `/billing`, upgrade with a [Stripe test card](https://docs.stripe.com/testing), and confirm the plan becomes Pro within a few seconds. Cancel at period end in the portal. If the plan stays Free, see [Troubleshooting](#troubleshooting).
9. **(A3)** Automated end to end: `pnpm test:stripe-sandbox` runs the lifecycle against your sandbox (customers, test clocks, subscriptions are created and deleted). It needs the unrestricted key, the two prices and the product. `pnpm verify:sandbox` runs it as a part.

A restricted `rk_test_` key runs the app but not `setup:stripe` or the sandbox suite; its permissions are in [integrations.md](integrations.md#stripe).

### Supabase (optional hosted database)

Use it when you want a hosted sandbox or preview database. The local Docker database needs no account. Connection details and failure modes: [integrations.md](integrations.md#supabase-and-postgresql).

1. **[YOU S8]** In Supabase create a **non-production** project. Choose a region near where the API will run, and use the same region for the preview and production projects (a project's region cannot be changed later). In the project, click **Connect** at the top of the page and copy two strings, replacing the password placeholder with the database password, percent-encoded ([Supabase: connecting to Postgres](https://supabase.com/docs/guides/database/connecting-to-postgres)):
   - **Session pooler** (port `5432`, user `postgres.<project-ref>`): for migrations. It works from IPv4-only networks. The direct connection is IPv6-only unless the project has the IPv4 add-on.
   - **Transaction pooler** (port `6543`): for the running API.
2. **(A3)** Apply the migrations. This also creates the `app_login` role, which cannot log in yet:

   ```bash
   MIGRATION_DATABASE_URL='<session pooler string>' pnpm db:migrate --allow-remote
   ```

   Success: `{"applied": [...]}`. Otherwise: "cannot connect" from an IPv4 network means you used the direct string.
3. **(A3)** Give `app_login` a password:

   ```bash
   MIGRATION_DATABASE_URL='<session pooler string>' pnpm db:api-login --allow-remote
   ```

   Type a new password of 16 or more characters twice (or set `API_DB_PASSWORD` for one non-interactive run; never put it in `.env`). Success: `{"login_role": "app_login", "login": true}`, and a line saying where `DATABASE_URL` goes. Re-running rotates the password.
4. Set `DATABASE_URL` in `.env` to the Transaction pooler string with the user `app_login.<project-ref>` and that password. Never use the `postgres` URL there: outside local development the API refuses a login role that could skip row-level security.
5. Check: `pnpm check:setup` shows PostgreSQL Passed.

Apply migrations only with `pnpm db:migrate`. Never run `supabase db push` or `supabase db reset`, and do not connect Supabase Branching to this repository: they keep a second migration ledger and bypass the runner's checks.

### PostHog

Product analytics, optional.

1. **[YOU S9]** Create a **separate** PostHog project for non-production data. Copy its project token (`phc_...`, in Project settings) into `NEXT_PUBLIC_POSTHOG_KEY`; never use a personal (`phx_`) or secret (`phs_`) key. Set `NEXT_PUBLIC_POSTHOG_HOST` to `https://us.i.posthog.com` or `https://eu.i.posthog.com`, and `ANALYTICS_MODE=sandbox`. In the project settings enable **Discard client IP data** ([data-inventory.md](data-inventory.md#external-services)).
2. Restart `pnpm dev` (the web values are fixed when the web app starts or builds).
3. **(A3)** Check: `pnpm check:setup --send-test-events` sends one `integration_test_sent` event. Look it up in PostHog.

### Sentry

Error monitoring, optional.

1. **[YOU S10]** Create two Sentry projects (the API, and the web app), or one with separate environments. Copy the DSNs into `SENTRY_DSN` (API) and `NEXT_PUBLIC_SENTRY_DSN` (web), and set `ERROR_REPORTING_MODE=sandbox`. In both projects enable **Prevent storing of IP addresses** ([data-inventory.md](data-inventory.md#external-services)).
2. Restart `pnpm dev`.
3. **(A3)** Check: `pnpm check:setup --send-test-events` sends one synthetic `IntegrationTestError`; the Sentry row shows the event ID.

Source maps upload only from a build with Sentry's build variables, which is a Vercel setting ([deployment.md](deployment.md#variables-which-service-reads-what)).

### LLM provider

A connectivity check only, optional.

1. **[YOU S11]** Choose. With no LLM, set `LLM_MODE=disabled`: it needs no key, is valid in every environment, and the check answers "not configured". Otherwise create an API key at the provider and **set a spending limit there**, then set `LLM_PROVIDER` (`anthropic` or `openai`), `LLM_API_KEY`, `LLM_MODEL` (a model ID your key can use), and `LLM_MODE=sandbox`.
2. Check: `pnpm check:setup` retrieves the model's metadata and generates no tokens.

### You are done with Part 2 when

```bash
pnpm verify:sandbox
```

ends with no `FAIL` and no `BLOCKED` and shows `PASS` for each part you configured. It runs three parts, each only when its keys are there: `pnpm check:setup`, `pnpm test:stripe-sandbox`, and the signed-in browser tests (`E2E_SANDBOX=1 pnpm test:e2e`, which create a Clerk test user `e2e+clerk_test@example.com` in your development instance and need a local `DATABASE_URL` and Chromium). A part without keys is `SKIPPED` and stays NOT VERIFIED. It ends with an evidence record: paste it into a new file under `dev-docs/evidence/` (spec §20). If sign-up restrictions in your Clerk instance block `+clerk_test` addresses, relax them (the Restrictions setting; label not re-checked). Also open `/integrations` and select **Run all server checks**; each configured service must be Passed.

---

## Part 3: First preview deployment

Summary; every step, setting and check is in [deployment.md](deployment.md). The shipped topology is one Vercel project with Vercel Services (web and API together), which has **not** been verified on the platform: the first preview is where that is checked.

1. Put the repository on a Git host and push it (A1).
2. Create the Vercel project and its settings: root directory, Framework Preset **Services**, function region, plan (D1 to D3, D6).
3. Create the preview database and run the **Database migrate** workflow for `preview`, then `pnpm db:api-login` (D8, A3; [Hosted database and migrations](deployment.md#hosted-database-and-migrations)).
4. Add the Preview variables, including `APP_BASE_URL` and `DATABASE_URL` with the `app_login` role (D4; [the map](deployment.md#variables-which-service-reads-what)). Add the Clerk and Stripe webhook endpoints (D12), and your user ID in `DIAGNOSTICS_OPERATOR_IDS` (D16).
5. Authorize the first preview deployment (A2).
6. Run the checks in [Verify the first preview](deployment.md#verify-the-first-preview). The first takes two minutes: `curl -sI https://<preview-host>/dashboard` signed out. A `307` (to `/sign-in`, or to Clerk's handshake URL on a development instance) means the Next.js proxy ran inside the service. A `200` means only the page gate in the browser protects pages; the API authorizes every operation either way. Whether the proxy runs under Services is **NOT VERIFIED** until you do this.

You are done with Part 3 when every check under [Verify the first preview](deployment.md#verify-the-first-preview) passes and you have recorded the results as `sandbox` evidence. Production is a separate sequence ([deployment.md](deployment.md#production)).

---

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `Invalid configuration: ...` when the API starts | `pnpm check:setup` lists the variables by name. Edit `.env`, restart `pnpm dev` |
| `pnpm dev` stops with "Clerk is not configured" | Paste your Clerk development keys (S3) |
| `pnpm build` fails on the Clerk publishable key | Use `pnpm build:check` (account-free), or paste your keys |
| `pnpm test` or `pnpm verify` stops with "The database tests are required here", or a bare `uv run pytest` skips them ("PostgreSQL is not available") | `pnpm db:up`; or set `TEST_DATABASE_URL` to a **local** server. `pnpm verify --no-db` is the explicit exception |
| `/api/v1/*` returns 500 in the browser | The API is not running or stopped at start. Look for `Invalid configuration` in the `pnpm dev` output; run `pnpm check:setup`; fix `.env`; restart `pnpm dev` (the API does not reload `.env`) |
| Every sign-in asks you to create an organization, or there is no "Personal workspace" | Membership required: set **Membership optional** (S2) |
| Signed in, but a loop between `/sign-in` and a page, or `401` from the API | Open the app at the origin in `APP_BASE_URL` (`localhost` is not `127.0.0.1`), or add the origin to `CLERK_AUTHORIZED_PARTIES` and restart or redeploy. The API logs `clerk_azp_refused` with the origin |
| Organization pages return `404` | You are not a member, or `CLERK_SECRET_KEY` belongs to another Clerk instance than the publishable key. A `503` "Organization directory is unavailable" means Clerk is unreachable or rejected the key |
| Upgrade succeeded but the plan is still Free | Webhooks are not reaching the API: install the Stripe CLI (S7), or fix the deployed endpoint (payload format **Snapshot**). The billing page re-reads once; `pnpm billing:reconcile` repairs the rest |
| "Integrations page: diagnostics are not available" | Add your Clerk user ID to `DIAGNOSTICS_OPERATOR_IDS`, or use `APP_ENV=local`. In production also set `DIAGNOSTICS_ENABLED=true` |
| A check is "Skipped" | That integration is in `mock` or `disabled` mode: set it to `sandbox` in `.env` |
| `pnpm verify:sandbox` or `E2E_SANDBOX=1 pnpm test:e2e` stops "pending ... choose-organization" | Membership required: set **Membership optional** (S2) |
| `corepack: command not found` | Node 25 and later: `npm install -g corepack`, or `npm install -g pnpm@9.15.0` |
| The migration cannot connect to Supabase | The direct host is IPv6-only: use the Session pooler string |
| `Migration ... was modified after being applied` | A local database that applied an older first migration: `pnpm db:reset --yes` (deletes local data; ask first if it is not yours) |
