# Operations

Operational guidance for adopters. Items that depend on unresolved decisions, or on decisions each adopter makes, are marked. Do not treat them as verified.

## Environments

| Environment | Database | Integration modes | Notes |
|---|---|---|---|
| `local` | Docker PostgreSQL (`pnpm db:up`) or a non-production Supabase project | `mock` or `sandbox` | Developer keys only |
| `test` | Ephemeral database created by the test suite | `mock` | No `.env` is read |
| `preview` | Isolated non-production database with synthetic data | `sandbox` | Preview-scoped keys |
| `production` | Production database | `live` only | Startup rejects `mock` and `sandbox` |

Startup validation enforces key/mode consistency: live keys outside production, or test keys in production, prevent the API from starting.

## Deployment

> **DEC-001 is open.** Running the Python API on Vercel has not been verified on the platform. A local proof ([`proofs/dec-001`](../proofs/dec-001/README.md), [evidence](../dev-docs/evidence/dec-001-vercel-python.md)) packaged the API with the Vercel CLI and ran it through Vercel's runtime shim, but nothing has been deployed. Until a preview deployment is verified, do not rely on these steps for production.

The intended topology (ADR-0001):

- The Next.js app is deployed on Vercel.
- The Python API is served by Vercel's Python runtime, via `apps/api/index.py`, which exports an ASGI app.
- The browser reaches the API through the web app's `/api/v1/*` rewrite. `API_ORIGIN` is read at **build time**.

The proof also built and ran a second candidate: one Vercel project with [Services](https://vercel.com/docs/services), where `web` and `api` deploy together and the platform routes `/api/v1/*` to the API (`proofs/dec-001/vercel.json`). Choosing the topology is part of resolving DEC-001.

Before a first deployment:

1. Complete the DEC-001 proof in a preview environment and record the findings.
2. Configure environment variables per environment in the hosting provider. Never copy production values elsewhere. Set `APP_ENV` explicitly (`preview` or `production`): a deployed bundle has no `.env` file, so it refuses to start without it.
3. Apply migrations (below) **before** deploying code that depends on them.
4. Add platform rate-limit rules for coarse per-IP limits (DEC-011). Application-level limits use PostgreSQL counters.

Deploying, promoting, or changing hosting and Git settings are remote actions. They require explicit authorization from the repository owner.

## Migrations

- Migrations live in `supabase/migrations/`.
- They are ordered, checksummed, and immutable once applied. The runner refuses edited migrations.
- Run them with `pnpm db:migrate`. For a non-local database, add `--allow-remote` and use a direct connection, and only with authorization.
- A migration statement that waits more than 5 s for a table lock (held by running traffic) fails, and its migration is rolled back, instead of making every later query on that table queue behind it. Re-run when traffic is lower. A second concurrent run still waits for the first one to finish.
- `pnpm db:seed --yes` and `pnpm db:reset --yes` refuse to run unless `APP_ENV` is set to `local` or `test` (an unset `APP_ENV` does not count) and every database host is local: `localhost`, a loopback address, or a Unix socket in `/tmp`, `/private/tmp`, `/var/run/postgresql`, or `/run/postgresql`. A socket elsewhere, such as the Cloud SQL Auth Proxy's `/cloudsql/...`, may lead to a remote database. A tunnel to a remote database on a local port still looks local, so never run them with such a tunnel open.
- Use **expand → deploy → contract** for changes that the previous release must survive: add new columns or tables first, deploy code that uses them, and remove old structures in a later release.
- A destructive change (drop, rename, type narrowing, or data rewrite) requires:
  - review;
  - a verified backup;
  - owner authorization before it runs against any shared environment.

## Rollback

- **Code**: redeploy the previous release. Because migrations are backward compatible, the previous release runs against the current schema.
- **Schema**: there is no automatic down-migration in shared environments. Fix forward with a new migration.

## Health and readiness

- `GET /api/v1/health`: liveness only, with no dependency calls.
- `GET /api/v1/ready`: checks the database and returns `503` with `{"status":"not_ready"}` when it is unavailable, at most one round-trip deadline later (below). It does not reveal configuration.

## Provider outages

| Provider down | Behavior |
|---|---|
| Clerk | New sign-ins fail. Requests with valid tokens continue while the cached key set is valid (up to an hour; indefinitely with `CLERK_JWT_KEY`), then fail closed (`503`). A request whose key is cached never waits on Clerk: the refresh ahead of expiry runs in the background. Key set refreshes are attempted at most once every 5 s per instance, with a 3 s timeout, and a token with an unknown key ID waits at most that long for a refresh another request started. While the key set cannot be read, such a token gets `503`, not `401`, since its key may have just been rotated in. Stale organization memberships cannot be re-verified, so organization requests fail closed (`503`). A membership read first ends the request's database transaction, so it holds no connection or admission place. After a failed membership read, reads fail fast for 5 s instead of each waiting out the 5 s timeout. |
| Stripe | Checkout and portal return `503`. Existing entitlements (from synchronized records) are unaffected. Stripe retries webhooks; run reconciliation afterwards. Webhook synchronization runs in at most `DATABASE_WORKER_POOL_MAX_SIZE - 1` slots per instance (the setting must be at least 2). A delivery that finds no free slot queues in the event loop, holding no server thread, for up to `DATABASE_CONNECT_TIMEOUT_SECONDS`; after that it gets `503` and Stripe retries it. So a burst never takes the database connections that readiness and rate limits need, nor the threads that every other route needs. |
| Database | Readiness fails. Data requests return `503`. No partial writes. A database or pooler that stops responding fails each call after the round-trip deadline: `DATABASE_STATEMENT_TIMEOUT_MS` plus `DATABASE_CONNECT_TIMEOUT_SECONDS` (15 s by default). After a pooler restart or failover, connections that died while idle are replaced on first use, without failing the request. |
| PostHog / Sentry | No user-visible impact. Exports fail silently and are logged. |
| LLM provider | Only the connectivity check reports failure. |

## Routine tasks

| Task | Command | Frequency |
|---|---|---|
| Billing reconciliation (dry run) | `pnpm billing:reconcile` | After incidents or missed webhooks |
| Apply reconciliation | `pnpm billing:reconcile --apply` | When a dry run shows divergences you have reviewed |
| Prune rate-limit counters and processed outbox events | Job `prune-expired` (see [Scheduled jobs](#scheduled-jobs)) | Daily |
| Dependency audit | `pnpm security:audit` | Weekly and before releases |

### Reading a reconciliation report

`pnpm billing:reconcile` checks every customer, first those with webhook events flagged `needs_reconciliation`. With `--apply`, it reads Stripe under the same per-customer lock as webhook processing, so it never overwrites a newer webhook result, and it records plan changes like a webhook does.

| Field | Meaning | What to do |
|---|---|---|
| `divergences`, kind `differs` or `missing_locally` | Stripe and the local record disagree | `--apply` stores Stripe's state |
| `divergences`, kind `missing_remotely` | A local subscription that is already final (`canceled`, `incomplete_expired`) and that Stripe no longer lists for that customer | Never applied automatically. Subscriptions that are not yet final are read from Stripe by ID instead (for example, after the customer was deleted in Stripe, which cancels them), and their state is applied |
| `unlinked_customers` | Flagged Stripe customers that no user could be linked to | `--apply` links a customer whose Stripe metadata names an existing user; others need investigation in Stripe |
| `rejected_customers` | Stripe rejected the request for that customer. Usually the customer was deleted in Stripe after its stored subscriptions had all ended: nothing to do, and it is reported each time the rotation reaches it. If *every* customer is rejected, the keys belong to a different Stripe account (for example, a new sandbox) | Nothing is changed and existing entitlements stay as they are. If many customers are rejected, check the keys first; otherwise confirm in Stripe that the customer is deleted |
| more than one Pro subscription for one user | Logged as `billing_multiple_entitling_subscriptions` | Cancel and refund the duplicate in Stripe |

## Secret rotation

Rotate immediately after any suspected exposure, and periodically otherwise.

1. **Clerk**: create a new secret key, deploy it, then revoke the old key. For webhooks, roll the endpoint's signing secret.
2. **Stripe**: roll the API key, preferably a restricted key with the minimum permissions. Roll each webhook endpoint's signing secret separately, per environment.
3. **Database**: rotate the database password in Supabase, update `DATABASE_URL` everywhere, then restart.
4. **LLM / Sentry auth token / PostHog**: rotate the key at the provider and update the environment.
5. **CRON_SECRET**: generate a new value (`openssl rand -hex 32`) and set it in the API environment. Vercel Cron sends the current value automatically.

After rotating, confirm the old secret no longer works and record the rotation, without values, in your incident log.

## Incident response

1. **Contain**: revoke or rotate exposed credentials, and disable affected features through configuration.
2. **Assess**: use request IDs (the `X-Request-ID` header, also present in logs and Sentry) to trace requests. Logs never contain tokens or request bodies.
3. **Recover**: deploy fixes forward, run billing reconciliation if billing was affected, and verify with the readiness endpoint.
4. **Review**: write a short report and update the threat model (spec §13.2) and tests.

Vulnerability reports from outside the team follow [SECURITY.md](../SECURITY.md).

## Backup and restore

> **Adopter decision (DEC-005, [ADR-0002](../dev-docs/adr/0002-organization-entitlements-contacts-adopter-decisions.md)).** The template sets no recovery objectives and makes no recovery-readiness claim. Decide and test the items below before production.

**Adopter checklist:**

1. **Objectives.** Choose a recovery point objective (the acceptable data-loss window) and a recovery time objective.
2. **Plan capabilities.** Confirm what your Supabase plan offers: daily backups, their retention, and whether point-in-time recovery is available. Choose a plan that meets the objectives.
3. **Authority.** Name who may start a restore, and how that person gets production database credentials.
4. **Hosted restore test.** Run a timed restore into an isolated, non-production project that holds synthetic data only:
   1. Load synthetic data into the source project. `pnpm db:seed` refuses non-local databases by design, so apply `supabase/seed.sql`, or a larger synthetic set, with `psql` and the migration-role connection string.
   2. Record per-table row counts and checksums. The queries in `scripts/restore-drill.mjs` work against any PostgreSQL database.
   3. Restore a backup (or a point in time) into a **new** project. Never restore over the source.
   4. Re-run the counts and checksums. Also confirm that RLS is still forced on every `app` table, and that the migration ledger matches.
   5. Record the elapsed time against the RTO, and the data-loss window against the RPO.
5. **Evidence.** Record the method, dates, plan, timings, and results in `dev-docs/evidence/`, labeled `sandbox`.
6. **Repeat** after major schema changes, and on a schedule you choose.

**Local restore drill.** `pnpm db:restore-drill` dumps the local Docker database with `pg_dump` and restores it into a fresh PostgreSQL server: a throwaway container from the same image, with no network, that has never run a migration. The container is removed afterwards. It then verifies:

- per-table row counts and content checksums;
- row-level-security flags;
- object owners, privileges, and policies in the `app` schema;
- the attributes and memberships of the `app_api` and `app_worker` roles;
- the migration ledger.

A database dump does not contain roles, which belong to the whole server. A logical restore into a new server therefore needs two things:

- **Create the roles first.** Run the role block at the top of `supabase/migrations/20261014000001_core_schema.sql`. The drill does exactly this. Without it, `pg_restore` fails with `role "app_worker" does not exist`.
- **Keep the owners.** Do not pass `--no-owner`. `app.lock_my_membership` is a `SECURITY DEFINER` function that must stay owned by `app_worker`. With `--no-owner`, the role that runs the restore owns it instead, and the drill fails.

The drill proves that the dump format and this restore procedure work for this schema on a local server that you control as a superuser. It does not prove:

- a restore through a hosted provider's backup or point-in-time recovery, or into a server where you are not a superuser (such as a new Supabase project, where provider-managed roles already exist);
- anything about recovery objectives.

Those belong to the hosted restore test above.

## Data retention and deletion

> **Adopter decision (DEC-004, [ADR-0002](../dev-docs/adr/0002-organization-entitlements-contacts-adopter-decisions.md)).** The template has no account deletion or export feature and makes no deletion-completeness claim. Use the checklist in [data-inventory.md](data-inventory.md) to set your policy before production.

Current behavior:

- `user.deleted` from Clerk revokes all of the user's organization memberships, and no membership read taken earlier can restore them. Application data and billing records are retained.
- Webhook ledgers store event IDs and types only, not payloads.
- Telemetry retention follows each provider's project settings.

## Scheduled jobs

Jobs are registered in code ([architecture](architecture.md#jobs)). Run them in either of two ways:

- **CLI:**
  - `cd apps/api && uv run python -m app.cli job` lists jobs;
  - `uv run python -m app.cli job <name>` runs one.
- **HTTP, which Vercel Cron uses:** `GET /api/v1/jobs/<name>` with `Authorization: Bearer <CRON_SECRET>`.
  - Without `CRON_SECRET`, the endpoint returns 404. In production the API then logs a `jobs_endpoint_disabled` warning at startup, because nothing else runs the jobs.
  - A wrong or missing secret returns 401.
  - Rotate the secret like any other (see [Secret rotation](#secret-rotation)).

| Job | Default schedule (UTC) | What it does |
|---|---|---|
| `drain-outbox` | 02:15 daily | First records expired leases (the worker died) as failed attempts: a retry after backoff, or `dead` on the last attempt. Then dispatches outbox events left after requests (failed or not yet dispatched), until none is due or 60 s have passed. An event whose worker keeps dying therefore uses one attempt per drain and goes behind the events already due, so it never blocks them |
| `prune-expired` | 05:00 daily | Deletes expired rate-limit windows, and processed outbox events older than `OUTBOX_RETENTION_DAYS`, in batches of 1,000 for up to 60 s; the next run continues |
| `reconcile-billing` | 04:00 daily | Compares a batch of `BILLING_RECONCILE_BATCH_SIZE` customers with Stripe: up to half are customers with recently flagged (`needs_reconciliation`) webhook events, the rest the least recently checked, so every customer is checked in turn. A customer Stripe rejects (for example, deleted in Stripe) is reported as `rejected` and still moves to the back of the rotation. A run stops starting new checks after 60 s. It applies Stripe's state only when `BILLING_RECONCILE_APPLY=true` |
| `reconcile-memberships` | 03:30 daily | Re-verifies the stalest organization-membership mirrors against Clerk (`MEMBERSHIP_RECONCILE_BATCH_SIZE` per run) |

`apps/api/vercel.json` holds the schedules, and a test fails if it drifts from the registry. Schedules are daily so the configuration deploys on any Vercel plan. If your plan allows it, run `drain-outbox` every few minutes. With Vercel Services (one project), crons must be in the root `vercel.json`: the DEC-001 proof found that a service's own `vercel.json` crons are not registered.

**Not verified until DEC-001:** Vercel Cron delivery, plan limits, and whether after-response dispatch completes before the function is frozen. If it does not complete, `drain-outbox` delivers the events at its next run. Vercel Cron invokes **production** deployments only, never previews. Verifying cron delivery therefore needs a production deployment, which is a separately authorized remote action. On a preview, call the job routes yourself with `Authorization: Bearer <CRON_SECRET>`; that checks the routes, not the scheduler.

## Outbox

- **Inspect.** Run this as an operator (read-only):

  ```sql
  SELECT status, count(*) FROM app.outbox GROUP BY status;
  SELECT id, event_type, attempts, last_error FROM app.outbox WHERE status = 'dead';
  ```
- **Replay a dead event** after fixing its handler: `UPDATE app.outbox SET status = 'pending', attempts = 0, available_at = now() WHERE id = '<id>';` Handlers are idempotent, so a replay is safe.
- **Retention:** processed events are deleted after `OUTBOX_RETENTION_DAYS`. Dead events are kept until you replay or delete them.
- **Leases:** `OUTBOX_LEASE_SECONDS` (default 900) must stay longer than the longest a function may run (Vercel: 300 s by default, up to 800 s). If you raise the function duration past it, raise the lease too, or a slow handler can lose its lease and run twice at once.

## Connection pools

Each API process opens up to `DATABASE_POOL_MAX_SIZE` request connections (`app_api`) and `DATABASE_WORKER_POOL_MAX_SIZE` system connections (`app_worker`). Size them so that the maximum number of concurrent instances × (both pool sizes) fits your database pooler's client limit. With Supabase, use the transaction pooler connection string.

Each process runs blocking work on 40 threads. At most 40 − `DATABASE_POOL_MAX_SIZE` of them may wait for a request connection outside a request-scoped transaction; past that, requests get a 503 at once (logged as `database_waiters_exhausted`). Keep `DATABASE_POOL_MAX_SIZE` well below 40 ([architecture](architecture.md#data-access)).

## Integration diagnostics

The `/integrations` page and `/api/v1/diagnostics/*` expose configuration facts and call external services.

- **Access.** Only Clerk user IDs listed in `DIAGNOSTICS_OPERATOR_IDS` may use them. The one exception is `APP_ENV=local` with no operators listed.
- **Production.** They are disabled unless `DIAGNOSTICS_ENABLED=true`.
- **When disabled or not permitted**, every diagnostics route returns `404`.

## Performance testing

See [perf/README.md](../perf/README.md). Local runs are for relative comparisons only. Runs against deployed environments need an owner-approved run plan, and production load testing is prohibited without explicit authorization.

## Python runtime limitations

From the DEC-001 local proof ([evidence](../dev-docs/evidence/dec-001-vercel-python.md)). "Documented" values come from Vercel's documentation and have not been observed on the platform. "Local" values were measured on one development machine.

| Topic | Finding |
|---|---|
| Runtime | `python3.12` on x86_64. Vercel's runtime shim (`vercel_runtime`, with a vendored uvicorn) serves the ASGI app, and one instance serves concurrent requests. Local: verified in an Amazon Linux 2023 container. |
| Bundle | Local: 74.3 MiB uncompressed, installed from `uv.lock` with Linux wheels. Documented limit: 500 MB. `excludeFiles` keeps `tests/` out. |
| Function root | Read-only. The API writes nothing there, and in a bundle it reads no `.env` file: configuration comes from environment variables only. |
| Cold start | Local, under x86_64 emulation: 2.2 to 6.6 s to the first response (p50 3.6 s; it varies with machine load). Natively, about 0.6 s. Platform cold starts are not measured: pre-warming and bytecode caching differ. |
| Duration | Documented: 300 s by default (the maximum on Hobby); up to 800 s on Pro and Enterprise. |
| Payload | Documented: 4.5 MB request and response bodies. The API rejects bodies over `MAX_REQUEST_BODY_BYTES` (1 MB) first. |
| Database | Use the transaction pooler. Every setting is transaction-local and prepared statements are off (local: verified with PgBouncer in transaction mode). A database or pooler that stops responding fails each call after the round-trip deadline (15 s by default). |
| Crons | With Services, only the root `vercel.json` crons are registered. |
