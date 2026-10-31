# Data Inventory

This inventory lists every place the template stores or sends data about people, why, and for how long. It is the evidence that DEC-004 requires (spec §3.3) and reflects the code as of 2026-10-31 (commit `1729bd0`). A provider's own records are listed as far as its documentation and the code show them; the rows say where a claim is NOT VERIFIED.

> **Adopter decision (DEC-004, [ADR-0002](../dev-docs/adr/0002-organization-entitlements-contacts-adopter-decisions.md)).** The template defines no retention periods and has no account deletion or export feature. The "Retention" column therefore describes **current behavior**, not policy. Each project made from the template sets its own policy with the checklist at the end of this file. Update this file whenever code changes what is stored or sent.

## Identifiers

The template never stores email addresses, names, or payment details. Those stay with Clerk and Stripe.

| Identifier | Where it comes from | Where it is used |
|---|---|---|
| Clerk user ID (`user_…`) | Clerk | `app.users`, the write-limit counter key (`writes:user:<Clerk user ID>`), the diagnostics operator list, the `actor_session` log line |
| Internal user ID (UUID) | `app.users.id`, generated on first request | Every table, PostHog `distinct_id`, Stripe metadata, API log lines and Sentry tags (`user_id`) |
| Clerk organization ID (`org_…`) | Clerk | `app.organizations`, the counter key `identity:active-refresh:<internal user ID>:<Clerk organization ID>`, and the path of organization requests (`/api/v1/orgs/{org_id}/…`), which Vercel's request logs keep ([Vercel](#external-services)) |
| Internal organization ID (UUID) | `app.organizations.id` | `org_id` on API log lines and Sentry tags in an organization context |
| Stripe customer, subscription, price and event IDs (`cus_…`, `sub_…`, `price_…`, `evt_…`) | Stripe | `app.billing_customers`, `app.subscriptions`, `app.billing_events`; some billing log lines ([Logs](#logs-api-stdout)) |

## Application database (PostgreSQL, schema `app`)

| Table | Personal data | Purpose | Retention (current behavior) |
|---|---|---|---|
| `users` | Clerk user ID, created time, when memberships were last read from Clerk | Map identity to an internal ID | Kept indefinitely. `user.deleted` revokes memberships and keeps the row; billing sets the user's subscription to end at the period's end (below, item 2). |
| `organizations` | Clerk organization ID, deleted time | Tenant boundary | Kept; marked deleted on `organization.deleted`. |
| `organization_memberships` | User, organization, role, verification time | Authorization mirror (DEC-009) | Replaced on each re-verification; removed when Clerk reports removal or user deletion. |
| `projects` | Name (**free text a user may fill with personal data**), creator | Sample tenant data | Until the user deletes the project. |
| `billing_customers` | User → Stripe customer ID, when last reconciled | Billing link | Kept indefinitely. Payment records are never deleted (DEC-004 interim rule). When Stripe deletes a customer, the next checkout replaces the ID in this row; the subscriptions that ended under the old customer keep the old ID. |
| `subscriptions` | Status, plan, period dates, cancellation dates | Entitlements | Kept indefinitely. |
| `billing_events` | Stripe event ID, event type, customer ID, timestamps, attempt count; **no payloads** | Webhook idempotency ledger | Kept indefinitely. |
| `identity_events` | Clerk/Svix event ID, event type, timestamps; **no payloads** | Webhook idempotency ledger | Kept indefinitely. |
| `audit_log` | Actor user, organization, action, target, outcome, request ID; the Clerk user ID of the impersonating actor (`impersonator_clerk_id`), only when impersonation is allowed (`CLERK_ALLOW_IMPERSONATION=true`) | Security audit (ADM-01, ADM-03) | Kept indefinitely. |
| `billing_checkout_sessions` | User → the open Stripe Checkout Session: session ID (`cs_…`), the request's idempotency key, expiry, created time. **No URL** (it would let anyone who reads it open the user's checkout) | Refuse a second checkout while one is open or a subscription is unsettled (BILL-07, DEC-013) | One row per user. The user's next checkout replaces or deletes it; the row is removed with the user (`ON DELETE CASCADE`). A row from a completed checkout stays until that next checkout: no job deletes it. Only the API role reads or writes it. |
| `rate_limit_counters` | Keys containing the internal user ID (`llm:user:…`, `checkout:…`, `portal:…`, `billing-sync:…`, `diagnostics:user:…`, `diagnostics:denied:…`, `identity:miss-refresh:…`), or, for the write limit (`writes:user:…`), the Clerk user ID; `identity:active-refresh:<internal user ID>:<Clerk organization ID>` also names a Clerk organization; window start, hit count | Rate limits (DEC-011) | Until the daily `prune-expired` job runs (about two days). |
| `job_runs` | Job name, when its last run over HTTP started, and until when that run holds the job. **No personal data** | Run each job once at a time and spaced apart when started over `GET /api/v1/jobs/<name>` (SEC-09) | One row per job, replaced by its next run. Only the worker role reads or writes it. |
| `outbox` | Domain events: internal user ID, organization ID, resource ID, plan, context; **no names, emails, or free text** | Reliable after-commit side effects such as analytics (ADR-0003) | Processed events: `OUTBOX_RETENTION_DAYS` (default 7), deleted by `prune-expired`. Dead events: until replayed or deleted by an operator. |

Backups of this database follow the hosting plan's schedule. They are not defined yet (DEC-005).

### Credentials that reach this data

Not personal data, but whoever holds them can read it. None is stored in the database or the repository.

| Name | What it is | Where it lives |
|---|---|---|
| `DATABASE_URL` | The connection string of the `app_login` role, which owns nothing and can only switch to the two runtime roles (local development may use the superuser; preview and production refuse it) | The API's environment (the hosting project) |
| `MIGRATION_DATABASE_URL` | The schema owner's connection, for migrations, seed, reset, and `pnpm db:api-login`. The running API never reads it | A shell for a one-off command, or a GitHub Environment secret. **Never in the hosting project** |
| `API_DB_PASSWORD` | A one-off input to `pnpm db:api-login`; the server receives only a SCRAM-SHA-256 verifier. It is not a setting and not stored | The shell of that one command |

## External services

| Service | What the template sends | What the service also collects on its own | Notes |
|---|---|---|---|
| **Clerk** | Nothing beyond normal sign-in flows; the API reads a user's organization memberships. In the browser, Clerk's own components also load from `challenges.cloudflare.com` (Cloudflare Turnstile, Clerk's bot protection), `*.protect.clerk.com` (Clerk Protect, abuse and fraud protection) and `img.clerk.com` (images); the CSP allows exactly these hosts besides the Frontend API (`apps/web/src/lib/csp.ts`). | Email, name, password hash or OAuth identity, sessions, devices, IP addresses | Clerk is the identity provider. Deletion and export of this data use Clerk's dashboard or API. A session that someone else started (impersonation, an actor token) is refused unless `CLERK_ALLOW_IMPERSONATION=true`; then its actor's Clerk user ID is recorded (`audit_log`, and the `actor_session` log line). |
| **Stripe** | Internal user ID as customer metadata, `client_reference_id`, and subscription metadata | Name, email, billing address, payment method, invoices, entered in Stripe Checkout or the Customer Portal | Payment records may have legal retention obligations (DEC-004 question 6). |
| **PostHog** | Allowlisted events only, identified by internal user ID. Browser: page views with query strings stripped, `upgrade_clicked`, `paywall_viewed`, `billing_portal_clicked`, `integration_test_sent`. API: `project_created`, `plan_changed`, `checkout_started`, `billing_portal_opened`, `llm_check_run`, `integration_test_sent`. The API's list is the kernel's plus the events modules register; a module's events are listed here when it adds them. | The client IP address of browser requests, unless the project discards it | Python SDK sends `disable_geoip=True`. **Recommended:** enable "Discard client IP data" in the PostHog project. Autocapture and session recording are off. The SDK's own `$` properties describe the browser and the visit, and the template removes only the URL and referrer ones (`$current_url`, `$referrer`, `$initial_referrer`): the installed `posthog-js` (1.438.9) holds, among others, `$raw_user_agent`, `$timezone`, `$browser_language`, `$screen_height`, `$screen_width`, `$viewport_height`, `$device_id`, `$session_id` and `$window_id`. That list is read from the SDK's bundle, not captured on the wire (NOT VERIFIED). The browser enforces its allowlist when it sends: an event that is not listed is dropped, and so is every property except the event's allowlisted ones, the SDK's own `$`-prefixed ones (such as `$session_entry_utm_*`; person-property setters are dropped), `distinct_id`, and `token`. An event tracked before the user is identified is held in memory (at most 20, with its time), sent once identity settles, and discarded if the user changed. If an adopter uses PostHog's optional managed reverse proxy (`NEXT_PUBLIC_POSTHOG_HOST`), events pass through Cloudflare, which PostHog lists as a subprocessor ([PostHog: managed reverse proxy](https://posthog.com/docs/advanced/proxy/managed-reverse-proxy)). |
| **Sentry** | Error events with stack traces, and performance transactions when `SENTRY_TRACES_SAMPLE_RATE` is above 0. No user, cookies, headers, bodies, query strings, or local variables; a scrubber redacts secret-shaped values, and replaces a path segment shaped like a provider's ID (`user_…`, `org_…`, `cus_…`) with `{id}` in URLs, so the paths of the API's calls to Clerk and Stripe, and of the organization routes, carry none. The SDKs add no `sentry-trace` or `baggage` header to calls to other services. API events carry the tags `request_id`, and the internal `user_id` and `org_id` UUIDs in an authenticated request; the `user` field stays removed. When the build environment holds `SENTRY_AUTH_TOKEN` and `SENTRY_PROJECT`, the web build also uploads the application's source maps (code, not user data), creates and finalizes a release named after the commit, and on Vercel records its deploy (environment and deployment URL). It sends no commit data: `release.setCommits` is off ([ADR-0004 §25](../dev-docs/adr/0004-cycle-1-adopted-decisions.md)). | The client IP of browser events, depending on project settings | **Recommended:** enable "Prevent storing of IP addresses" and keep server-side data scrubbing on. |
| **LLM provider** | No user data. The connectivity check reads model metadata only. | The calling server's IP and API key usage | — |
| **Vercel** (the hosting the template ships for) | Every request and response of both apps, because it serves them: the URL with its query string, the headers (cookies and the bearer token included), the bodies, and the client IP address, as the endpoint of the TLS connection. It also calls the job routes from its cron. | Its runtime logs. [Vercel's page](https://vercel.com/docs/logs/runtime) lists, for each function request, the method, request path, search parameters, time, status, host, request ID, user agent, region and firewall outcome, and every line the function wrote to stdout (the API's JSON lines). It does not list the client IP among those fields, and says its "Logs from your browser" filter matches your IP address and user agent. | Retention follows the plan: Hobby 1 hour, Pro 1 day, Enterprise 3 days, and 30 days with Observability Plus (page updated 2026-08-28). The path and search parameters are the real ones: the API's own line holds only the route template (see Logs). A log drain you add sends these logs on. These records are the platform's; the template cannot scrub them. |
| **Supabase** (the hosted PostgreSQL the template documents; optional) | The application database: every table in [the list above](#application-database-postgresql-schema-app), over the Transaction pooler, as the `app_login` role. | The provider's own copies and records: backups (plan-dependent), database logs, and the Dashboard accounts of your team | A deletion in the database does not reach its backups until they expire ([Supabase: database backups](https://supabase.com/docs/guides/platform/backups); backup and recovery objectives are the adopter's, [operations.md](operations.md#backup-and-restore)). The project's region is fixed when it is created. |

## Logs (API stdout)

Each request produces one JSON line containing:

- the request ID;
- the method;
- the **route template** (for example `/orgs/{org_id}/projects`), never the actual path;
- the status and the duration;
- for an authenticated request, the internal `user_id` and, in an organization context, `org_id`: pseudonymous UUIDs, on every line of that request. A request refused before it has a tenant context (a missing token, a non-member organization) logs neither.

Logs never include IP addresses, emails, names, tokens, headers, or bodies, and carry no Clerk ID except the one `actor_session` line, which names the impersonating actor and exists only when impersonation is allowed. Billing lines carry Stripe IDs, which name records in your Stripe account and no person: the customer, subscription and price IDs (`stripe_customer_id`, `stripe_subscription_id`, `stripe_subscription_ids`, `replaced_customer_id`, `price_id`), the event ID of a rejected synchronization (`event_id`), and Stripe's own request ID when Stripe refuses a call (`stripe_request_id`; the API's request ID stays `request_id`). Every log message and field passes through redaction; patterns are redacted, free text is not recognized, so keep personal data out of exception messages. Retention depends on the hosting platform (DEC-004, OBS-09). uvicorn's own access line, which carries the client address and the raw path and query string, is off: `pnpm dev:api` passes `--no-access-log`, and on Vercel the runtime shim starts its uvicorn with the access log off (`vercel_runtime` 0.23.0, read in a local `vercel build`; NOT VERIFIED for the version a deployment installs). Vercel's own request logs are the platform's, and they keep the real path and the search parameters ([Vercel](#external-services)).

## Browser storage

| Storage | Content | Set by |
|---|---|---|
| Cookies (`__session`, `__client_uat`, and others) | Clerk session state | Clerk |
| `localStorage` and `sessionStorage` (PostHog keys) | PostHog distinct ID, first-seen and session properties (token parameters masked, referrers reduced to origin and path), and queued events | PostHog, only when analytics is enabled |

Consent requirements for analytics storage are an adopter decision (checklist item 8).

## Adopter decision checklist

Answer these questions before production, record the policy in an ADR, and keep this inventory in sync with it. Template code never deletes payment records. Do not claim that deletion is complete until each system's deletion has been tested against sandbox providers.

1. **Account deletion scope.** For each row above, choose delete, anonymize, or retain:
   - `users`, `audit_log` actors (including `impersonator_clerk_id`), and project names;
   - the stored Checkout Session row, which goes with the user;
   - the Stripe customer metadata;
   - the Clerk user;
   - the PostHog person;
   - Sentry events.
2. **Active subscriptions.** Cancel immediately, cancel at period end, or block deletion until the subscription is canceled. The template's default is the second: when Clerk reports `user.deleted`, Stripe is asked to end the subscription at the period's end (`BILLING_ON_USER_DELETED`, [operations.md](operations.md#a-user-deleted-in-clerk)). Nothing blocks deletion, cancels at once or refunds. Whether users may delete their own account in Clerk at all is step S12 of [the setup guide](setup.md#clerk): the template's header offers it through `<UserButton />` when Clerk's setting is on, and you can delete a user in the Clerk Dashboard whatever it says.
3. **Sole organization owners.** Block deletion, transfer ownership, or delete the organization.
4. **Export.** Scope (which tables and providers) and format (for example JSON).
5. **Retention periods** for `audit_log`, `billing_events`, `identity_events`, logs, and telemetry. Configure the matching settings in PostHog, Sentry, and your log drain.
6. **Mandatory retention** (legal, tax, fraud): which records, for how long, and who may access them.
7. **Completion time.** The expected time to finish a deletion across all systems.
8. **Analytics consent.** Which jurisdictions apply. Whether PostHog may run before consent, or must stay off until the user opts in.
