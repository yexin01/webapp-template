# Notes for the documentation integrator: package `db`

Branch `arch/db`. Evidence is labelled `local` (a command run on this machine against local containers, including
Supabase's Postgres image) or `mocked`. Nothing here is `sandbox` or `live`: no hosted Supabase project was contacted.

## What changed

| Area | Change |
|---|---|
| First migration | `GRANT ... TO CURRENT_USER` replaced by a named grant (`format('%I', current_user)`) in `20261014000001_core_schema.sql` and the restore drill's `ROLES_SQL`. The old form crashes Supabase images older than 17.6.1.152 (supautils < 3.2.3) when run as `postgres`. **One-time MIG-01 deviation:** the file was edited in place (pre-release, nothing shared had applied it, and a later migration cannot run after a first one that crashes). A guard test rejects `CURRENT_USER`, `SESSION_USER`, and `CURRENT_ROLE` as grantee in any migration, the module template, and `ROLES_SQL`. |
| Runtime login role | New `app_login` role (migration `20261030000010`), its assertion (`20261030000011`), `pnpm db:api-login`, optional `MIGRATION_DATABASE_URL`, a startup/readiness refusal in preview and production, an honest database probe. Details below. |
| TLS | `sslmode=require` is added for non-local hosts when the URL, `PGSSLMODE`, `PGREQUIRESSL`, and `PGSERVICE` say nothing about TLS (pool and migration connections). An explicit `sslmode`, `requiressl`, or `sslrootcert=system` is never overridden. |
| Migration ledger | `public.app_schema_migrations` is revoked from `anon`, `authenticated`, `service_role` (and `PUBLIC`) on every migrate. The code comment no longer claims RLS alone keeps it private. |
| Policies | `projects_api_access` (FOR ALL) replaced by `projects_api_select/insert/update/delete` (migration `20261030000012`); the module template does the same. The creator is checked on INSERT only. |
| Migration lint | squawk-cli 2.70.0 (released 2026-10-15), root `.squawk.toml`, run by `pnpm api:lint`, the CI `api` job, and `pnpm test:new-module`. |
| Supabase image | `scripts/test-supabase-image.mjs` (`pnpm test:supabase`) and a CI job `api-supabase`. Test fixtures that assumed a superuser administrator were fixed. |
| Restore | `supabase/restore/post-restore.sql`; the drill restores with `pg_restore --no-owner --single-transaction` and then runs that file. |
| DEC-001 proof | The packaged-runtime step now logs in as a role of its own, through PgBouncer, with `sslmode=disable` for the TLS-less local pooler. |

## New commands, scripts, and variables

- `pnpm db:api-login [--allow-remote]` = `python -m app.cli api-login`. Gives `app_login` LOGIN and a password: typed twice
  at a prompt, or from the environment variable `API_DB_PASSWORD` (a one-off value for this command, **not** a
  setting: do not put it in `.env`). At least 16 characters. The server receives only a SCRAM-SHA-256 verifier computed in
  the client; the password is never printed or sent in plaintext. Re-running rotates the password. A non-local
  target needs `--allow-remote`, exactly like `migrate`. It refuses a role that has SUPERUSER, BYPASSRLS, CREATEROLE,
  REPLICATION, or INHERIT. It prints, on stderr, where `DATABASE_URL` goes (for Supabase's shared pooler the role
  becomes `app_login.<project-ref>`), with `<password>` as a placeholder.
- `MIGRATION_DATABASE_URL` (optional, server only, in `.env.example`): the schema owner's connection for `db:migrate`,
  `db:seed`, `db:reset`, `db:api-login`. Empty: `DATABASE_URL`. The running API never reads it. It is in
  `check-bundle-secrets`' server-only list.
- `pnpm test:supabase` = `node scripts/test-supabase-image.mjs [pytest args]`. Env: `SUPABASE_POSTGRES_IMAGE`
  (default `public.ecr.aws/supabase/postgres:17.6.1.106`), `SUPABASE_TEST_CONTAINER`, `SUPABASE_TEST_PORT`.
- `pnpm api:lint` now also runs squawk.
- Python dev dependency `squawk-cli==2.70.0`.

## How the login role works (for docs/architecture.md, docs/operations.md, ADR-0001 note)

- Every request transaction already switches to `app_api` or `app_worker` with `set_config('role', ...)`. Before, the
  connection logged in as the migration role (on Supabase `postgres`: owns every table, BYPASSRLS), so an ordinary
  query containing `set_config('role', 'none', true)` or `RESET ROLE` got that role back. `app_login` is NOLOGIN until
  `db:api-login`, NOINHERIT, no BYPASSRLS/CREATEROLE/CREATEDB/REPLICATION, owns nothing, holds no privilege of its
  own, and is a member of `app_api` and `app_worker` only. Outside those roles it reads nothing in schema `app`
  (tests: `permission denied for schema app`).
- Migration `20261030000011` refuses an `app_login` that was created elsewhere with another shape.
- **Refusal in preview and production.** The first transaction of each process checks `session_user` once (cached for
  the process). If it is a superuser, has BYPASSRLS or CREATEROLE, is a member of such a role, or owns (or is a member
  of the owner of) a table, view, or materialized view in schema `app`, every database transaction fails with a 503 whose
  message names the fix ("Set DATABASE_URL to the app_login role (run pnpm db:api-login ...) and redeploy"), `/ready`
  reports not ready, `/health` stays up, and one ERROR log is written. The log and the response never contain the URL
  or the role name. Local and test may log in as a superuser (the shipped `.env.example` does). The check is not run per
  transaction (PERF-07).
- **Probe.** `GET /api/v1/diagnostics/integrations` / the `/integrations` page database check now reads `session_user`
  too. Preview and production: FAILED (`unsafe_session`) naming the fix. Local: PASSED with "Caution: ... allowed in
  local development only". It says "The login role cannot skip row-level security" only when that is true.
- **Honest limit (must stay in the docs).** `app_login` can still `SET ROLE app_worker`, and the worker's policies are
  `USING (true)` on users, organizations, memberships, billing, audit, and the project select. Code that can run
  arbitrary SQL (an injection, an adopter's raw psycopg use) can therefore still read across tenants. The fix removes
  the escape to the table owner, to BYPASSRLS, and to CREATEROLE, and makes a query without a role switch fail
  closed. The README must not imply RLS stops SQL injection. Closing the rest would need a second login role that is
  a member of `app_worker` only, with its own URL and pool (optional hardening, ARCH-11: not built).
  `test_the_login_role_can_still_switch_to_the_worker_role` documents it.

## Manual steps (owner / adopter)

Local development needs none: `.env.example` keeps the superuser `postgres`, which is allowed for `APP_ENV=local|test`.
An existing local database that applied the old first migration needs `pnpm db:reset --yes` (the checksum guard reports
`Migration 20261014000001_core_schema.sql was modified after being applied`). **Ask the owner before resetting theirs.**

Hosted Supabase (only with the owner's authorization for any remote effect):

1. Use the **session pooler** string for migrations (see Connection guidance):
   `MIGRATION_DATABASE_URL='postgresql://postgres.<ref>:<url-encoded password>@aws-0-<region>.pooler.supabase.com:5432/postgres'`
   exported in the shell. Do not put it in Vercel.
2. `pnpm db:migrate --allow-remote`. This also creates `app_login` (NOLOGIN).
3. `pnpm db:api-login --allow-remote`; type a new password (16+ characters) twice.
4. In Vercel, set `DATABASE_URL` to the **transaction pooler** string with the login role:
   `postgresql://app_login.<ref>:<url-encoded password>@aws-0-<region>.pooler.supabase.com:6543/postgres`. Never put the
   `postgres` URL in Vercel. Redeploy.
5. To rotate: re-run step 3, update `DATABASE_URL`, redeploy. The old password stops working at step 3, so expect
   failing requests until the redeploy finishes (a window; a second login role would avoid it). The `postgres`
   password is rotated separately in the Supabase dashboard.
6. After restoring a dump into a new server, re-run step 3: a dump holds no role passwords.

Optional, owner choice: Supabase "Enforce SSL on incoming connections" (Database Settings > SSL Configuration; or
`supabase ssl-enforcement update --project-ref <ref> --enable-db-ssl-enforcement --experimental`; both restart the
database); turn off the Data API (the template's browser never connects to the database); buy the IPv4 add-on only if
direct connections are wanted.

## Connection guidance for hosted Supabase (item 9: write into docs/integrations.md, docs/operations.md, .env.example comment)

- **Runtime `DATABASE_URL`:** Supavisor **transaction mode**, port 6543, user `app_login.<project-ref>`. The shared
  pooler is reachable over IPv4 on every plan.
- **Migrations (`MIGRATION_DATABASE_URL`):** the **session pooler**, same host, port 5432, user `postgres.<project-ref>`.
  The runner uses only transaction-scoped advisory locks and `SET LOCAL`, no prepared statements, so any pooler mode
  works for it. Supabase's direct connection (`db.<ref>.supabase.co:5432`) and the dedicated pooler are **IPv6-only**
  unless the project has the IPv4 add-on; Vercel and GitHub-hosted runners are IPv4-only, and so are many home
  networks. Use the direct connection only from a machine with IPv6 or on a project with the add-on. Replace the two
  "use a direct connection" sentences (docs/integrations.md steps 2 and 3, docs/operations.md around line 41).
- **Do not copy driver-only URL suffixes** (`?pgbouncer=true`, `?workaround=supabase-pooler.vercel`, `supa=...`,
  `connection_limit`, `pool_timeout`) into `DATABASE_URL`: libpq rejects them (`invalid URI query parameter`) and the
  validator's message wrongly blames percent-encoding. Not changed in this package (see Follow-ups).
- **TLS:** non-local hosts default to `sslmode=require`: no plaintext fallback, and no password sent to a server (or
  middlebox) that says it has no TLS. `require` does **not** check the server's certificate, so it does not stop an
  active man-in-the-middle; do not describe it as doing so. For that, download the Supabase CA (Database Settings > SSL
  Configuration), ship it with the API, and use `?sslmode=verify-full&sslrootcert=<path>` in both URLs. The path must
  exist inside the deployed function; on Vercel that means bundling the file with the API package. Not set up here.
  `PGSSLMODE` and a `service=` entry are respected and take precedence.
- `supabase/` is only a folder of migrations for this repository's runner; its ledger is `public.app_schema_migrations`.
  Tell adopters not to run `supabase db push`/`db reset` or connect Supabase Branching to this repository: they keep a
  second ledger (`supabase_migrations.schema_migrations`) and re-apply every file. Add a short `supabase/README.md`
  (owned by the integrator) and amend integrations.md line ~117 (the revoke list now includes `service_role`). Spec
  line 353 ("Local PostgreSQL: Supabase CLI") is superseded by `pnpm db:up`.

## Restore (docs/operations.md, README db:restore-drill row, spec §8.4)

The documented procedure for any target, including a Supabase project where the restoring role is not a superuser:

1. Create the roles with the migrations' role blocks (the drill's `ROLES_SQL`: `app_api`, `app_worker`, `app_login`,
   memberships by explicit name).
2. `pg_restore --no-owner --exit-on-error --single-transaction -d "$TARGET_URL" app.dump` (everything is owned by the
   restoring role, as at the source).
3. `psql "$TARGET_URL" -v ON_ERROR_STOP=1 --single-transaction -f supabase/restore/post-restore.sql` (hands
   `app.lock_my_membership` back to `app_worker`, using a temporary `CREATE` grant, as the first migration does).
4. Check that `app.lock_my_membership(uuid)` is owned by `app_worker`; run `pnpm db:api-login`.

The old advice "do not pass `--no-owner`" fails on a non-superuser target (`permission denied for schema app` on the
`ALTER FUNCTION ... OWNER TO app_worker`, one easily missed error, leaving the SECURITY DEFINER function owned by
`postgres`). After a Supabase dashboard-backup restore (`psql -f`, errors expected) run step 3 as well.

## Spec / ADR / matrix changes needed (integrator)

- §21 line ~1378: split MIG-03 (was PASS; now PASS (local) for the lint once the evidence is recorded), MIG-04 and DB-06
  hosted: **NOT VERIFIED** until a sandbox run against a hosted project (DEC-006); MIG-04 "dedicated migration role" is now
  `MIGRATION_DATABASE_URL`; the automated deployment step stays unbuilt (OPS-01 / DEC-001 decision; a
  `workflow_dispatch`-only workflow with a protected environment is the recommended shape).
- §18: record the MIG-01 in-place edit of migration 1 (date 2026-10-30), and a D-003: local and CI default to plain
  `postgres:17.6-alpine` instead of DEC-007's "Supabase CLI"; Supabase parity comes from `api-supabase` /
  `pnpm test:supabase`.
- ADR-0001: a clarifying note, not a change: DEC-008 option A is now carried out; the runtime role is `app_login`.
- docs/architecture.md roles paragraph; docs/operations.md rotation and migration-role text; data inventory: add
  `MIGRATION_DATABASE_URL` (secret, server only) and note `API_DB_PASSWORD` (one-off command input, not stored).
- docs/architecture.md "Adding a feature" step 3: to add editing, uncomment the column-level UPDATE grant in the
  generated migration for editable columns only (never `id`, `org_id`, `owner_user_id`, `created_by`, `created_at`),
  declare an update permission in `domain.py`, treat rowcount 0 as 404 like delete, and re-check the role inside the
  transaction if editing is limited by role (TEN-07). No API mapping for SQLSTATE 42501 is needed.
- docs/operations.md line ~42: `lock_timeout` bounds the wait for a lock, not how long it is held. A plain
  `CREATE INDEX` or table rewrite blocks writes for its whole duration, and the runner is transactional, so
  `CREATE INDEX CONCURRENTLY` cannot be used in a migration (squawk's `require-concurrent-index-creation` is switched
  off for that reason). Build such an index by hand with `CONCURRENTLY` first, then add the migration with
  `IF NOT EXISTS`.
- CI: the new job `api-supabase` pins its image inside `scripts/test-supabase-image.mjs`; Dependabot does not read that.

## Limitations

- No hosted Supabase project was contacted: hosted behaviour (Supavisor user routing `app_login.<ref>`, the shared
  pooler with SCRAM verifiers, supautils on the hosted image) is `NOT VERIFIED`.
- The Supabase image tested is `17.6.1.106` (the tag the installed Supabase CLI runs), which still has the supautils
  crash; `17.11.0.004` (the current CLI pin) was not pulled. Try it with `SUPABASE_POSTGRES_IMAGE`.
- 5 database tests are skipped on the Supabase image because they create or alter a SUPERUSER role, which `postgres` there
  cannot do; they run in the plain-PostgreSQL jobs.
- `CREATE INDEX CONCURRENTLY` is impossible in the runner (see above); there is no no-transaction migration mode.
- Squawk exits non-zero ("Failed to find files") if every migration is excluded; harmless while the new migrations exist.
- The lint skips the 8 old migrations by exact name, so a future adopter who squashes or removes migrations must edit
  `.squawk.toml` (a test keeps the exclusion list equal to the old files).
- The login check runs on the first transaction of a process: a process that was refused keeps refusing until restarted
  (redeploy after fixing `DATABASE_URL`).
- `with_tls_default` cannot see a `service=` file's sslmode, so such connections are left unchanged.
- Rotating `app_login` is not zero-downtime.
- The DEC-001 proof change was not run end to end (see Evidence).

## Decisions taken (reversible)

- `app_login` is one shared role for both pools (the briefing's "separate worker login" is documented as optional hardening, not built).
- The minimum password length for `api-login` is 16 characters; no generation, because a generated password would have to be printed.
- The guard refuses CREATEROLE as well as superuser/BYPASSRLS/ownership (the briefing listed it; the task text did not).
- The Supabase image script defaults to the locally verified tag, not the newest.
- The proof logs in as its own `dec001_login` role instead of `app_login`, so a developer's `app_login` password is never changed by a proof run.
- Migration file and role names are fixed (`app_login`); no `--role` option.

## Follow-ups (found, not done)

- Validator message for driver-only URL query parameters (`pgbouncer`, `workaround`, `supa`, `connection_limit`,
  `pool_timeout`): name the parameter instead of blaming percent-encoding (config.py `database_url_is_valid`).
- Hosted-migration workflow (`.github/workflows/db-migrate.yml`, `workflow_dispatch` only, protected environment): needs the
  owner's decision (DEC-001, OPS-01, MIG-05); not built.
- A schema-version readiness check was deliberately dropped (nothing consumes `/ready` on Vercel).
- A second, worker-only login role and pool (see the limit above).
- `supabase_migrations.schema_migrations` detection in `migrate()` (optional).
- Catalog lint tests proposed in deploy-ci-schema-lint (SECURITY DEFINER search_path, no PUBLIC grants, primary keys,
  foreign-key indexes) and a no-transaction migration mode for `CREATE INDEX CONCURRENTLY`: not built.
- Run `pnpm test:supabase` with `17.11.0.004` and add it to the CI job as a second matrix entry once someone with network access confirms it.

## Evidence

All `local`. Machine: heavy load from unrelated processes; PostgreSQL 17.6 (`arch-pg`, plain, superuser `postgres`) for the
default runs and `public.ecr.aws/supabase/postgres:17.6.1.106` for the provider-image runs.

| Command | Result |
|---|---|
| `pnpm test:supabase` (container `arch-db-supabase`, 127.0.0.1:54351) | PASS. Migrations twice as non-superuser `postgres` (11 applied, then none); anon/authenticated/service_role reach neither the ledger nor schema `app`; runtime and login roles have no unsafe attribute; `app_login` member of `app_api` and `app_worker` only; `lock_my_membership` owned by `app_worker`; `api-login`; `app_login` reads nothing in `app` outside the runtime roles; the startup check accepts `app_login` and refuses `postgres`; **464 passed, 5 skipped** (`-m db`, 534 deselected), 148 s. Container removed. |
| Same script with migration 1 reverted to `TO CURRENT_USER` | FAIL at the first migrate: `server closed the connection unexpectedly` (the supautils crash is caught). |
| Full API suite, plain PostgreSQL: `uv run pytest -q -p no:cacheprovider` (`TEST_DATABASE_URL` = `arch-pg`) | **1003 passed**, 195 s, no failures or skips |
| `pnpm api:lint` (ruff, format, import-linter, squawk), `uv run mypy` | clean (squawk: 0 issues in 3 files) |
| `pnpm test:new-module` | passed (three generated modules incl. migration lint, RLS tests with the new update checks, web tests) |
| `pnpm test:init-template`, `pnpm check:placeholders`, `pnpm contracts:check`, `check-bundle-secrets --self-test` | passed |
| `pnpm db:restore-drill` (source: a throwaway postgres:17.6-alpine compose project `arch-db-drill` on 54351, the repo's compose file was not used) | PASS, `mismatches: []`; with `post-restore.sql` emptied: FAIL (`_privileges`, `_owners`). |
| Restore by hand on the Supabase image (`pg_dump -n app -Fc`, as `postgres`) | old procedure: `permission denied for schema app` on `ALTER FUNCTION ... OWNER TO app_worker`; new procedure: ok, function owner `app_worker`, ACL `{app_worker=X/app_worker,app_api=X/app_worker}` equal to the source. |
| `proofs/dec-001/proof.py db --login-role ...` against `arch-pg` | created/migrated a database and `dec001_login`; `Database(enforce_login_role=True)` accepted it; `--drop` removed both. **The full `proofs/dec-001/run.sh` was NOT run** (it needs the developer's `pnpm db:up` database on 54329, off limits for this round), so the PgBouncer/runtime steps are untested here: `bash -n` and a Python parse only. |

Mutation checks (each reverted, test failed as expected): grantee guard (migration 1 and `ROLES_SQL` reverted), ledger
`service_role` revoke removed, TLS default removed (4 tests), migration 12 removed (`test_a_member_may_update_a_row_another_member_created`),
squawk rule `ban-drop-column` excluded, the login-role guard call removed (4 tests), `app_login` created with INHERIT (16 tests).

Not run: a hosted Supabase project, Vercel, the web app build and e2e (untouched).
