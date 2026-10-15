# External Integrations

How to configure each service for **local development** and **preview** with test or sandbox accounts. Use separate projects or instances per environment, and never reuse production credentials outside production (spec §5).

Each integration has a mode in `.env`:

- `mock`: no network.
- `sandbox`: provider test mode or a non-production project.
- `live`: production only.

Results obtained with `mock` never prove connectivity (spec TEST-04).

## Checking your setup: the Integrations page

After filling in `.env` and running `pnpm dev`, sign in and open `/integrations`.

**Server checks.** Each server check uses the keys in `.env`:

| Service | What the check does | Side effects |
|---|---|---|
| PostgreSQL | Confirms queries run as `app_api` with row-level security and the tenant context applied | None |
| Clerk | Reads your organization memberships from the Clerk Backend API (your session token was already verified) | None |
| Stripe | Retrieves both Pro prices and checks they are active, recurring monthly and yearly, in one currency, and in the right mode | None |
| LLM provider | Retrieves the model's metadata (no tokens generated) | None; counts toward the LLM check limits |
| PostHog | Sends the allowlisted event `integration_test_sent` and flushes it | One event in your project |
| Sentry | Sends a synthetic `IntegrationTestError` and flushes it | One issue in your project |

**Browser checks.** These show the Clerk session state and send a PostHog event and a Sentry error from the browser SDKs.

**Outcomes:**

- **Passed**: the provider confirmed the request.
- **Sent (unconfirmed)**: the SDK sent it, but the provider gives no confirmation. Look it up in the dashboard; Sentry checks show the event ID.
- **Failed**: shows the reason and an error code.
- **Skipped**: the integration is `mock` or `disabled`.

**Access.** Diagnostics are an operator tool:

- Set `DIAGNOSTICS_OPERATOR_IDS` to your Clerk user ID or IDs.
- With `APP_ENV=local` and no operators listed, any signed-in user may use them.
- Everyone else gets "not available".
- Checks are rate-limited (`DIAGNOSTICS_RUNS_PER_USER_PER_MINUTE`, `DIAGNOSTICS_RUNS_GLOBAL_PER_DAY`).
- They are off in production unless `DIAGNOSTICS_ENABLED=true`.

| Service | Mode variable | Needed for |
|---|---|---|
| Clerk | (always on outside tests) | Sign-in, organizations |
| Stripe | `BILLING_MODE` | Checkout, portal, webhooks |
| Supabase / PostgreSQL | `DATABASE_URL` | All data |
| PostHog | `ANALYTICS_MODE` | Product analytics |
| Sentry | `ERROR_REPORTING_MODE` | Error monitoring |
| LLM provider | `LLM_MODE` | Connectivity check |

---

## Clerk

1. Create an application in the Clerk dashboard. Use its **Development** instance for local work.
2. Enable **Organizations**. The API maps the `org:admin` role to admin, and every other role to member.
3. Copy the publishable key (`pk_test_…`) to `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` and the secret key (`sk_test_…`) to `CLERK_SECRET_KEY`.
4. Set `CLERK_AUTHORIZED_PARTIES` to every origin that serves the web app, for example `http://localhost:3000`. Tokens from other origins are rejected.
5. Optional, but recommended: **membership webhooks**, which revoke access immediately when a member is removed.
   - Endpoint: `https://<your-api-host>/api/v1/webhooks/clerk`.
   - Events: `organizationMembership.created`, `organizationMembership.updated`, `organizationMembership.deleted`, `organization.deleted`, `user.deleted`.
   - Copy the signing secret (`whsec_…`) to `CLERK_WEBHOOK_SIGNING_SECRET`.
   - Locally, Clerk needs a public URL (a tunnel) to reach your machine. Without webhooks, memberships are re-verified with Clerk once they are older than `MEMBERSHIP_MAX_STALENESS_SECONDS`: 300 s, the bound approved for TEN-07 (DEC-009, ADR-0002). It can be set lower, not higher.
6. Optional: set `CLERK_JWT_KEY` to the instance's PEM public key for networkless token verification. Otherwise keys are fetched from the instance JWKS (`<issuer>/.well-known/jwks.json`) and cached for up to an hour. A token with an unknown key ID refreshes the cached set, at most once every 5 s per instance.

**Sessions.** A session with status `pending` (the `sts` claim: a required step, such as choosing an organization, is unfinished) is treated as signed out (`401`), as Clerk's SDKs do by default.

**Organization requests.** Access is decided by the membership mirror, never by organization claims in the session token (TEN-07). When the mirror has no membership for the requested organization:

- If it is the session's active organization (`o.id` in Clerk's v2 session token, `org_id` in v1), the API re-reads the user's memberships from Clerk at once. So an organization the user has just created or switched to with `<OrganizationSwitcher>` works on the first request. The claim only triggers the re-read: access still requires Clerk to list the membership.
- Otherwise the miss is answered from the mirror (`404`) if the user's memberships were read successfully in the last 30 s. Beyond that, at most one such miss per user per 5-s window, across instances, calls Clerk; concurrent misses in the same window get `404`. A failed read (a Clerk outage, `503`) does not start the 30-s period, so the next miss a few seconds later asks Clerk again. An organization that is not the session's active one (for example, opened from a link right after the user joined it) can therefore return `404` for up to 30 s, or until its membership webhook arrives.
- A session token can keep naming an organization the user was just removed from until Clerk refreshes the token (session tokens last 60 s by default). Each such request is one Clerk call, and is still denied.

Membership read times and the staleness bound use each API instance's own clock. Clock skew between instances shifts the bound by the skew, so keep instance clocks synchronized (managed platforms do this).

Sign-in and sign-up pages live at `/sign-in` and `/sign-up`.

## Stripe

1. Use a **sandbox** or test mode account.
2. Create a **Pro** product with two recurring prices: monthly and yearly. Put the price IDs in `STRIPE_PRICE_PRO_MONTHLY` and `STRIPE_PRICE_PRO_YEARLY`. Only these two prices are purchasable, and subscriptions to any other price never grant Pro. Stripe prices cannot be edited, so changing a price means a new price ID: before you replace one of these, add the old ID to `STRIPE_PRICE_PRO_MONTHLY_RETAINED` or `STRIPE_PRICE_PRO_YEARLY_RETAINED` (comma-separated), or existing subscribers on it lose Pro at their next synchronization.
3. Put the test secret key in `STRIPE_SECRET_KEY` (`sk_test_…`, or a restricted `rk_test_…` key). A restricted key needs write access to Customers, Checkout Sessions, and Customer Portal sessions, and read access to Subscriptions and Prices (Prices is used by the Integrations page check).
4. Configure the **Customer Portal** (Settings → Billing → Customer portal), following ADR-0001 / DEC-010:
   - Allow updating payment methods and viewing invoices.
   - Allow cancellation **at the end of the billing period**.
   - Allow switching between the monthly and yearly Pro prices. Proration behavior is configured here.
5. Webhooks:
   - **Local**: install the Stripe CLI and run
     `stripe listen --forward-to localhost:8000/api/v1/webhooks/stripe`.
     Put the printed `whsec_…` in `STRIPE_WEBHOOK_SECRET`.
   - **Deployed**: add an endpoint `https://<your-api-host>/api/v1/webhooks/stripe` with the events below, and use that endpoint's own signing secret in that environment.
   - Events: `checkout.session.completed`, `checkout.session.async_payment_succeeded`, `checkout.session.async_payment_failed`, `checkout.session.expired`, `customer.subscription.created`, `customer.subscription.updated`, `customer.subscription.deleted`, `customer.subscription.paused`, `customer.subscription.resumed`, `customer.subscription.pending_update_applied`, `customer.subscription.pending_update_expired`, `invoice.paid`, `invoice.payment_failed`, `invoice.payment_action_required`.
6. In Stripe's subscription settings, set what happens **after all payment retries fail** to cancel the subscription or mark it unpaid. Under DEC-010, `past_due` keeps Pro while Stripe retries, so "leave the subscription past due" would keep Pro indefinitely.
7. Set `BILLING_MODE=sandbox`.

**How billing state flows.**

- Checkout and portal sessions are created by the API.
- Pro access is granted only after a signature-verified webhook triggers the API to **re-read the customer's subscriptions from Stripe**. Duplicate, delayed, and out-of-order events therefore converge on the same state.
- The success redirect alone never grants Pro.
- If a webhook was missed, run `pnpm billing:reconcile` (dry run) and then `pnpm billing:reconcile --apply`.

**Useful sandbox tests:** use Stripe test cards for successful, declined, and authentication-required payments. Use **test clocks** to advance through renewals and failed renewals.

## Supabase / PostgreSQL

- **Local**: `pnpm db:up` runs PostgreSQL 17 in Docker on `127.0.0.1:54329`; `.env.example` already points there. No Supabase account is required.
- **Hosted (non-production project)**:
  1. Create a Supabase project for development or preview.
  2. Use the **pooled** connection string (transaction mode) in `DATABASE_URL` for the API. The driver disables prepared statements for pooler compatibility.
  3. Apply migrations with a direct (non-pooled) connection:
     `DATABASE_URL=<direct-connection-string> pnpm db:migrate --allow-remote`.
     Only run this against a database you are authorized to change.
  4. Tables live in the `app` schema, which is **not** exposed through Supabase's Data API. The browser never connects to the database (DEC-008). Do not grant the `anon` or `authenticated` roles access to `app`. The migration ledger, `public.app_schema_migrations`, has row-level security with no policies, and the migration runner revokes every grant on it from `anon`, `authenticated`, and `PUBLIC`.
  5. Migrations are applied in version order. A new migration numbered before one already applied is refused: give it a later version. Concurrent runs are safe; each migration is applied once.
- The migration creates two `NOLOGIN` roles, `app_api` and `app_worker`, and grants them to the connecting role. Every API transaction switches into one of them transaction-locally, setting the role, statement timeout, and tenant context in a single `set_config` statement.

## PostHog

1. Create a **separate project** for non-production data.
2. Set `NEXT_PUBLIC_POSTHOG_KEY` (project API key, `phc_…`) and `NEXT_PUBLIC_POSTHOG_HOST` (US or EU ingestion host).
3. Set `ANALYTICS_MODE=sandbox`.

Privacy defaults:

- Autocapture is off and session recording is off.
- Text and attributes are masked, and query strings are stripped.
- Users are identified by internal UUID only.
- Only allowlisted events are sent:
  - browser: `apps/web/src/lib/analytics.ts`
  - API: `ANALYTICS_ALLOWLIST` in `apps/api/app/kernel/infrastructure/observability.py`

Consent requirements and retention are adopter decisions (DEC-004): see the checklist in [data-inventory.md](data-inventory.md#adopter-decision-checklist).

## Sentry

1. Create two projects (API and web), or one project with separate environments.
2. Set `SENTRY_DSN` (API) and `NEXT_PUBLIC_SENTRY_DSN` (web), then `ERROR_REPORTING_MODE=sandbox`.
3. Optional, CI only: `SENTRY_AUTH_TOKEN` enables source-map upload during builds.

Both SDKs are configured to collect no user info, cookies, headers, request bodies, query parameters, or local variables. A `beforeSend` scrubber redacts secret-shaped values as a second line of defense.

## LLM provider

The provider is not prescribed (DEC-006).

1. Set `LLM_PROVIDER` to `anthropic` or `openai`, `LLM_API_KEY`, and `LLM_MODEL` (a model ID your key can access). Then set `LLM_MODE=sandbox`.
2. **Set a spending limit at the provider.**

The connectivity check (`POST /api/v1/llm/check`) retrieves the model's metadata and generates no tokens. It is rate-limited per user (`LLM_CHECKS_PER_USER_PER_MINUTE`) and globally (`LLM_CHECKS_GLOBAL_PER_DAY`).

## Callback URL summary

| Provider | URL | Environment |
|---|---|---|
| Clerk webhook | `<api>/api/v1/webhooks/clerk` | Per environment; own signing secret |
| Stripe webhook | `<api>/api/v1/webhooks/stripe` | Per environment; own signing secret |
| Stripe Checkout success | `<APP_BASE_URL>/billing/success?session_id={CHECKOUT_SESSION_ID}` | Built by the API |
| Stripe Checkout cancel | `<APP_BASE_URL>/billing?checkout=canceled` | Built by the API |
| Stripe portal return | `<APP_BASE_URL>/billing` | Built by the API |
| Clerk allowed origins | `CLERK_AUTHORIZED_PARTIES` | Per environment |

## Verifying an integration (sandbox)

Record evidence in `dev-docs/evidence/` (spec §20). Do not paste keys, tokens, or personal data into evidence.

1. **Clerk**:
   - Sign up, create an organization, invite a second test user, then remove them.
   - Confirm the removed user gets `404` on that organization's API routes. With webhooks this is immediate; without them it happens within the staleness bound.
2. **Stripe**:
   - Upgrade (monthly), then cancel at period end in the portal.
   - Advance a test clock past the period end, and confirm the plan becomes Free.
   - Repeat with a declining test card to observe `past_due`, then `unpaid`.
3. **Sentry / PostHog**: use the server and browser checks on `/integrations`. Then confirm in the dashboards that the events arrived, and that no emails, tokens, or query strings were captured.
4. **LLM**: run the LLM check on `/integrations` and confirm it passes.
5. **All**: select **Run all server checks** on `/integrations`. Record each outcome, with mode `sandbox`, in your evidence file.
