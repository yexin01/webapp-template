# supabase/

The database files. This is not a Supabase CLI project (there is no `config.toml`): the migration runner in `apps/api/app/kernel/infrastructure/migrations.py` reads it.

- `migrations/`: the SQL migrations, ordered by version and checksummed in the ledger `public.app_schema_migrations`.
- `seed.sql`: deterministic synthetic fixtures for local development and tests. `pnpm db:seed --yes` loads them into a local database.
- `restore/post-restore.sql`: what to run after restoring a dump into a new server ([operations.md](../docs/operations.md#restore-a-dump-into-a-new-server)).

## Apply migrations only with `pnpm db:migrate`

Never run `supabase db push` or `supabase db reset`, and do not connect Supabase Branching to this repository. They keep a second ledger (`supabase_migrations.schema_migrations`) and re-apply every file, which bypasses the runner's checksums, lock timeout and ledger. A hosted database also needs `--allow-remote` and the owner's authorization: [setup.md](../docs/setup.md#supabase-optional-hosted-database), [operations.md](../docs/operations.md#migrations).

A merged migration is never edited: add a new one, numbered after the latest. Keep new objects in schema `app`.
