# Operations

Day-two procedures for adopters: health checks, provider outages, routine tasks, key rotation, database operations, scheduled jobs, limits, incidents, and backups. How to deploy, which variable goes where, and rollback are in [deployment.md](deployment.md). Items that depend on unresolved decisions, or on decisions each adopter makes, are marked. Do not treat them as verified: nothing here has been run on a deployed platform ([limitations.md](limitations.md)).

## Health and readiness

- `GET /api/v1/health`: liveness only, with no dependency calls. Answers `{"status":"ok"}`.
- `GET /api/v1/ready`: checks the database and answers `{"status":"ready","checks":{"database":"ok"}}`, or `503` with `{"status":"not_ready"}` when it is unavailable, at most one round-trip deadline later ([provider outages](#provider-outages)). It does not reveal configuration.
- In preview and production a database login that could skip row-level security makes every database transaction fail with `503` and `/ready` report not ready, while `/health` stays up ([The runtime login role](#the-runtime-login-role)).

## Provider outages

- **Clerk.**
  - New sign-ins fail.
  - Requests with valid tokens continue while the cached key set is valid (up to an hour; indefinitely with `CLERK_JWT_KEY`), then fail closed (`503`). A request whose key is cached never waits on Clerk: the refresh ahead of expiry runs in the background.
  - Key-set refreshes are attempted at most once every 5 s per instance, with a 3 s timeout. A token with an unknown key ID waits at most that long for a refresh another request started. While the key set cannot be read, such a token gets `503`, not `401`, since its key may have been rotated in a moment ago.
  - Stale organization memberships cannot be re-verified, so organization requests fail closed (`503`). A membership read first ends the request's database transaction, so it holds no connection or admission place. After a failed membership read, reads fail fast for 5 s instead of each waiting out the 5 s timeout.
- **Stripe.** Checkout and the portal return `503`. A rejected API key, or a rejected customer, Checkout or portal request (the server builds every input), is `503 NOT_CONFIGURED`. Existing entitlements (from synchronized records) are unaffected. Stripe retries webhooks; run reconciliation afterwards. Webhook synchronization runs in at most `DATABASE_WORKER_POOL_MAX_SIZE - 1` slots per instance (the setting must be at least 2). A delivery that finds no free slot queues in the event loop, holding no server thread, for up to `DATABASE_CONNECT_TIMEOUT_SECONDS`; after that it gets `503` and Stripe retries it. A burst therefore never takes the database connections that readiness and rate limits need, nor the threads that every other route needs.
- **Database.** Readiness fails. Data requests return `503`. No partial writes. A database or pooler that stops responding fails each call after the round-trip deadline: `DATABASE_STATEMENT_TIMEOUT_MS` plus `DATABASE_CONNECT_TIMEOUT_SECONDS` (15 s by default). After a pooler restart or failover, connections that died while idle are replaced on first use, without failing the request.
- **PostHog and Sentry.** No user-visible impact. Exports fail silently and are logged.
- **LLM provider.** Only the connectivity check reports failure.

Each `503` the API chooses (a dependency outage, a missing configuration) is reported to Sentry, tagged `error_code`, at most once a minute per cause per instance; `NOT_CONFIGURED` is a warning. The log line `dependency_or_configuration_error` carries the cause type.

## Routine tasks

| Task | Command | Frequency |
|---|---|---|
| Billing reconciliation (dry run) | `pnpm billing:reconcile` | After incidents or missed webhooks |
| Apply reconciliation | `pnpm billing:reconcile --apply` | When a dry run shows divergences you have reviewed |
| Prune rate-limit counters and processed outbox events | Job `prune-expired` ([Scheduled jobs](#scheduled-jobs)) | Daily |
| Dependency audit | `pnpm security:audit` | Weekly and before releases |
| Replay a missed Stripe delivery | Workbench > Webhooks > the endpoint > Event deliveries > **Resend** (up to 15 days after the event), or `stripe events resend <event_id> --webhook-endpoint=<endpoint_id>` (up to 30 days). Then `pnpm billing:reconcile` | After a failed delivery |

Stripe retries a failed delivery automatically for up to three days with exponential back-off in live mode; events created in a sandbox are retried three times over a few hours ([Stripe: webhooks](https://docs.stripe.com/webhooks)). A manual resend does not stop the automatic retries.

`pnpm billing:reconcile` and `job` read the full API configuration from `.env` or the environment, `STRIPE_WEBHOOK_SECRET` included.

### Reading a reconciliation report

`pnpm billing:reconcile` checks every customer, first those with webhook events flagged `needs_reconciliation`. With `--apply`, it reads Stripe under the same per-customer lock as webhook processing, so it never overwrites a newer webhook result, and it records plan changes like a webhook does.

| Field | Meaning | What to do |
|---|---|---|
| `divergences`, kind `differs` or `missing_locally` | Stripe and the local record disagree | `--apply` stores Stripe's state |
| `divergences`, kind `missing_remotely` | A local subscription that is already final (`canceled`, `incomplete_expired`) and that Stripe no longer lists for that customer | Never applied automatically. Subscriptions that are not yet final are read from Stripe by ID instead (for example, after the customer was deleted in Stripe, which cancels them), and their state is applied |
| `unlinked_customers` | Flagged Stripe customers that no user could be linked to | `--apply` links a customer whose Stripe metadata names an existing user; others need investigation in Stripe |
| `rejected_customers` | Stripe rejected the request for that customer. Usually the customer was deleted in Stripe after its stored subscriptions had all ended: nothing to do, and it is reported each time the rotation reaches it. If *every* customer is rejected, the keys belong to a different Stripe account (for example, a new sandbox) | Nothing is changed and existing entitlements stay as they are. If many customers are rejected, check the keys first; otherwise confirm in Stripe that the customer is deleted |
| more than one Pro subscription for one user | Logged as `billing_multiple_entitling_subscriptions` | Cancel and refund the duplicate in Stripe |

A paid subscription on a price that is neither one of the two configured prices nor a monthly or yearly price of `STRIPE_PRODUCT_PRO` is logged as `billing_unknown_price` at error level: the user is paying without Pro. Check the price in the Dashboard (its product, and that it bills every month or every year) and `STRIPE_PRODUCT_PRO`, fix what is wrong, then run `pnpm billing:reconcile --apply`.

## Key rotation

Rotate immediately after any suspected exposure, and periodically otherwise. For every secret:

1. Create the new value at the provider. Keep the old one valid until step 5 where the provider allows an overlap.
2. Set the new value everywhere the table lists, in every Vercel scope that uses it.
3. **Redeploy.** Vercel applies a variable change only to new deployments ([Vercel: environment variables](https://vercel.com/docs/environment-variables/managing-environment-variables)), and the web values marked *baked* in [deployment.md](deployment.md#variables-which-service-reads-what) are fixed at build, so the rebuild is what makes them take effect. "Restart" is not enough.
4. Verify.
5. Revoke the old value at the provider.
6. Record the rotation, without values, in your incident log, and confirm the old value no longer works.

| Secret | Set in | Verify | Notes |
|---|---|---|---|
| `CLERK_SECRET_KEY` | Vercel: the web proxy and the API both read it; local `.env` | Sign in; the Clerk check on `/integrations` | Revoke the old key in the Clerk Dashboard once the new deployment is live |
| `CLERK_WEBHOOK_SIGNING_SECRET` | Vercel (API) | Resend a delivery from the Clerk endpoint | Roll the endpoint's signing secret in Clerk; one secret per environment |
| `CLERK_JWT_KEY` | Vercel: the API, and the web build for the proxy | Sign in | A public key, not a secret. Update it when the instance's signing key changes (Dashboard > API keys > Show JWT public key). Until the new deployment is live, tokens signed with the new key fail; leave it empty to fetch the key set instead |
| `STRIPE_SECRET_KEY` | Vercel (API); local `.env` | The Stripe check; `pnpm check:setup` | Prefer a restricted key with the minimum permissions ([integrations.md](integrations.md#stripe)) |
| `STRIPE_WEBHOOK_SECRET` | Vercel (API), one per endpoint and environment | The endpoint's Event deliveries show `2xx` | In Workbench, roll the secret and choose to expire the old one immediately or after up to 24 hours: set the new secret and deploy within that window, so deliveries keep verifying |
| `DATABASE_URL` (the `app_login` password) | Vercel (API) | `/api/v1/ready`; the PostgreSQL check | Re-run `pnpm db:api-login --allow-remote`. The old password stops working at once, so requests fail until the redeploy finishes. The `postgres` password is rotated separately in the Supabase Dashboard |
| `MIGRATION_DATABASE_URL` | GitHub Environment secrets (`preview`, `production`) | Run **Database migrate** | Rotate the `postgres` password in Supabase |
| `LLM_API_KEY` | Vercel (API) | The LLM check | Revoke at the provider |
| `CRON_SECRET` | Vercel, Production scope | Call a job route with the new value; the old one answers `401` | `openssl rand -hex 32`, at least 32 characters. Vercel sends the project's `CRON_SECRET` as the `Authorization` header of each cron call ([Vercel: securing cron jobs](https://vercel.com/docs/cron-jobs/manage-cron-jobs#securing-cron-jobs)) |
| `NEXT_PUBLIC_POSTHOG_KEY`, `NEXT_PUBLIC_SENTRY_DSN` | Vercel (web, and the API for the PostHog key) | Events arrive | Baked: a rotated key stays in already-built browser bundles until you redeploy |
| `SENTRY_DSN` | Vercel (API) | `pnpm check:setup --send-test-events` | |
| `SENTRY_AUTH_TOKEN` | Vercel (web build) | The build log shows the upload | Revoke at Sentry |
| Protection Bypass secret | Vercel project, and the webhook URLs registered at Stripe and Clerk | A test delivery | Regenerating invalidates earlier deployments: redeploy and update the URLs |

Vercel's own guide: [rotating environment variables](https://vercel.com/docs/environment-variables/rotating-secrets).

## Database

### Migrations

- Migrations live in `supabase/migrations/`. They are ordered, checksummed and immutable once applied: the runner refuses an edited migration, and a new migration numbered before one already applied.
- Apply them with `pnpm db:migrate`. For a non-local database add `--allow-remote`, use the Session pooler string (the direct connection is IPv6-only without the add-on), and do it only with authorization. For a deployed environment use the **Database migrate** workflow ([deployment.md](deployment.md#hosted-database-and-migrations)). Apply migrations before deploying code that depends on them.
- Never use `supabase db push`, `supabase db reset` or Supabase Branching on this repository: they keep a second ledger and bypass the runner's checksums and lock timeout.
- A statement that waits more than 5 s for a table lock (held by running traffic) fails, and its migration is rolled back, instead of making every later query on that table queue behind it. Re-run when traffic is lower. A second concurrent run still waits for the first to finish.
- That timeout bounds the wait for a lock, not how long it is held: a plain `CREATE INDEX` or a table rewrite blocks writes for its whole duration. The runner is transactional, so `CREATE INDEX CONCURRENTLY` cannot be used in a migration (the migration lint switches that rule off for this reason). Build such an index by hand with `CONCURRENTLY` first, then add the migration with `IF NOT EXISTS`.
- Use **expand, deploy, contract** for changes the previous release must survive: add new columns or tables first, deploy code that uses them, and remove old structures in a later release. A destructive change (drop, rename, type narrowing, or data rewrite) requires review, a verified backup, and owner authorization before it runs against any shared environment.
- Keep new database objects in schema `app`: `pnpm db:reset` and the restore drill cover that schema. If a migration must create something outside it (an extension, a function in `public`), create it with `IF NOT EXISTS` or `OR REPLACE`, because a reset leaves it behind.
- `pnpm db:seed --yes` and `pnpm db:reset --yes` refuse to run unless `APP_ENV` is set to `local` or `test` (an unset `APP_ENV` does not count) and every database host is local: `localhost`, a loopback address, or a Unix socket in `/tmp`, `/private/tmp`, `/var/run/postgresql`, or `/run/postgresql`. A socket elsewhere, such as the Cloud SQL Auth Proxy's `/cloudsql/...`, may lead to a remote database. A tunnel to a remote database on a local port still looks local, so never run them with such a tunnel open. To start from an empty volume: `docker compose down -v && pnpm db:up && pnpm db:migrate`.
- If the old first migration was applied to a local database, the runner reports that `20261014000001_core_schema.sql` "was modified after being applied": `pnpm db:reset --yes` (this deletes local data; ask before doing it to a database that is not yours).

### The runtime login role

The API connects as `app_login` and every request transaction switches into `app_api` or `app_worker` ([integrations.md](integrations.md#supabase-and-postgresql)). Creating or rotating its password is `pnpm db:api-login --allow-remote`. In preview and production a process whose first transaction finds a login that could skip row-level security (a superuser, `BYPASSRLS`, `CREATEROLE`, a member of such a role, or a table owner in schema `app`) fails every database transaction with a `503` that names the fix, logs one ERROR line (never the URL or the role name), and reports `/ready` not ready. A refused process keeps refusing until it restarts: fix `DATABASE_URL`, then redeploy. The same check shows on the `/integrations` PostgreSQL card (`unsafe_session`).

This guard removes the escape to the table owner and to `BYPASSRLS`. It does not stop code that can run arbitrary SQL from reading across tenants ([integrations.md](integrations.md#supabase-and-postgresql)).

### Restore a dump into a new server

A dump holds no roles, which belong to the whole server. The procedure works for any target, including a Supabase project where the restoring role is not a superuser:

1. Create the roles with the role blocks of the migrations (`app_api`, `app_worker`, `app_login`; the restore drill's `ROLES_SQL` in `scripts/restore-drill.mjs` is the reference).
2. `pg_restore --no-owner --exit-on-error --single-transaction -d "$TARGET_URL" app.dump`. Everything is then owned by the restoring role, as at the source.
3. `psql "$TARGET_URL" -v ON_ERROR_STOP=1 --single-transaction -f supabase/restore/post-restore.sql`. It hands the `SECURITY DEFINER` function `app.lock_my_membership` back to `app_worker`.
4. Check that `app.lock_my_membership(uuid)` is owned by `app_worker`, then run `pnpm db:api-login`: a dump holds no role passwords.

After a restore from a Supabase Dashboard backup (`psql -f`, where errors are expected), run step 3 as well. A restore that keeps the original owners fails on a target where you are not a superuser: `ALTER FUNCTION ... OWNER TO app_worker` is refused with `permission denied for schema app`, an error that is hard to notice among others, and the function stays owned by the restoring role (`postgres` on Supabase). That is why steps 2 and 3 exist.

## Scheduled jobs

Jobs are registered in code ([architecture](architecture.md#jobs)). Run them in either of two ways:

- **CLI:**
  - `cd apps/api && uv run python -m app.cli job` lists jobs;
  - `uv run python -m app.cli job <name>` runs one.
- **HTTP, which Vercel Cron uses:** `GET /api/v1/jobs/<name>` with `Authorization: Bearer <CRON_SECRET>`.
  - Without `CRON_SECRET`, the endpoint returns 404. In production the API then logs a `jobs_endpoint_disabled` warning at startup, because nothing else runs the jobs.
  - A wrong or missing secret returns 401.
  - A job runs once at a time, across all instances, and no sooner than `JOB_MIN_SPACING_SECONDS` (60) after its previous start. A call that finds it running returns 409; one that comes too soon returns 429 with `Retry-After`. A failed run does not block the next: only the spacing counts, from its start. The CLI is not limited.
  - Rotate the secret like any other ([Key rotation](#key-rotation)).

| Job | Default schedule (UTC) | What it does | If a run is missed |
|---|---|---|---|
| `drain-outbox` | 02:15 daily | First records expired leases (the worker died) as failed attempts: a retry after backoff, or `dead` on the last attempt. Then dispatches outbox events left after requests (failed or not yet dispatched), until none is due or 60 s have passed. An event whose worker keeps dying therefore uses one attempt per drain and goes behind the events already due, so it never blocks them | Events left after a request (analytics, side effects) wait for the next run. A dead event stays until you replay it |
| `prune-expired` | 05:00 daily | Deletes expired rate-limit windows, and processed outbox events older than `OUTBOX_RETENTION_DAYS`, in batches of 1,000 for up to 60 s; the next run continues | Rows pile up; behavior does not change |
| `reconcile-billing` | 04:00 daily | Compares a batch of `BILLING_RECONCILE_BATCH_SIZE` customers with Stripe: up to half are customers with recently flagged (`needs_reconciliation`) webhook events, the rest the least recently checked, so every customer is checked in turn. A customer Stripe rejects (for example, deleted in Stripe) is reported as `rejected` and still moves to the back of the rotation. A run stops starting new checks after 60 s. It applies Stripe's state only when `BILLING_RECONCILE_APPLY=true` | A missed webhook is not repaired until the next run, or `pnpm billing:reconcile` |
| `reconcile-memberships` | 03:30 daily | Re-verifies the stalest organization-membership mirrors against Clerk (`MEMBERSHIP_RECONCILE_BATCH_SIZE` per run) | A mirror is re-verified later; access still follows the `MEMBERSHIP_MAX_STALENESS_SECONDS` bound on each request |

**When a job fails.** Vercel does not retry a failed cron invocation. Delivery is best effort: a run can be missed, and the same run can occasionally be delivered twice ([Vercel: managing cron jobs](https://vercel.com/docs/cron-jobs/manage-cron-jobs)). The jobs are written for that: each is safe to repeat and to run concurrently with itself, and each run works through what is outstanding. The guard refuses the second delivery of a run (409 while the first is running, 429 after), which is harmless. A run that the platform stopped (a timeout) holds its job for up to 15 minutes, so a call in that time returns 409: run the job from the CLI to catch up at once. Find a failed run in the project's **Settings > Cron Jobs > View Logs** (cron runs are logged as function invocations); a completed run logs `job_completed` with the job name, `duration_ms` and a summary. To catch up at once, run the job by hand ([above](#scheduled-jobs)) or call its route.

The root `vercel.json` holds the schedules, and a test fails if it drifts from the registry. Schedules are daily so the configuration deploys on any Vercel plan; the Hobby plan runs a daily cron at any minute within the scheduled hour. If your plan allows it, run `drain-outbox` every few minutes, and set `JOB_MIN_SPACING_SECONDS` below the interval if it is a minute or less. With Vercel Services (one project), crons must be in the root `vercel.json`: a service's own `vercel.json` crons are not registered.

**Not verified until the first deployment:** Vercel Cron delivery, plan limits, and whether after-response dispatch completes before the function is frozen. If it does not complete, `drain-outbox` delivers the events at its next run. Vercel Cron calls the **production** deployment only, never a preview ([Vercel: cron jobs](https://vercel.com/docs/cron-jobs)), so verifying it needs a production deployment, which is a separately authorized remote action. On a preview, call the job routes yourself with `Authorization: Bearer <CRON_SECRET>`; that checks the routes, not the scheduler.

## Outbox

- **Inspect.** Run this as an operator (read-only):

  ```sql
  SELECT status, count(*) FROM app.outbox GROUP BY status;
  SELECT id, event_type, attempts, last_error FROM app.outbox WHERE status = 'dead';
  ```
- **Replay a dead event** after fixing its handler: `UPDATE app.outbox SET status = 'pending', attempts = 0, available_at = now() WHERE id = '<id>';` Handlers are idempotent, so a replay is safe.
- **Retention:** processed events are deleted after `OUTBOX_RETENTION_DAYS`. Dead events are kept until you replay or delete them.
- **Leases:** `OUTBOX_LEASE_SECONDS` (default 900) must stay longer than the longest a function may run (Vercel: 300 s by default, up to 800 s). If you raise the function duration past it, raise the lease too, or a slow handler can lose its lease and run twice at once.

## Limits

The application limits these. Counters live in PostgreSQL, so every limit holds across instances. Each setting is in the "Advanced" section of [`.env.example`](../.env.example).

| What | Default | Setting | Over the limit |
|---|---|---|---|
| Writes (`POST`, `PUT`, `PATCH`, `DELETE`) of one signed-in user on routes with no limit of their own: projects and generated modules. One count across the personal context and every organization | 60 a minute | `WRITES_PER_USER_PER_MINUTE` | `429`, `Retry-After` |
| Checkout sessions, portal sessions, billing re-reads, per user | 10, 20 and 30 an hour | `CHECKOUTS_PER_USER_PER_HOUR`, `PORTAL_SESSIONS_PER_USER_PER_HOUR`, `BILLING_SYNCS_PER_USER_PER_HOUR` | `429`, `Retry-After` |
| LLM checks | 5 a minute per user; 200 a day for everyone | `LLM_CHECKS_PER_USER_PER_MINUTE`, `LLM_CHECKS_GLOBAL_PER_DAY` | `429`; the daily budget answers code `BUDGET_EXCEEDED` |
| Integration checks | 20 a minute per user; 1,000 a day for everyone | `DIAGNOSTICS_RUNS_PER_USER_PER_MINUTE`, `DIAGNOSTICS_RUNS_GLOBAL_PER_DAY` | `429` |
| A job started over HTTP | One run at a time; 60 s between starts | `JOB_MIN_SPACING_SECONDS` (0: only the one-at-a-time rule) | `409` while it runs; `429`, `Retry-After` when too soon |
| JSON request body | 1,000,000 bytes | `MAX_REQUEST_BODY_BYTES` | `413` |
| Requests per IP | Not set by the application | The platform rule, step D7 of [deployment.md](deployment.md#set-up-vercel) | `429` from the platform |

- **Windows.** A window is a fixed minute, hour or day of the clock, so a burst across a boundary can use up to twice the limit. A refused request counts too. Counters older than two days are deleted by `prune-expired`.
- **Whose counter.** A write is counted for the signed-in user (by Clerk user ID), whatever its outcome. A new account has an allowance of its own, so the per-IP rule, not this limit, is what holds back a crowd of accounts.
- **Not counted.** Reads. Checkout, the portal, the re-read, the LLM check and diagnostics have the limits above instead, so they are not counted twice. The Stripe and Clerk webhooks have no user and are authenticated by signature; the platform rule excludes them.
- **Your own routes.** A router from `pnpm new:module` counts its writes. A write route you add to another router needs the same dependency, or a limit of its own listed in `NOT_COUNTED` in `apps/api/tests/integration/test_write_limit.py`; that test fails for any other.

## Connection pools

Each API process opens up to `DATABASE_POOL_MAX_SIZE` request connections (`app_api`) and `DATABASE_WORKER_POOL_MAX_SIZE` system connections (`app_worker`: webhooks, jobs, rate limits, readiness, and event dispatch after the response). Requests use worker connections too, so the pools do not keep the two kinds of work apart; they keep a request that holds a request connection from waiting on its own pool. The request pool is 1 to 30, the worker pool 2 to 10 (billing synchronization uses all but one), and both together at most 30. Size them so that the maximum number of concurrent instances times both pool sizes fits your database pooler's client limit. With Supabase, use the Transaction pooler string.

**Thread budget.** Each process runs blocking work on 40 request threads. They are held by:

- admitted requests, at most `DATABASE_POOL_MAX_SIZE`;
- bounded external work: `DATABASE_WORKER_POOL_MAX_SIZE - 1` plus `LLM_MAX_CONCURRENT_CHECKS`, `BILLING_MAX_CONCURRENT_STRIPE_CALLS` and `CLERK_MAX_CONCURRENT_READS`, plus 5 for Clerk key-set lookups while `CLERK_JWT_KEY` is empty;
- calls waiting for a connection, which get what is left: 12 with the defaults, 17 with `CLERK_JWT_KEY` set.

Startup refuses settings that leave fewer than 8 waiter places and names the variables. Past the waiter places, a request gets a `503` at once (logged as `database_waiters_exhausted`). Not counted, because no limit caps them though they hold threads while they run: operator diagnostics and Clerk key-set lookups while no usable key set is cached. Cron job runs are not counted either, but each job runs at most once at a time ([Scheduled jobs](#scheduled-jobs)). Keep `DATABASE_POOL_MAX_SIZE` well below 40 ([architecture](architecture.md#data-access)).

## Integration diagnostics

The `/integrations` page and `/api/v1/diagnostics/*` expose configuration facts and call external services ([integrations.md](integrations.md#the-integrations-page)).

- **Access.** Only Clerk user IDs listed in `DIAGNOSTICS_OPERATOR_IDS` may use them. The one exception is `APP_ENV=local` with no operators listed. A session someone else started as the user (an `act` claim) is never an operator.
- **Production.** They are disabled unless `DIAGNOSTICS_ENABLED=true`.
- **When disabled or not permitted**, every diagnostics route returns `404`, except `GET /api/v1/diagnostics/access`, which answers any signed-in caller with `{"available": false}` and writes no audit entry.

## Incident response

1. **Contain**: revoke or rotate exposed credentials ([Key rotation](#key-rotation)), and disable affected features through configuration.
2. **Assess**: start from a request ID. A user who hits a `5xx` sees `Reference: <request ID>`; it is the `X-Request-ID` response header, also present in the logs and in Sentry. Then:
   - **Logs.** One JSON line per request, with the request ID, method, route template (never the real path), status and duration. A request that was authenticated also carries `user_id` and `org_id`, the internal UUIDs (never Clerk IDs), on every line; a request refused before it had a tenant context logs neither. Filter by `request_id`, `user_id` or `org_id`. An impersonated session logs `actor_session` and writes `app.audit_log.impersonator_clerk_id`.
   - **Sentry.** API errors appear in the API project and browser errors in the web project, tagged `request_id`; API events also carry `user_id` and `org_id`, and an expected `5xx` carries `error_code` (see [provider outages](#provider-outages)). Stripe configuration problems arrive as errors named `stripe_configuration_error`, `stripe_session_host_unexpected`, `stripe_webhook_unsupported_payload` and `billing_unknown_price`; rejected webhook signatures are logged at WARNING.
   - **PostHog.** Allowlisted product events only, identified by the internal user UUID (the distinct ID).
   - **What is scrubbed.** Logs and events never contain tokens, cookies, headers, request bodies, query strings or local variables, and secret-shaped values are redacted. Free text in an exception or log message is only pattern-redacted, so keep personal data out of messages.
   - **Telemetry can be late or lost.** After a response, the API gives Sentry and PostHog up to 2 s to send what the request queued; the log lines `telemetry_flush_incomplete`, `telemetry_flush_skipped` and `telemetry_flush_failed` say when it did not finish. Whether Vercel keeps an instance alive long enough is NOT VERIFIED. Sampled traces of a request can be lost when an instance is frozen; errors are not.
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
   1. Load synthetic data into the source project. `pnpm db:seed` refuses non-local databases by design, so apply `supabase/seed.sql`, or a larger synthetic set, with `psql` and the migration-role connection string (`MIGRATION_DATABASE_URL`, the Session pooler string).
   2. Record per-table row counts and checksums. The queries in `scripts/restore-drill.mjs` work against any PostgreSQL database.
   3. Restore a backup (or a point in time) into a **new** project. Never restore over the source. Then follow [Restore a dump into a new server](#restore-a-dump-into-a-new-server) for the roles, the owner of `app.lock_my_membership`, and the `app_login` password.
   4. Re-run the counts and checksums. Also confirm that RLS is still forced on every `app` table, and that the migration ledger matches.
   5. Record the elapsed time against the RTO, and the data-loss window against the RPO.
5. **Evidence.** Record the method, dates, plan, timings, and results in `dev-docs/evidence/`, labeled `sandbox`.
6. **Repeat** after major schema changes, and on a schedule you choose.

**Local restore drill.** `pnpm db:restore-drill` dumps the local Docker database with `pg_dump` and restores it into a fresh PostgreSQL server: a throwaway container from the same image, with no network, that has never run a migration. It creates the roles first, restores with `pg_restore --no-owner --single-transaction`, runs `supabase/restore/post-restore.sql`, and removes the container afterwards. It then verifies:

- per-table row counts and content checksums;
- row-level-security flags;
- object owners, privileges, and policies in the `app` schema;
- the attributes and memberships of the `app_api`, `app_worker` and `app_login` roles;
- the migration ledger.

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

## Performance testing

See [perf/README.md](../perf/README.md). Local runs are for relative comparisons only. Runs against deployed environments need an owner-approved run plan, and production load testing is prohibited without explicit authorization.

## Python runtime limitations

From the DEC-001 local proof ([evidence](../dev-docs/evidence/dec-001-vercel-python.md)). "Documented" values come from Vercel's documentation and have not been observed on the platform. "Local" values were measured on one development machine.

| Topic | Finding |
|---|---|
| Runtime | `python3.12` on x86_64. Vercel's runtime shim (`vercel_runtime`, with a vendored uvicorn) serves the ASGI app, and one instance serves concurrent requests. Local: verified in an Amazon Linux 2023 container. |
| Bundle | Local: 74.3 MiB uncompressed, installed from `uv.lock` with Linux wheels. Documented limit: 500 MB. `excludeFiles` in the root `vercel.json` keeps `tests/` and tool caches out. |
| Function root | Read-only. The API writes nothing there, and in a bundle it reads no `.env` file: configuration comes from environment variables only. |
| Cold start | Local, under x86_64 emulation: 2.2 to 6.6 s to the first response (p50 3.6 s; it varies with machine load). Natively, about 0.6 s. Platform cold starts are not measured: pre-warming and bytecode caching differ. |
| Duration | Documented: 300 s by default (the maximum on Hobby); up to 800 s on Pro and Enterprise. |
| Payload | Documented: 4.5 MB request and response bodies. The API rejects bodies over `MAX_REQUEST_BODY_BYTES` (1 MB) first. |
| Database | Use the transaction pooler. Every setting is transaction-local and prepared statements are off (local: verified with PgBouncer in transaction mode). A database or pooler that stops responding fails each call after the round-trip deadline (15 s by default). |
| Crons | With Services, only the root `vercel.json` crons are registered. |
