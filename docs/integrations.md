# External Integrations

Reference for the six external services: what each does here and where its code lives, which variables configure it, how the template checks it, and what goes wrong. The steps that need a person (accounts, keys, dashboard settings) are in [setup.md](setup.md). What to set in Vercel is in [deployment.md](deployment.md). Every variable is described in [`.env.example`](../.env.example).

Use separate projects or instances per environment, and never reuse production credentials outside production (spec §5).

## Modes

Each integration has a mode in `.env`:

- `mock`: no network.
- `sandbox`: provider test mode or a non-production project.
- `live`: production only.
- `disabled`: off. No provider is called.

| Variable | local, test | preview | production |
|---|---|---|---|
| `BILLING_MODE` | `mock`, `sandbox` | `mock`, `sandbox` | `live` |
| `LLM_MODE` | `disabled`, `mock`, `sandbox` | `disabled`, `mock`, `sandbox` | `disabled`, `live` |
| `ANALYTICS_MODE`, `ERROR_REPORTING_MODE` | `disabled`, `sandbox` | `disabled`, `sandbox` | `disabled`, `live` |

Preview and production must set all four explicitly. Clerk has no mode: it is always on outside tests, and the placeholder keys from `.env.example` mean "not configured". Startup validation also pairs keys with modes: live keys outside production, or test keys in production, prevent the API from starting. Results obtained with `mock` never prove connectivity (spec TEST-04).

## Status of each integration

Status in this repository on 2026-10-31. Nothing has been run against a real provider: no row is `sandbox` or `live`. Record your own runs under `dev-docs/evidence/` (spec §20) and update this table.

- `mocked`: fakes and loopback stubs.
- `local`: real software on one machine (PostgreSQL, the packaged function build).
- `NOT RUN`: a test exists that needs your keys; it was not run.
- `NOT VERIFIED`: no test can run without the platform or provider.

| Provider | What is tested | Evidence now | Command that upgrades it |
|---|---|---|---|
| Clerk | Token verification (issuer, `azp`, `act`, key rotation), the membership mirror and its webhooks, and the page gate and proxy, against fakes and local stubs (`apps/api/tests/unit/test_clerk_verifier.py`, `test_clerk_directory.py`, `test_identity.py`; `apps/web/src/proxy.test.ts`, `src/ui/page-gate.test.tsx`). Signed-in browser flows: `apps/web/e2e/authenticated.spec.ts` | `mocked`. Browser flows `NOT RUN` | `pnpm verify:sandbox` (its `e2e` part; needs Clerk development keys) |
| Stripe | Adapter, webhook signature and payload handling, synchronization, checkout rules, portal policy and `setup:stripe` against an in-memory provider and loopback stubs (`test_stripe_adapter.py`, `test_stripe_setup.py`, `test_billing.py`). Sandbox lifecycle: `apps/api/tests/sandbox/test_stripe_lifecycle.py` | `mocked`. Sandbox suite `NOT RUN` | `pnpm test:stripe-sandbox` (or `pnpm verify:sandbox`; needs an unrestricted `sk_test_` key, the two prices and their product) |
| PostgreSQL | Row-level security, roles, migrations, the login-role guard and the restore drill on PostgreSQL 17 | `local` | Already run on every `pnpm test` |
| Supabase | Migrations and the login role on Supabase's PostgreSQL image (`pnpm test:supabase`, needs Docker). A hosted project (pooler user routing `app_login.<project-ref>`, TLS, hosted extensions) | image: `local`. Hosted: `NOT VERIFIED` | `pnpm db:migrate --allow-remote`, `pnpm db:api-login --allow-remote`, then `pnpm check:setup` against the hosted project |
| PostHog | Event allowlist, property scrubbing, pending-event buffer, flush after the response, wire contents (`test_posthog_analytics.py`, `test_telemetry_wire.py`, `apps/web/src/lib/analytics*.test.*`). Real delivery | `mocked`. Delivery `NOT RUN` | `pnpm check:setup --send-test-events` |
| Sentry | Scrubbing, tags, throttle, flush, trace sampling, wire contents (`test_sentry_tracing.py`, `test_telemetry_wire.py`, `apps/web/src/sentry-*.test.ts`). Real delivery and source-map upload | `mocked`. Delivery and upload `NOT RUN` | `pnpm check:setup --send-test-events`; a build with the three Sentry build variables |
| LLM provider | Adapter and limits against loopback stubs. Real model-metadata call | `mocked`. Call `NOT RUN` | `pnpm check:setup` (read only) |
| Vercel | Packaging and routing of the root `vercel.json` with the pinned CLI, no deployment (`pnpm test:vercel-build`) | `local`. The platform: `NOT VERIFIED` | A preview deployment you authorize ([deployment.md](deployment.md#verify-the-first-preview)) |

## Checking an integration

### `pnpm check:setup`

The preflight of `pnpm dev`, then the checks behind the Integrations page, run from the command line without signing in. It contacts only the providers in `sandbox` mode (the Clerk membership read, the Stripe prices and portal read, the LLM model metadata), and nothing in `mock` or `disabled` mode. It prints a result per integration, an evidence block (spec §20), and the manual steps still needed. It refuses `APP_ENV=production`. Exit status 1 means a check failed.

- `--send-test-events` also sends one PostHog event and one Sentry issue to the sandbox projects. Run it only with the owner's go-ahead.
- Clerk: with no session, the check only shows that the secret key was accepted ("no session token was verified"). A publishable key and a secret key from different instances are not detected.
- Stripe: the webhook endpoint is not checked, because listing endpoints needs a restricted-key permission the setup does not require.

### The Integrations page

After filling in `.env` and running `pnpm dev`, sign in and open `/integrations`. Each server check uses the keys in `.env`:

| Service | What the check does | Side effects |
|---|---|---|
| PostgreSQL | Confirms queries run as `app_api` with row-level security and the tenant context applied, and reads the login role. In preview and production a login role that could skip row-level security FAILS the check and names the fix | None |
| Clerk | Reads your organization memberships from the Clerk Backend API (your session token was already verified) | None |
| Stripe | Retrieves both Pro prices and checks they are active, recurring monthly and yearly, in one currency, in the right mode, and on the product `STRIPE_PRODUCT_PRO` (it names the product of a price that is on another one). Reads the Customer Portal configuration and fails (`portal_mismatch`) when it does not match [what the template expects](#stripe) | None |
| LLM provider | Retrieves the model's metadata (no tokens generated) | None; counts toward the LLM check limits |
| PostHog | Sends the allowlisted event `integration_test_sent` and flushes it | One event in your project |
| Sentry | Sends a synthetic `IntegrationTestError` and flushes it | One issue in your project |

The browser checks show the Clerk session state and send a PostHog event and a Sentry error from the browser SDKs. Outcomes:

- **Passed**: the provider confirmed the request.
- **Sent (unconfirmed)**: the SDK sent it, but the provider gives no confirmation. Look it up in the dashboard; Sentry checks show the event ID.
- **Failed**: shows the reason and an error code.
- **Skipped**: the integration is `mock` or `disabled`.

Diagnostics are an operator tool:

- Set `DIAGNOSTICS_OPERATOR_IDS` to your Clerk user IDs. With `APP_ENV=local` and no operators listed, any signed-in user may use them. Everyone else gets "not available".
- They are off in production unless `DIAGNOSTICS_ENABLED=true`.
- Checks are rate-limited (`DIAGNOSTICS_RUNS_PER_USER_PER_MINUTE`, `DIAGNOSTICS_RUNS_GLOBAL_PER_DAY`).
- The header link, the home-page button and the dashboard LLM card show only for a user the API lets use diagnostics. The web app asks `GET /api/v1/diagnostics/access`, which answers any signed-in caller with `{"available": true|false}`, writes no audit entry, and keeps a customer's browser console clean.

### `pnpm verify:sandbox`

The one command after you paste sandbox keys. It runs three parts, each only when its keys are there, skips the others and says why, and ends with an evidence record to keep under `dev-docs/evidence/`.

| Part | Runs | When |
|---|---|---|
| `setup` | `pnpm check:setup` | A `.env` exists and Clerk development keys, or a `*_MODE=sandbox`, are set |
| `stripe` | `pnpm test:stripe-sandbox` | `STRIPE_SECRET_KEY` is an unrestricted `sk_test_` key, both `STRIPE_PRICE_PRO_*` and `STRIPE_PRODUCT_PRO` are set, and the test database answers. `STRIPE_SANDBOX_TESTS=0` leaves it out |
| `e2e` | `E2E_SANDBOX=1 pnpm test:e2e` | Clerk development keys, a local `DATABASE_URL` that answers (the run writes test rows), and Chromium installed |

With nothing configured every part is skipped, the exit status is 0, and the record says NOT VERIFIED. A part that failed, or could not run, exits 1. `--skip <part>` leaves a part out.

Checks that no automated test covers, to do by hand and record:

1. **Clerk organizations.** Sign up, create an organization, invite a second test user, then remove them. The removed user gets `404` on that organization's API routes: immediately with the membership webhook, within `MEMBERSHIP_MAX_STALENESS_SECONDS` without it.
2. **Stripe.** A 3-D Secure test card (abandon a Checkout session, then check out again), and a declining card through all retries (needs the Retries setting). The Dashboard cannot attach a test clock to a Checkout customer.
3. **Sentry and PostHog.** Confirm in the dashboards that the events arrived, and that no emails, tokens or query strings were captured.

---

## Clerk

Sign-in, sessions and organizations. API: `apps/api/app/modules/identity` (token verification, the membership mirror, the webhook). Web: `apps/web/src/proxy.ts` and `src/ui/page-gate.tsx` (page access), `src/lib/api/client.ts` (bearer tokens). Steps: [setup.md](setup.md#clerk).

| Variable | Apps | Notes |
|---|---|---|
| `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` (API alias: `CLERK_PUBLISHABLE_KEY`) | both | `pk_test_...` or `pk_live_...`. The API derives the token issuer from it, so both apps need the key |
| `CLERK_SECRET_KEY` | both | The web proxy and the API's membership reads. The web build on Vercel requires it |
| `CLERK_AUTHORIZED_PARTIES` | both | Origins allowed in a token's `azp`, as a browser sends them. Empty: the origin of `APP_BASE_URL`, which must be listed when this is set. On a preview deployment the deployment's own URL and branch URL are added; production never adds any. The web proxy applies the same list when the web app is built |
| `CLERK_JWT_KEY` | both | PEM public key (Dashboard > API keys > Show JWT public key) for networkless verification, in the API and in the web proxy. Empty: the API takes keys from `<issuer>/.well-known/jwks.json` and caches them for up to an hour (a token with an unknown key ID refreshes the set, at most once every 5 s per instance), and the proxy fetches Clerk's key set through its SDK. The web app fixes it when it is built: build again after changing it |
| `CLERK_WEBHOOK_SIGNING_SECRET` | api | `whsec_...` of this environment's endpoint. Without it, memberships are re-verified when older than `MEMBERSHIP_MAX_STALENESS_SECONDS` (300 s, the approved bound; lower is allowed, higher is rejected: DEC-009, ADR-0002) |
| `CLERK_ALLOW_IMPERSONATION` | api | Default `false`: a session someone else started as a user (dashboard impersonation, actor tokens, agent tasks; an `act` claim) gets `403`. When `true`, the audit entry names the actor (`app.audit_log.impersonator_clerk_id`) and the log line `actor_session` records it |

The webhook endpoint is `<api-host>/api/v1/webhooks/clerk`. Events: `organizationMembership.created`, `organizationMembership.updated`, `organizationMembership.deleted`, `organization.deleted`, `user.deleted`. Locally, Clerk needs a public URL to reach your machine, so skip the webhook there; the 300 s bound and the daily `reconcile-memberships` job cover it. The production instance (domain, DNS, Paths, OAuth credentials, webhook): [deployment.md](deployment.md#clerk-production-instance).

**Sessions.** A session with status `pending` (the `sts` claim: a required step, such as choosing an organization, is unfinished) is treated as signed out (`401`), as Clerk's SDKs do by default. This is why Clerk's default for applications created since 2025-08-22, **Membership required**, breaks the template: every session stays pending until the user picks an organization, and the personal workspace never appears. Setting Membership optional is step S2 in [setup.md](setup.md#clerk). With Membership optional, choosing an organization is never a required step. Sources: [Clerk: configure Organizations](https://clerk.com/docs/guides/organizations/configure) and [session tasks](https://clerk.com/docs/guides/configure/session-tasks).

**Organization requests.** Access is decided by the membership mirror, never by organization claims in the session token (TEN-07). When the mirror has no membership for the requested organization:

- If it is the session's active organization (`o.id` in Clerk's v2 session token, `org_id` in v1), the API re-reads the user's memberships from Clerk at once. An organization the user created or switched to a moment ago with `<OrganizationSwitcher>` therefore works on the first request. The claim only triggers the re-read: access still requires Clerk to list the membership.
- Otherwise the miss is answered from the mirror (`404`) if the user's memberships were read successfully in the last 30 s. Beyond that, at most one such miss per user per 5-s window, across instances, calls Clerk; concurrent misses in the same window get `404`. A failed read (a Clerk outage, `503`) does not start the 30-s period. An organization that is not the session's active one (for example, opened from a link right after the user joined it) can therefore return `404` for up to 30 s, or until its membership webhook arrives.
- A session token can keep naming an organization the user was removed from a moment ago until Clerk refreshes the token (session tokens last 60 s by default). Each such request is one Clerk call, and is still denied.

Membership read times and the staleness bound use each API instance's own clock. Keep instance clocks synchronized (managed platforms do this).

Sign-in and sign-up pages live at `/sign-in` and `/sign-up`. Every page except `/`, `/sign-in` and `/sign-up` requires sign-in, in two layers that read one list (`apps/web/src/lib/page-access.ts`): the proxy on the server and a gate in the root layout in the browser. Both are presentation; the API authorizes every operation.

**Failure modes**

| Symptom | Cause | Fix |
|---|---|---|
| Every user is asked to create an organization; no "Personal workspace"; `401` on every call after sign-in | Membership required | S2: Membership optional |
| The sandbox end-to-end run stops with "pending with the task choose-organization" | Same | Same |
| A loop between `/sign-in` and a page, or `401` after sign-in | The page is opened at an origin that `APP_BASE_URL` and `CLERK_AUTHORIZED_PARTIES` do not name (`127.0.0.1` instead of `localhost`, a custom domain added later) | Open the app at the `APP_BASE_URL` origin, or list the origin and build again. The API logs `clerk_azp_refused` with the `azp` to add |
| Organization pages return `404` | You are not a member, or `CLERK_SECRET_KEY` belongs to another Clerk instance than the publishable key | Check both keys come from the same instance |
| `503` "Organization directory is unavailable" | Clerk is unreachable or rejected the key | Check Clerk's status and `CLERK_SECRET_KEY` |
| `403` for an impersonated session | `CLERK_ALLOW_IMPERSONATION` is `false` | Set it to `true` where the API runs, deploy, and run `pnpm db:migrate` (the audit column) |
| The webhook endpoint answers `503 NOT_CONFIGURED` | `CLERK_WEBHOOK_SIGNING_SECRET` is unset (reported to Sentry at warning level) | Set it, or remove the endpoint |
| `CLERK_SECRET_KEY is malformed or still the .env.example placeholder` | The shipped placeholder, or a key that is not `sk_test_` / `sk_live_` | Paste your keys (S3) |

## Stripe

Billing: Free and Pro (monthly and yearly). Subscriptions belong to individuals: a user's Pro plan covers their own actions in their personal workspace and in every organization they belong to, is never shared with other members, and organization roles neither grant nor remove it ([ADR-0002](../dev-docs/adr/0002-organization-entitlements-contacts-adopter-decisions.md)). API: `apps/api/app/modules/billing` (`adapters/stripe.py`, `adapters/stripe_setup.py`). Web: `apps/web/src/features/billing`. Steps: [setup.md](setup.md#stripe).

| Variable | Apps | Notes |
|---|---|---|
| `BILLING_MODE` | api | `mock`, `sandbox`, `live` |
| `STRIPE_SECRET_KEY` | api | `sk_test_...` or a restricted `rk_test_...`; `sk_live_` / `rk_live_` in production |
| `STRIPE_WEBHOOK_SECRET` | api | `whsec_...` of the endpoint of this environment |
| `STRIPE_PRICE_PRO_MONTHLY`, `STRIPE_PRICE_PRO_YEARLY` | api | `price_...`. Only these two prices are purchasable |
| `STRIPE_PRODUCT_PRO` | api | `prod_...`. The product both prices belong to. Any monthly or yearly price of it grants Pro |
| `STRIPE_CUSTOM_DOMAIN` | both | Your Stripe custom domain, live mode only. The API refuses any Checkout or portal URL whose host is not `stripe.com`, a subdomain of it, or this domain |
| `BILLING_RECONCILE_APPLY` | api | Unset (default): the scheduled `reconcile-billing` job applies Stripe's state when `BILLING_MODE` is `sandbox` or `live`, and only reports in `mock`. `true` or `false` forces either ([ADR-0004 §20](../dev-docs/adr/0004-cycle-1-adopted-decisions.md)) |

The template creates customers, Checkout sessions and portal sessions. It never creates or changes the **Customer Portal configuration**: that stays in the Dashboard (ADR-0001, DEC-010), and the template only reads it. `pnpm setup:stripe` creates the Pro product (ID `prod_pro`) and the two prices (lookup keys `pro_monthly`, `pro_yearly`) in a sandbox only: it refuses any key but `sk_test_` and any `APP_ENV` but `local` or `test`, finds what exists and creates only what is missing, stops with nothing created if an existing price differs in amount, currency, interval or state, or if the two prices belong to different products, prints the three IDs to set (`STRIPE_PRICE_PRO_MONTHLY`, `STRIPE_PRICE_PRO_YEARLY`, `STRIPE_PRODUCT_PRO`), and never writes `.env` or prints the key. Live products and prices are created in the Dashboard, and a live product has its own ID.

**Prices and the Pro product** (adopted by delegation, reversible; the alternative is to list replaced prices in settings: [ADR-0004 §16](../dev-docs/adr/0004-cycle-1-adopted-decisions.md)). Pro is granted by the product. A subscription has the plan `pro_monthly` or `pro_yearly` when its price is one of the two configured prices, or any other price of `STRIPE_PRODUCT_PRO` that bills every month or every year (an interval count of 1). Any other price has no plan and grants no Pro. Checkout sells only the two configured prices. Stripe prices cannot be edited, and subscriptions on an archived price stay active ([Stripe: manage prices](https://docs.stripe.com/products-prices/manage-prices)). To change a price: create the new price on the same product, set `STRIPE_PRICE_PRO_MONTHLY` (or `STRIPE_PRICE_PRO_YEARLY`) to it, deploy, and update the Customer Portal so that it offers only the two current prices. Subscribers on the old price keep Pro. Keep other plans, add-ons and free prices on other products ([Stripe: how products and prices work](https://docs.stripe.com/products-prices/how-products-and-prices-work)): every monthly or yearly price of the Pro product grants Pro, whatever it charges. A paid subscription (`active` or `past_due`) on a price with no plan is logged as `billing_unknown_price` at error level ([operations.md](operations.md#reading-a-reconciliation-report)). If a price was replaced before `STRIPE_PRODUCT_PRO` was set, `pnpm billing:reconcile --apply` gives Pro back to the subscribers it dropped.

**What the Customer Portal must allow** (the `/integrations` check and `pnpm setup:stripe` report what is missing), following ADR-0001 and DEC-010:

- Update payment methods and view invoices.
- Cancellation **at the end of the billing period**.
- Switching between exactly the two Pro prices. Proration is configured here.

**Checkout rule** (DEC-013 option B, adopted by delegation of the owner and reversible; the alternatives are option A, the same refusal without storing the open session, and option C, keeping detection and manual refunds; [ADR-0004](../dev-docs/adr/0004-cycle-1-adopted-decisions.md)). Checkout is refused while the user has a subscription that is not over: an entitling one (`already_pro`), an unpaid, paused or past-due one on an unknown price (`payment_required`), a trialing or active one on an unknown price (`subscription_exists`), or an incomplete one (`payment_pending`). The billing page then shows the reason and **Manage billing** (the Customer Portal) instead of Upgrade. Checkout offers cards only ([limitations](limitations.md#billing)). The open Checkout Session is stored per user (`app.billing_checkout_sessions`) and expired at Stripe before another is created. If Stripe cannot expire it, no second session is created and the request fails with `503`; the retry closes it.

**How billing state flows**

- Checkout and portal sessions are created by the API.
- Pro is granted only from Stripe's data, read by the API: after a signature-verified webhook, by reconciliation, or by a re-read the signed-in user's own request triggers (`POST /api/v1/billing/sync`: the success page asks once after 3 s, the billing page after returning from the portal, and after a refused checkout). Duplicate, delayed and out-of-order events converge on the same state.
- The redirect alone never grants Pro.
- If a webhook was missed, run `pnpm billing:reconcile` (dry run), then `pnpm billing:reconcile --apply` ([operations.md](operations.md#reading-a-reconciliation-report)).

**Webhooks.** Endpoint: `<api-host>/api/v1/webhooks/stripe`. Events (14): `checkout.session.completed`, `checkout.session.async_payment_succeeded`, `checkout.session.async_payment_failed`, `checkout.session.expired`, `customer.subscription.created`, `customer.subscription.updated`, `customer.subscription.deleted`, `customer.subscription.paused`, `customer.subscription.resumed`, `customer.subscription.pending_update_applied`, `customer.subscription.pending_update_expired`, `invoice.paid`, `invoice.payment_failed`, `invoice.payment_action_required`. Create the event destination in Workbench > Webhooks ([Stripe: webhooks](https://docs.stripe.com/webhooks)) and:

- Choose the payload format **Snapshot**, not Thin, although Stripe labels Thin as recommended. This API answers a correctly signed Thin notification with `400 INVALID_REQUEST` (logged as `stripe_webhook_unsupported_payload`).
- Keep the account's default API version: the handler reads only the event ID, type, creation time and the object's customer.
- Use that destination's own signing secret in `STRIPE_WEBHOOK_SECRET` of that environment.
- If a Thin destination was ever created: create a Snapshot one, put its secret in `STRIPE_WEBHOOK_SECRET`, delete the Thin one (the format cannot be changed), then run `pnpm billing:reconcile` and `pnpm billing:reconcile --apply`.
- Locally, `pnpm dev` runs `stripe listen --forward-to http://127.0.0.1:8000/api/v1/webhooks/stripe` for you ([setup.md](setup.md#stripe)).

**Restricted keys.** A restricted key `rk_test_` runs the app but not `pnpm setup:stripe` or `pnpm test:stripe-sandbox`. It needs write access to Customers, Checkout Sessions and Customer Portal sessions, and read access to Subscriptions and Prices. Reading the portal configuration (the portal check) needs the Dashboard permission for the Customer Portal; the exact permission name is not confirmed here, and the check reports "Customer Portal: not checked" instead of failing when the key lacks it.

**Custom domain** (live mode, optional). Set `STRIPE_CUSTOM_DOMAIN` in the API and web environment and deploy before the domain goes live in Stripe, which switches to it once its DNS verifies.

**Failure modes**

| Symptom | Cause | Fix |
|---|---|---|
| Upgrade succeeded but the plan is still Free | Webhooks do not reach the API: no Stripe CLI locally, a wrong secret, a Thin payload, or Deployment Protection blocking a preview | Check the destination's Event deliveries in Workbench. The billing page re-reads once; `pnpm billing:reconcile` repairs the rest |
| Checkout or portal answers `503 NOT_CONFIGURED` | Stripe rejected the key, or a session URL had an unexpected host (Sentry: an error tagged `error_code=NOT_CONFIGURED`; logs: `stripe_configuration_error`, `stripe_session_host_unexpected`) | Check `STRIPE_SECRET_KEY`, the mode, and `STRIPE_CUSTOM_DOMAIN` |
| The Stripe check fails with `portal_mismatch` | The default portal configuration lacks something above | Fix it in the Dashboard (step S5 in [setup.md](setup.md#stripe)) |
| Checkout answers `409` | The user has a subscription that is not over | Use Manage billing |
| `STRIPE_PRODUCT_PRO is required and must be a Stripe product ID` | `BILLING_MODE` is not `mock` and the product is unset or not a `prod_` ID | Paste the `STRIPE_PRODUCT_PRO` line that `pnpm setup:stripe` prints, or the ID of the product both prices belong to |
| The Stripe check fails with `price_mismatch`: "Monthly price belongs to product `prod_X`, but STRIPE_PRODUCT_PRO is `prod_Y`" | A configured price is on another product than `STRIPE_PRODUCT_PRO` | If `prod_X` is your Pro product, set `STRIPE_PRODUCT_PRO=prod_X`. Otherwise configure a price of the Pro product |
| A user pays and stays Free; the log has `billing_unknown_price` | The price is neither configured nor a monthly or yearly price of the Pro product (for example, it is on another product, or bills every three months) | Fix the product or the price in Stripe, then run `pnpm billing:reconcile --apply` |
| `STRIPE_WEBHOOK_SECRET is required and must start with whsec_` | `BILLING_MODE` is not `mock` and the secret is unset | Locally: `stripe listen --print-secret`, or install the Stripe CLI so `pnpm dev` supplies it |

Test cards and clocks: [Stripe: testing](https://docs.stripe.com/testing).

## Supabase and PostgreSQL

All data lives in PostgreSQL schema `app` with forced row-level security. API: `apps/api/app/kernel/infrastructure` (`db.py`, `migrations.py`, `config.py`). Migrations: `supabase/migrations/`. Steps for a hosted project: [setup.md](setup.md#supabase-optional-hosted-database). Local: `pnpm db:up` runs PostgreSQL 17 in Docker on `127.0.0.1:54329`; `.env.example` already points there, and no Supabase account is required.

| Variable | Apps | Notes |
|---|---|---|
| `DATABASE_URL` | api | The runtime connection. In preview and production it must be the `app_login` role |
| `MIGRATION_DATABASE_URL` | cli | The schema owner, for `pnpm db:migrate`, `db:seed`, `db:reset`, `db:api-login`. Empty: `DATABASE_URL`. Never on Vercel |
| `API_DB_PASSWORD` | cli | One-off password for `pnpm db:api-login`, in the shell. Not a setting: never in `.env` |
| `DATABASE_POOL_MAX_SIZE`, `DATABASE_WORKER_POOL_MAX_SIZE`, `DATABASE_CONNECT_TIMEOUT_SECONDS`, `DATABASE_STATEMENT_TIMEOUT_MS` | api | Tuning: [operations.md](operations.md#connection-pools) |
| `PGSSLMODE`, `PGSERVICE` and the other libpq variables | api, cli | Respected, and take precedence over the TLS default |

**Roles.** The migrations create `app_api` and `app_worker` (`NOLOGIN`) and `app_login`. Every request transaction switches into `app_api` or `app_worker` transaction-locally. The API connects as `app_login` (adopted by delegation of the owner and reversible; the alternative is to connect as the migration role, as before, which the check below now refuses in preview and production), which is `NOLOGIN` until `pnpm db:api-login` gives it a password: no superuser, no `BYPASSRLS`, no `CREATEROLE`, owns nothing, member of `app_api` and `app_worker` only. In preview and production the first transaction of a process checks `session_user`: if it is a superuser, has `BYPASSRLS` or `CREATEROLE`, or owns a table in schema `app`, every database transaction fails with a `503` that names the fix, `/ready` reports not ready, and `/health` stays up. Local and test may use a superuser (the shipped `.env.example` does). A refused process keeps refusing until it restarts: redeploy after fixing `DATABASE_URL`.

**What this does not stop.** `app_login` can still `SET ROLE app_worker`, and the worker's policies are `USING (true)` on users, organizations, memberships, billing, audit and the project select. Code that can run arbitrary SQL (an injection, raw database access an adopter adds) can therefore still read across tenants. Row-level security is a second line behind the application's checks, not a defense against SQL injection. Closing the rest needs a second login role that is a member of `app_worker` only, with its own URL and pool; that is not built.

**Connection strings for a hosted Supabase project** ([Supabase: connecting to Postgres](https://supabase.com/docs/guides/database/connecting-to-postgres)):

- **Runtime**: the Transaction pooler (port `6543`, user `app_login.<project-ref>`), in `DATABASE_URL`. The driver disables prepared statements, which transaction mode does not support.
- **Migrations**: the Session pooler (port `5432`, user `postgres.<project-ref>`), in `MIGRATION_DATABASE_URL`. The shared pooler is IPv4-only on every plan. The direct connection (`db.<project-ref>.supabase.co`) is IPv6-only unless the project has the IPv4 add-on; Vercel, GitHub-hosted runners and many home networks are IPv4-only. The migration runner uses only transaction-scoped locks and no prepared statements, so any pooler mode works for it.
- **TLS**: a non-local host defaults to `sslmode=require`: no plaintext fallback, but the certificate is not checked, so it does not stop an active man-in-the-middle. For that, download the Supabase CA (Database Settings > SSL Configuration), ship it with the API, and set `sslmode=verify-full&sslrootcert=<path>` in both URLs. That is not set up here.
- Do not copy driver-only suffixes (`?pgbouncer=true`, `?workaround=supabase-pooler.vercel`, `supa=...`, `connection_limit`, `pool_timeout`): libpq rejects them with `invalid URI query parameter`, so the API refuses the URL at startup. Its message names both likely causes, an unencoded character in the password and a query parameter libpq does not know, and cannot say which: libpq's reason quotes the URL, password included.
- `app` is not exposed through Supabase's Data API, and the browser never connects to the database (DEC-008). Do not grant `anon` or `authenticated` access to `app`. The migration ledger `public.app_schema_migrations` has row-level security with no policies, and the runner revokes every grant on it from `anon`, `authenticated`, `service_role` and `PUBLIC`. Optional, your choice: turn off the Data API; "Enforce SSL on incoming connections" in Database Settings.

**Migrations.** `supabase/` is a folder of migrations for this repository's runner, whose ledger is `public.app_schema_migrations`. Apply them only with `pnpm db:migrate`. Never run `supabase db push` or `supabase db reset`, and do not connect Supabase Branching to this repository: they keep a second ledger (`supabase_migrations.schema_migrations`) and re-apply every file, bypassing the checksums, lock timeout and ledger. Migrations are applied in version order; a new migration numbered before one already applied is refused. Concurrent runs are safe. Procedures: [operations.md](operations.md#database).

**Failure modes**

| Symptom | Cause | Fix |
|---|---|---|
| `pnpm db:migrate --allow-remote` cannot connect | The direct host is IPv6-only and your network is IPv4 | Use the Session pooler string |
| `DATABASE_URL is not a valid PostgreSQL connection string` (`invalid URI query parameter` from `psql`) | A driver-only suffix in the URL, or a special character in the password that is not percent-encoded | Remove the suffix; percent-encode the password |
| Every database request answers `503` naming `app_login` | `DATABASE_URL` is a login role that could skip row-level security | `pnpm db:api-login`, set `DATABASE_URL`, redeploy |
| `permission denied for schema app` | Connected as a role outside the runtime roles | Use `app_login` |
| `Migration ... was modified after being applied` | A local database that applied the old first migration | `pnpm db:reset --yes` (deletes local data: ask first) |
| Password rejected after a restore | A dump holds no role passwords | Run `pnpm db:api-login` again |

## PostHog

Product analytics. Browser: `apps/web/src/lib/analytics.ts`, the only file allowed to import `posthog-js`. API: `ANALYTICS_ALLOWLIST` in `apps/api/app/kernel/infrastructure/observability.py`, plus each module's `analytics_events`. Steps: [setup.md](setup.md#posthog).

| Variable | Apps | Notes |
|---|---|---|
| `ANALYTICS_MODE` | both | `disabled`, `sandbox`, `live` |
| `NEXT_PUBLIC_POSTHOG_KEY` (API alias: `POSTHOG_PROJECT_API_KEY`) | both | The project token `phc_...`. A personal, secret or OAuth key (`phx_`, `phs_`, `phr_`) is refused: the value ships in every browser bundle. PostHog's guide names it `NEXT_PUBLIC_POSTHOG_PROJECT_TOKEN`; this template reads the other name |
| `NEXT_PUBLIC_POSTHOG_HOST` (API alias: `POSTHOG_HOST`) | both | `https://us.i.posthog.com` (default) or `https://eu.i.posthog.com` |

Privacy defaults:

- Autocapture and session recording are off. Text and attributes are masked, and query strings are stripped.
- Users are identified by internal UUID only.
- Only allowlisted events are sent, and the allowlist is enforced where the event leaves: in the browser by `before_send` (an event not in `ANALYTICS_EVENTS`, and any property that is not allowlisted or the SDK's own `$` property, is dropped), and in the API by the container's registry. Importing `posthog-js` elsewhere is banned by `apps/web/eslint.guards.mjs`, which `eslint.config.mjs` does not load yet (an owner-applied step, spec §23 step 4).
- Consent and retention are adopter decisions (DEC-004): [data-inventory.md](data-inventory.md#adopter-decision-checklist). Enable "Discard client IP data" in the project.

**Ad blockers.** Blockers drop browser events, which biases the data. The supported fix is PostHog's managed reverse proxy, free on PostHog Cloud: in PostHog, open the organization proxy settings and create a new managed proxy with a non-obvious subdomain, then add the CNAME record it shows ([PostHog: managed reverse proxy](https://posthog.com/docs/advanced/proxy/managed-reverse-proxy)). Set `NEXT_PUBLIC_POSTHOG_HOST=https://<your-subdomain>` and `POSTHOG_HOST` to the regional ingestion host, so the API keeps sending directly (the proxy carries events through Cloudflare, a PostHog subprocessor). Do not use PostHog's same-origin rewrite recipe (`/ingest`): a rewrite forwards every cookie to PostHog, including Clerk's `__session`, which the API accepts as a bearer token.

**Failure modes**

| Symptom | Cause | Fix |
|---|---|---|
| `NEXT_PUBLIC_POSTHOG_KEY is required when ANALYTICS_MODE is enabled` | Mode on, no key | Set the token, or `ANALYTICS_MODE=disabled` |
| `NEXT_PUBLIC_POSTHOG_KEY holds a secret PostHog credential` | A `phx_` / `phs_` / `phr_` key | Use the project token `phc_...` |
| Events never arrive from the browser | A blocker, or a key baked into an older build | Use the managed proxy; build again after changing the key |

## Sentry

Error monitoring. Browser: `apps/web/src/instrumentation-client.ts`, `lib/error-reporting.ts`. Web server: `sentry.server.config.ts`. API: `observability.py`. Steps: [setup.md](setup.md#sentry).

| Variable | Apps | Notes |
|---|---|---|
| `ERROR_REPORTING_MODE` | both | `disabled`, `sandbox`, `live` |
| `SENTRY_DSN` | api | DSN of the API project |
| `NEXT_PUBLIC_SENTRY_DSN` | web | DSN of the web project. Sentry's Vercel integration sets this name, and not `SENTRY_DSN` |
| `SENTRY_TRACES_SAMPLE_RATE` | both | `0` to `1`. The browser records no spans and sends no sampling decision; the API samples each request a page makes at this rate |
| `SENTRY_RELEASE` (alias: `VERCEL_GIT_COMMIT_SHA`) | api | Release tag of API events |
| `SENTRY_AUTH_TOKEN`, `SENTRY_ORG`, `SENTRY_PROJECT` | web, build only | Source-map upload |

Both SDKs collect no user info, cookies, headers, request bodies, query parameters or local variables. A `beforeSend` scrubber redacts secret-shaped values as a second line of defense; free text in an exception or log message is only pattern-redacted, so keep personal data out of messages. API events carry the tags `request_id`, `user_id` and `org_id` (internal UUIDs, never Clerk IDs), and an expected 5xx (a dependency outage, a missing configuration) is reported with the tag `error_code`, at most once a minute per cause per instance. The Sentry `user` field stays removed.

**Source maps** upload only when `SENTRY_AUTH_TOKEN` is in the build environment of the web project, set in Vercel (a CI build is never deployed, so its maps would not match). With the token set, `next build` fails unless `SENTRY_PROJECT` is set, and `SENTRY_ORG` too unless the token is an organization token (`sntrys_...`). Sentry's Vercel integration sets `SENTRY_ORG`, `SENTRY_PROJECT`, `SENTRY_AUTH_TOKEN` and `NEXT_PUBLIC_SENTRY_DSN` ([Sentry: Vercel integration](https://docs.sentry.io/organization/integrations/deployment/vercel/)). Under one Vercel project the API function also receives `SENTRY_AUTH_TOKEN` ([deployment.md](deployment.md#variables-which-service-reads-what)).

**No relay.** Browser events go straight to Sentry. Do not add the optional `tunnelRoute` that Sentry's wizard offers: it is a rewrite that forwards all headers, cookies included. `apps/web/src/next-config.test.ts` fails if `next.config.ts` sets it.

Enable "Prevent storing of IP addresses" in both projects ([data-inventory.md](data-inventory.md#external-services)).

**Failure modes**

| Symptom | Cause | Fix |
|---|---|---|
| `next build` fails naming `SENTRY_PROJECT` or `SENTRY_ORG` | A token without them | Set them, or remove the token |
| No source maps in Sentry | No `SENTRY_AUTH_TOKEN` in the Vercel build environment | Set the three variables there |
| An error event is missing | The instance was frozen before the flush finished (`telemetry_flush_incomplete` in the logs) | See [operations.md](operations.md#incident-response) |

## LLM provider

A connectivity check only (`POST /api/v1/llm/check`): API `apps/api/app/modules/llm`, web `apps/web/src/features/llm`. The provider is not prescribed (DEC-006). Steps: [setup.md](setup.md#llm-provider).

| Variable | Apps | Notes |
|---|---|---|
| `LLM_MODE` | api | `disabled` is valid in every environment and needs no key or model. `mock` and `sandbox` are refused in production |
| `LLM_PROVIDER` | api | `anthropic` or `openai` |
| `LLM_API_KEY` | api | Set a spending limit at the provider |
| `LLM_MODEL` | api | A model ID your key can access |
| `LLM_TIMEOUT_SECONDS`, `LLM_CHECKS_PER_USER_PER_MINUTE`, `LLM_CHECKS_GLOBAL_PER_DAY`, `LLM_MAX_CONCURRENT_CHECKS` | api | Limits, in [`.env.example`](../.env.example) |

The check retrieves the model's metadata and generates no tokens. With `LLM_MODE=disabled` it answers `200 {"ok": false, "provider": "none", "error_code": "not_configured"}` without touching a rate limiter, and the web card says the check is turned off.

## Callback URL summary

| Provider | URL | Environment |
|---|---|---|
| Clerk webhook | `<api-host>/api/v1/webhooks/clerk` | Per environment; its own signing secret |
| Stripe webhook | `<api-host>/api/v1/webhooks/stripe` | Per environment; its own signing secret |
| Stripe Checkout success | `<APP_BASE_URL>/billing/success?session_id={CHECKOUT_SESSION_ID}` | Built by the API |
| Stripe Checkout cancel | `<APP_BASE_URL>/billing?checkout=canceled` | Built by the API |
| Stripe portal return | `<APP_BASE_URL>/billing?from=portal` | Built by the API |
| Clerk allowed origins | `CLERK_AUTHORIZED_PARTIES` | Per environment |

`<api-host>` is the app's own domain under the shipped single-project topology. Preview URLs, Deployment Protection and the Clerk Paths row: [deployment.md](deployment.md#preview-deployments).
