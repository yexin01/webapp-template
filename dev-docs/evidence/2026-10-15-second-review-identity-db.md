# Evidence: Second Review and Fixes — Identity and Database Kernel (2026-10-15)

| Field | Value |
|---|---|
| Scope | Every commit up to `dbf8a9b`, including the first review's fixes ([`2026-10-15-review-fixes.md`](2026-10-15-review-fixes.md)). This record covers authentication and identity, the request-scoped transaction, admission, the outbox, jobs, migrations, and the maintenance commands. Billing, LLM, the web app, scripts and CI, the performance harness, and the DEC-001 proof were fixed in a parallel session: see [`2026-10-15-second-review-billing-web-tooling.md`](2026-10-15-second-review-billing-web-tooling.md) |
| Method | Six independent adversarial reviews by area, then fixes, then a second-pass adversarial review of the fixes themselves. Each finding was reproduced (or traced in code) before it was fixed. Every regression test was **mutation-checked**: reverting the fix makes it fail. The parallel session's reviewers reported several of the same defects independently |
| Environment | Apple M5, macOS; Python 3.12 (uv), psycopg 3.3.6, PyJWT 2.15.1; FastAPI 0.143; PostgreSQL 17.6 in Docker (local) |
| Integration mode | **mocked** for every external provider. No `.env` exists. Clerk behaviour that the fixes rely on (the `sts` and `o` session-token claims) is taken from Clerk's documentation and is **not verified** against a real instance (DEC-006) |
| Remote actions | **None.** No push, deployment, publication, or remote setting change |
| Commits | `b7c10ea` (database kernel), `0b1eeb6` (identity) |

## Gates

| Check | Command | Result |
|---|---|---|
| Lint, format, import contracts | `pnpm api:lint` | PASS (8 contracts kept) |
| Strict types | `uv run mypy` | PASS (84 files) |
| API tests, these two commits alone | `uv run pytest` in a throwaway worktree at `0b1eeb6` | PASS: **439** |
| API tests, combined tree of both sessions | `pnpm test:api` | PASS: **466** |
| Contract drift | `pnpm contracts:check` | PASS (no API schema change) |

The full gate suite over the final tree is recorded in [§23](../engineering-implementation-plan.md#23-current-status-2026-10-15).

## Defects found and fixed

### Authentication and identity

1. **A JWKS outage stalled every token verification.** PyJWT's `PyJWKClient` holds one lock across each network fetch, even for cached keys, and starts its refresh cooldown only after a *successful* fetch. During a Clerk outage, a few tokens with unknown key IDs queued 5 s fetches behind that lock, and valid tokens with cached keys waited behind them (against §16.4). `JwksKeySet` replaces it:
   - cached keys are read without a lock;
   - refreshes are single-flight, at most one attempt per 5 s whether it succeeds or not;
   - the refresh ahead of expiry runs on its own thread, so no request waits for it;
   - a failed or unusable answer never replaces a good key set, and after the hour's lifespan verification fails closed (AUTH-04);
   - while the key set cannot be read, an unknown key ID is a `503`, not a `401`, because the key may have just been rotated in;
   - only RSA keys are kept (an EC key in the set used to turn into a 500).
2. **Malformed Clerk answers were 500s.** A non-JSON key set or membership list, or an unexpected shape, raised raw exceptions. They are now `503`. One unreadable membership item fails the whole read instead of silently dropping that membership. After a directory failure, reads fail fast for 5 s, and the per-page timeout is 5 s. The membership reconcile job isolates each user's failure.
3. **Concurrent misses each called Clerk.** The 30 s miss cooldown was check-then-act: 20 parallel requests for unknown organizations made 20 Clerk calls (shared instance quota). A miss now claims a refresh atomically, at most one per user per 5 s across instances (the kernel rate limiter), after the request transaction is released. A failed read does not start the 30 s cooldown, so it is retried within seconds.
4. **A new organization returned 404 for up to 30 s.** The session's active organization (`o.id` in Clerk's v2 token, `org_id` in v1) is now re-checked at once on a miss. The claim never grants access: the refreshed mirror decides.
5. **`user.deleted` did not advance the membership watermark**, so a slower, older read could restore a deleted user's memberships. It now stamps the watermark like a read that found none.
6. **Deadlocks (reported by the parallel session's reviewer):** the watermark lock was `FOR UPDATE`, which deadlocked with a privileged write's audit foreign key (`FOR KEY SHARE`); it is `FOR NO KEY UPDATE`. Organizations are upserted in a fixed order, so two users' refreshes cannot deadlock.
7. **Pending sessions** (`sts: pending`, a required step such as choosing an organization) were accepted; they are signed out (`401`), as Clerk's SDKs do by default.
8. **`MEMBERSHIP_MAX_STALENESS_SECONDS` accepted up to 86 400**, beyond the approved TEN-07 bound; it is capped at 300.
9. **An unauthenticated request took an admission place** before its token was checked. Routers now verify the token first (FastAPI still verifies it once per request).

### Database kernel, outbox, and maintenance

1. **A lost connection reported success.** After a pooler restart, a server-side termination, or a client deadline closing the connection, a caught error left the transaction status `UNKNOWN`, and psycopg left the block without `COMMIT` and without an error. Earlier blocks' work was lost behind a 2xx. Every block and the request commit now refuse to end as a success unless the transaction can commit; a lost connection is a `503`.
2. **A 500 committed its writes.** A non-domain exception after a write still committed the request transaction (and its outbox event), so a retrying client duplicated it. The scope now commits on success or a `DomainError` (keeping NT-07's denial audit) and rolls back otherwise. A failed context switch rolls back too.
3. **Admission was shared across event loops.** An AnyIO semaphore cannot wake a waiter on another loop, so a request could wait forever; Starlette's test client runs each request on its own loop. The gate is now per event loop. This was the likely cause of one hung test run.
4. **Threads waiting for connections starved scoped requests.** Admission bounds only scoped requests; unscoped routes (LLM, checkout, portal, diagnostics, and every block after `release()`) waited for an API connection on pool threads. 60 such requests made 35 of them fail and scoped latency reach 5.5 s. At most `40 − DATABASE_POOL_MAX_SIZE` threads may now wait (past that, an immediate `503`), the thread limit is set explicitly, and `DATABASE_POOL_MAX_SIZE` is capped at 30.
5. **Outbox:**
   - an expired lease was re-leased with its original position, so a crash-looping event blocked every drain for `OUTBOX_MAX_ATTEMPTS` runs (about 11 days with the daily job);
   - an event could run once more than `OUTBOX_MAX_ATTEMPTS`;
   - the 120 s lease was shorter than a function may run, so a live handler could run twice.

   Now only pending events with attempts left are leased; every drain, after-response dispatch included, first turns expired leases into retries with backoff, or `dead`; the lease defaults to 900 s (`OUTBOX_LEASE_SECONDS`); each outcome commits on its own; and pending events left without attempts by a lowered limit are dead-lettered. `OUTBOX_BATCH_SIZE` was unused and is removed.
6. **Maintenance commands (ENV-05):** `seed` had no confirmation flag; it now needs `--yes`. `seed` and `reset` passed with `APP_ENV` unset (it defaulted to `local`) and with any Unix socket, including a Cloud SQL proxy socket; they now need `APP_ENV` set to `local` or `test`, and only standard local socket directories count as local.
7. **Migrations** used server-side prepared statements (which break through a transaction pooler), no connect timeout, and no lock timeout. A migration statement now gives up after waiting 5 s for a table lock.
8. **Production without `CRON_SECRET`** silently never ran the jobs; startup now logs `jobs_endpoint_disabled`.

### Earlier changes judged correct

The first review's fixes in these areas were re-checked and are correct and needed: the key-set lifespan replacing PyJWT's unbounded key cache, unhandled exceptions handled inside the request middleware, `413` for chunked bodies, Svix `400`s, the identity settings checks, stamping membership reads before the Clerk call, refusing commit after a caught statement error, refusing later blocks after a rolled-back one, returning the admission place on `release()`, the shielded commit, the connect-timeout rounding and keepalives, batched pruning with a time budget, the migration advisory lock, out-of-order detection, and the ledger's RLS. They were incomplete in the ways listed above. No change was judged unnecessary.

## Not fixed: owner decisions and limits

- **Commit on `DependencyUnavailableError`.** A `503` raised outside a database block commits the request's earlier writes, like any `DomainError` and like unscoped routes. Rolling back instead is a one-line change.
- **Defaults to confirm:** outbox lease 900 s, migration lock timeout 5 s, 40 request threads (a constant), `DATABASE_POOL_MAX_SIZE` at most 30.
- **`Retry-After` on database `503`s.** Only `429`s carry it today.
- **Miss behaviour.** An organization that is not the session's active one can return `404` for up to 30 s after a successful membership read, and concurrent first misses within 5 s get `404` while one of them asks Clerk. Documented in `docs/integrations.md`.
- **Instance clocks.** Membership read times use each instance's clock; skew shifts the staleness bound by the skew. Documented.
- **`20261015000003_outbox_lease_index.sql`** builds its index without `CONCURRENTLY` (the runner wraps each migration in a transaction). Harmless before any deployment; on a large outbox it would block event inserts while it builds.
- **Not verified here:** Clerk's real token claims and JWKS behaviour, and anything that needs a sandbox, a preview deployment, or remote CI (DEC-001, DEC-006).
