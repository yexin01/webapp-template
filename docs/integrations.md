# External Integrations

How to configure each service for **local development** and **preview** with test or sandbox accounts. Use separate projects or instances per environment, and never reuse production credentials outside production (spec §5).

Each integration has a mode in `.env`:

- `mock`: no network.
- `sandbox`: provider test mode or a non-production project.
- `live`: production only.

Results obtained with `mock` never prove connectivity (spec TEST-04).

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
   - Locally, Clerk needs a public URL (a tunnel) to reach your machine. Without webhooks, memberships are re-verified with Clerk once they are older than `MEMBERSHIP_MAX_STALENESS_SECONDS` (default 300 s; owner confirmation pending, DEC-009).
6. Optional: set `CLERK_JWT_KEY` to the instance's PEM public key for networkless token verification. Otherwise keys are fetched from the instance JWKS and cached.

Sign-in and sign-up pages live at `/sign-in` and `/sign-up`.

## Stripe

1. Use a **sandbox** or test mode account.
2. Create a **Pro** product with two recurring prices: monthly and yearly. Put the price IDs in `STRIPE_PRICE_PRO_MONTHLY` and `STRIPE_PRICE_PRO_YEARLY`. Only these two prices are purchasable, and subscriptions to any other price never grant Pro.
3. Put the test secret key in `STRIPE_SECRET_KEY` (`sk_test_…`, or a restricted `rk_test_…` key). A restricted key needs write access to Customers, Checkout Sessions, and Customer Portal sessions, and read access to Subscriptions.
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
6. Set `BILLING_MODE=sandbox`.

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
  4. Tables live in the `app` schema, which is **not** exposed through Supabase's Data API. The browser never connects to the database (DEC-008). Do not grant the `anon` or `authenticated` roles access to `app`.
- The migration creates two `NOLOGIN` roles, `app_api` and `app_worker`, and grants them to the connecting role. Every API transaction switches into one of them with `SET LOCAL ROLE`.

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
  - API: `ANALYTICS_ALLOWLIST` in `apps/api/app/infrastructure/observability.py`

Consent requirements and retention are pending owner decision (DEC-004).

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
3. **Sentry / PostHog**: trigger a test error and a tracked event, then confirm in the dashboards that no emails, tokens, or query strings were captured.
4. **LLM**: run the check from the dashboard and confirm `ok: true`.
