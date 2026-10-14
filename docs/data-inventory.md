# Data Inventory

This inventory lists every place the template stores or sends data about people, why, and for how long. It is the evidence that DEC-004 requires (spec §3.3) and reflects the code as of 2026-10-14.

> **Adopter decision (DEC-004, [ADR-0002](../dev-docs/adr/0002-organization-entitlements-contacts-adopter-decisions.md)).** The template defines no retention periods and has no account deletion or export feature. The "Retention" column therefore describes **current behavior**, not policy. Each project made from the template sets its own policy with the checklist at the end of this file. Update this file whenever code changes what is stored or sent.

## Identifiers

The template never stores email addresses, names, or payment details. Those stay with Clerk and Stripe.

| Identifier | Where it comes from | Where it is used |
|---|---|---|
| Clerk user ID (`user_…`) | Clerk | `app.users`, rate-limit keys (indirectly), diagnostics operator list |
| Internal user ID (UUID) | `app.users.id`, generated on first request | Every table, PostHog `distinct_id`, Stripe metadata |
| Clerk organization ID (`org_…`) | Clerk | `app.organizations` |
| Stripe customer ID (`cus_…`) | Stripe | `app.billing_customers`, `app.subscriptions`, `app.billing_events` |

## Application database (PostgreSQL, schema `app`)

| Table | Personal data | Purpose | Retention (current behavior) |
|---|---|---|---|
| `users` | Clerk user ID, created time | Map identity to an internal ID | Kept indefinitely. `user.deleted` revokes memberships but keeps the row. |
| `organizations` | Clerk organization ID, deleted time | Tenant boundary | Kept; marked deleted on `organization.deleted`. |
| `organization_memberships` | User, organization, role, verification time | Authorization mirror (DEC-009) | Replaced on each re-verification; removed when Clerk reports removal or user deletion. |
| `projects` | Name (**free text a user may fill with personal data**), creator | Sample tenant data | Until the user deletes the project. |
| `billing_customers` | User → Stripe customer ID | Billing link | Kept indefinitely. Payment records are never deleted (DEC-004 interim rule). |
| `subscriptions` | Status, plan, period dates, cancellation dates | Entitlements | Kept indefinitely. |
| `billing_events` | Stripe event ID, event type, customer ID, timestamps, attempt count; **no payloads** | Webhook idempotency ledger | Kept indefinitely. |
| `identity_events` | Clerk/Svix event ID, event type, timestamps; **no payloads** | Webhook idempotency ledger | Kept indefinitely. |
| `audit_log` | Actor user, organization, action, target, outcome, request ID | Security audit (ADM-02) | Kept indefinitely. |
| `rate_limit_counters` | Keys containing the internal user ID (`llm:user:…`, `checkout:…`, `diagnostics:user:…`), window start, hit count | Rate limits (DEC-011) | Until `prune-rate-limits` runs. Scheduling it is not yet configured. |

Backups of this database follow the hosting plan's schedule. They are not defined yet (DEC-005).

## External services

| Service | What the template sends | What the service also collects on its own | Notes |
|---|---|---|---|
| **Clerk** | Nothing beyond normal sign-in flows; the API reads a user's organization memberships. | Email, name, password hash or OAuth identity, sessions, devices, IP addresses | Clerk is the identity provider. Deletion and export of this data use Clerk's dashboard or API. |
| **Stripe** | Internal user ID as customer metadata, `client_reference_id`, and subscription metadata | Name, email, billing address, payment method, invoices, entered in Stripe Checkout or the Customer Portal | Payment records may have legal retention obligations (DEC-004 question 6). |
| **PostHog** | Allowlisted events only, identified by internal user ID. Browser: page views with query strings stripped, `upgrade_clicked`, `paywall_viewed`, `billing_portal_clicked`, `integration_test_sent`. API: `project_created`, `checkout_started`, `billing_portal_opened`, `llm_check_run`, `integration_test_sent`. | The client IP address of browser requests, unless the project discards it | Python SDK sends `disable_geoip=True`. **Recommended:** enable "Discard client IP data" in the PostHog project. Autocapture and session recording are off. |
| **Sentry** | Error events with stack traces. No user, cookies, headers, bodies, query strings, or local variables; a scrubber redacts secret-shaped values. | The client IP of browser events, depending on project settings | **Recommended:** enable "Prevent storing of IP addresses" and keep server-side data scrubbing on. |
| **LLM provider** | No user data. The connectivity check reads model metadata only. | The calling server's IP and API key usage | — |

## Logs (API stdout)

Each request produces one JSON line containing:

- the request ID;
- the method;
- the **route template** (for example `/orgs/{org_id}/projects`), never the actual path;
- the status and the duration.

Logs never include IP addresses, user IDs, tokens, headers, or bodies. Every log message and field passes through redaction. Retention depends on the hosting platform (DEC-004, OBS-09).

## Browser storage

| Storage | Content | Set by |
|---|---|---|
| Cookies (`__session`, `__client_uat`, and others) | Clerk session state | Clerk |
| `localStorage` (PostHog keys) | PostHog distinct ID and queued events | PostHog, only when analytics is enabled |

Consent requirements for analytics storage are an adopter decision (checklist item 8).

## Adopter decision checklist

Answer these questions before production, record the policy in an ADR, and keep this inventory in sync with it. Template code never deletes payment records. Do not claim that deletion is complete until each system's deletion has been tested against sandbox providers.

1. **Account deletion scope.** For each row above, choose delete, anonymize, or retain:
   - `users`, `audit_log` actors, and project names;
   - the Stripe customer metadata;
   - the Clerk user;
   - the PostHog person;
   - Sentry events.
2. **Active subscriptions.** Cancel immediately, cancel at period end, or block deletion until the subscription is canceled.
3. **Sole organization owners.** Block deletion, transfer ownership, or delete the organization.
4. **Export.** Scope (which tables and providers) and format (for example JSON).
5. **Retention periods** for `audit_log`, `billing_events`, `identity_events`, logs, and telemetry. Configure the matching settings in PostHog, Sentry, and your log drain.
6. **Mandatory retention** (legal, tax, fraud): which records, for how long, and who may access them.
7. **Completion time.** The expected time to finish a deletion across all systems.
8. **Analytics consent.** Which jurisdictions apply. Whether PostHog may run before consent, or must stay off until the user opts in.
