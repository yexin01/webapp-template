# Evidence: Cycle 1 Fix Round (2026-10-30)

| Field | Value |
|---|---|
| Scope | The fixes for an adversarial architecture review (cycle 1: 107 findings) made by eleven code packages (`db`, `stripe`, `apix`, `obs`, `web`, `config`, `onboard`, `deploy`, `caps`, `web2`, `test`), merged into `arch/integration` (`fed5c93`), and the documentation round that followed (this record, [ADR-0004](../adr/0004-cycle-1-adopted-decisions.md), the specification, `docs/`) |
| Method | Each package found its findings true or false against the code first, fixed what was true with a regression test, and checked that test by **mutation**: the fix was reverted, the test was seen failing, the fix was restored. The numbers below are the ones each package's note reports (`docs-notes/<package>.md`). They were produced on the package's own branch, before the later merges, and are not comparable with each other. None of them was re-run for this record. What was run on the merged tree is in [Verification of the merged tree](#verification-of-the-merged-tree) |
| Environment | macOS (Darwin arm64); Node 25.8.2 (the repository pins 24 in `.nvmrc`; `engines` allows 22.12 or newer), pnpm 9.15.0, uv 0.11.1, Python 3.12.13. PostgreSQL 17.6 (`postgres:17.6-alpine`) in a shared local container for the API tests; `public.ecr.aws/supabase/postgres:17.6.1.106` for the Supabase-image runs. The machine ran at a load average of 280 to 440 for most of the work, so timings are inflated |
| Integration mode | `mocked` (test doubles, loopback stubs, captured transports) and `local` (a command run on this machine against local services: a local database, a local container, a local build). **Nothing is `sandbox` or `live`.** No Clerk, Stripe, Supabase, Vercel, PostHog, Sentry, or LLM account was contacted |
| Remote actions | None intended. No push, deployment, publication, or remote setting change. Package registries (npm, PyPI, the npm advisory endpoint, Docker image pulls) were used by the builds and audits. One disclosed accident (`onboard`): during a manual `scripts/dev.mjs` run the real `stripe` CLI was on `PATH`, so `stripe listen --print-secret` ran once with a synthetic, invalid `sk_test_` key. Stripe rejected it; nothing was created or changed. The committed tests never use the real CLI |

## Labels

- `mocked`: a test double stands in for the provider (the in-memory provider, a loopback stub server, a generated key pair, a captured transport). It proves behavior against our model of the provider, never connectivity.
- `local`: a command run on one machine against local services. It is not `sandbox`.
- `sandbox` and `live` appear in this record only as things that were **not run**.
- `NOT RUN`: a test or command exists and was not run. `NOT VERIFIED`: no sufficient evidence exists. The two are never PASS.

## Cycle 1 findings by disposition

Counted from the review's disposition file (`coverage.md`, 107 lines; it is kept with the review's working files, not in this repository). The file has no "refuted" disposition: a finding that was found false in whole or in part is recorded under the package that checked it.

| Disposition | Count | Detail |
|---|---|---|
| Fixed by a code package | 59 | `db` 9, `apix` 8, `config` 8, `stripe` 8, `deploy` 6, `web` 6, `obs` 5, `onboard` 5, `test` 4. The `web` findings include the work the integrator split into `caps` and `web2` |
| Duplicate of another finding | 18 | Folded into a finding above: `db` 5, `config` 4, `test` 4, `deploy` 2, `web` 2, `stripe` 1 |
| Assigned to the documentation round | 19 | The next table |
| Deferred | 11 | The table after it |
| **Total** | **107** | |

Claims inside findings that a package found false or overstated (kept here because the file cannot say so): "an adopter following PostHog's guide gets analytics silently switched off" (`config`: both validators fail naming `NEXT_PUBLIC_POSTHOG_KEY` with the mode enabled, and analytics is off by design with it disabled); "the route manifest collected zero routes under FastAPI 0.143" (`apix`: it collects 22; no manifest fix was needed); "LLM-09 requires a 503 from `/llm/check`" (`test`: the 503 declared there is the database one). Not done: the post-sign-up landing page of `clerk-web-wiring-drift` (`web`: the verifier found it is not Clerk guidance), and the Clerk half of `config-provider-provisioning` (out of scope for the code packages).

### Findings assigned to the documentation round

| Finding | Documents |
|---|---|
| `config-deployment-variable-map`, `config-env-registry`, `deploy-runbook-env-matrix` | Specification (ENV-06 row), [DEC-001 record](dec-001-vercel-python.md) ("Secrets isolation" row), `docs/architecture.md` (cron plan note); the variable map, rotation steps and `.env.example` tags are user documentation |
| `clerk-personal-accounts-default-breaks-personal-workspace`, `onboarding-clerk-membership-optional-missing`, `docs-dx-clerk-personal-accounts` | Specification (DEC-006 Clerk row); the setup step is user documentation |
| `clerk-production-instance-steps-undocumented`, `clerk-first-run-automation`, `docs-dx-accuracy-defects`, `docs-dx-manual-steps-not-automated`, `docs-dx-no-linear-setup-guide`, `onboarding-no-single-human-steps-list` | User documentation (`README.md`, `docs/setup.md`, `docs/deployment.md`, `docs/integrations.md`, `docs/operations.md`, `docs/limitations.md`); `docs/data-inventory.md` carries the privacy toggles |
| `db-seed-reset-gaps` | `docs/architecture.md` ("keep new database objects in schema `app`"); the command-table rows are user documentation |
| `docs-dx-github-first-push` | Specification (REPO-09 and SEC-12 rows); the GitHub settings, the CodeQL licensing note, and a `CODEOWNERS` template are user documentation or follow-ups |
| `web-arch-rendering-model-undocumented` | `docs/architecture.md` (Frontend); [ADR-0004 §6](../adr/0004-cycle-1-adopted-decisions.md) |
| `obs-no-blocker-safe-relay` | [ADR-0004 §11](../adr/0004-cycle-1-adopted-decisions.md), `docs/data-inventory.md`; the PostHog proxy recipe is user documentation |
| `secarch-adm02-operator-approval` | Specification (DEC-014 `OPEN`, option (B) adopted by delegation 2026-10-31; ADM-02 row) |
| `secarch-edge-and-write-limits` | Specification (SEC-09 row); the firewall rule is user documentation |
| `stripe-plans-hardcoded-price-keyed-entitlement` | Specification (BILL-01 row, §23 next step 7) |

### Deferred findings

| Finding | Reason | Re-evaluate |
|---|---|---|
| `api-arch-bulkheads-at-ports` | Large kernel refactor across every provider call path | Cycle 2, once the base is stable |
| `api-arch-generated-crud-forks-security-code` | Needs kernel tenancy and pagination helpers and a generator rewrite | Cycle 2 |
| `api-arch-oversized-units` | Mechanical split with no behavior gain | Low value now |
| `api-arch-typed-events` | Low severity; handlers keep dict payloads | — |
| `web-arch-data-layer-copied-per-feature` | Large shared data-layer redesign | Cycle 2 or 3 |
| `web-arch-forms-errors-foundation` | The forms foundation is product-specific; document the extension point | — |
| `testing-adopter-test-kit` | Low severity | — |
| `obs-no-organization-groups` | Low severity; PostHog groups are an adopter analytics choice | — |
| `db-runner-transaction-only-no-online-index` | The verifier showed the proposed safeguards are unsound through poolers; the manual procedure is documented and squawk's concurrent-index rule is switched off meanwhile | — |
| `clerk-org-lifecycle-no-sandbox-test` | Cannot be written and verified without Clerk sandbox keys; the manual checklist stays | After DEC-006 |
| `secarch-supply-chain-gates` | Needs a pnpm major upgrade (release-age limits need pnpm 10.16 or newer) | Cycle 2 |

## Results by package

### `db`

Supabase-safe grants, `app_login`, per-command policies, squawk, Supabase-image CI.

Requirements: DB-06, DB-10, MIG-01, MIG-03, MIG-04 (new DB-11; D-003, D-004). All `local`.

| Command | Result |
|---|---|
| `pnpm test:supabase` (container on 127.0.0.1:54351) | PASS. Migrations twice as the non-superuser `postgres` (11 applied, then none); `anon`, `authenticated`, `service_role` reach neither the ledger nor schema `app`; runtime and login roles have no unsafe attribute; `app_login` is a member of `app_api` and `app_worker` only; `lock_my_membership` owned by `app_worker`; `api-login` works; `app_login` reads nothing in `app` outside the runtime roles; the startup check accepts `app_login` and refuses `postgres`. **464 passed, 5 skipped** (`-m db`, 534 deselected), 148 s |
| The same script with migration 1 reverted to `TO CURRENT_USER` | FAIL at the first migrate (`server closed the connection unexpectedly`): the supautils crash is caught |
| Full API suite on plain PostgreSQL | **1003 passed**, 195 s, no failures or skips |
| `pnpm api:lint` (ruff, format, import-linter, squawk); `uv run mypy` | Clean (squawk: 0 issues in 3 files) |
| `pnpm test:new-module`; `pnpm test:init-template`; `pnpm check:placeholders`; `pnpm contracts:check`; `check-bundle-secrets --self-test` | Passed |
| `pnpm db:restore-drill` (throwaway `postgres:17.6-alpine` project) | PASS, `mismatches: []`. With `post-restore.sql` emptied: FAIL (`_privileges`, `_owners`) |
| Restore by hand on the Supabase image (`pg_dump -n app -Fc`, as `postgres`) | The old procedure fails with `permission denied for schema app`; the new one works and the function owner and ACL equal the source |
| `proofs/dec-001/proof.py db --login-role ...` against the shared server | Created and migrated a database and `dec001_login`; `Database(enforce_login_role=True)` accepted it; `--drop` removed both. **The full `proofs/dec-001/run.sh` was NOT run** |

Mutation checks (each reverted, the test failed): the grantee guard on migration 1 and on the restore drill's role SQL; the ledger revoke from `service_role`; the TLS default (4 tests); migration 12 (`test_a_member_may_update_a_row_another_member_created`); the squawk rule `ban-drop-column`; the login-role guard call (4 tests); `app_login` created with `INHERIT` (16 tests).

Not run: a hosted Supabase project (Supavisor user routing `app_login.<ref>`, SCRAM verifiers on the shared pooler, supautils on the hosted image: **NOT VERIFIED**); the newer image `17.11.0.004`; Vercel; the web build and end-to-end tests. 5 database tests skip on the Supabase image because they alter a `SUPERUSER` role; they run on plain PostgreSQL.

### `stripe`

Webhook diagnosis, portal check, `setup:stripe`, re-read, DEC-013 option B, sandbox suite.

Requirements: BILL-06, BILL-07, BILL-12, BILL-20 (new BILL-22, BILL-23). `mocked` and `local`.

| Command | Result |
|---|---|
| `uv run ruff check ...`; `ruff format --check`; `lint-imports`; `mypy` | Pass; 146 files formatted; 8 contracts kept; no issues in 85 files |
| Full API suite, once | **1086 passed, 5 skipped** (the sandbox suite), 224 s |
| `pnpm --filter web lint`, `typecheck`, `format:check`; `pnpm --filter web test` | Pass; 28 files, **429 passed**, 1 skipped (pre-existing) |
| `pnpm contracts:check`; `pnpm check:placeholders`; `pnpm test:init-template` | Pass |
| `pnpm build` (placeholder Clerk key); `pnpm check:bundle-secrets` | The build succeeds; no secret-shaped string in the bundle |
| `pytest tests/sandbox -q -rs`, and with `STRIPE_SANDBOX_TESTS=1` but no key | 5 skipped with the reason; collects cleanly. **The enabled path was not run** |

Mutation checks: signed Thin payload and forged-signature logging; configuration-error mapping, portal policy, probe wiring, and the `billing_unknown_price` level; user re-read (`try_admit` against wait, last-slot reservation, Stripe-call bound, a shared bucket); checkout (expire before create, live re-read, retry replay, local refusal, failure to close, replaced-session cleanup, close on failed save); `setup-stripe` (key check, difference check, always create, key printing). The web tests were run green only, not mutated.

Not run: the end-to-end suite (needs `E2E_SANDBOX=1`); any call to Stripe; the sandbox suite; `setup-stripe`; `describe_portal` against a real portal configuration; expiring real sessions; the restricted-key permission needed for portal reads. All **NOT VERIFIED**.

### `apix`

Errors, impersonation, composition, authorization declarations.

Requirements: TEN-02, ADM-01, ADM-02, ARCH-04, ARCH-08, ARCH-13, OBS-02 (new API-07, ARCH-18, ADM-03; T-21). `mocked` and `local`.

| Command | Result |
|---|---|
| `ruff check`, `ruff format --check`, `lint-imports` (8 kept, 0 broken), `mypy` (84 files) | Clean |
| Full API suite | **974 passed**. A run before the last commit had 4 failures in `test_jobs_configuration.py` (`Container.diagnostics` was an `init=False` field that `dataclasses.replace` dropped); fixed by making it a cached property, then the suite passed |
| `pnpm test:new-module`; `pnpm contracts:check` | Passed; the OpenAPI document is unchanged |
| `mocked` (capturing Sentry transport, loopback Stripe stub) | A revoked Stripe key on `create_customer` returns `503 DEPENDENCY_UNAVAILABLE` and yields exactly one event: level `error`, tag `error_code`, tag `request_id` equal to the response header, the Stripe `AuthenticationError` in the chain, no canary key, token, or query secret in the event. Before the change: 503 and no event |

Mutation checks (the named tests failed): the commit rule `isinstance(error, DomainError)` only; the handler without the report call (5 tests); actor refusal off, actor not passed to the audit insert, `act` ignored, operator check without the actor (5, 2, 12 or more, 1 tests); probes from a hand-written list; override check, duplicate-probe check, settings keyword check, a duplicated settings field; the old per-organization membership loop (the query-count test saw `[8, 106, 106, 8]` round trips, now `[8, 8, 8, 8]`), no `ORDER BY` (`DeadlockDetected`), no de-duplication (`CardinalityViolation`); installed but unlisted packages injected into a service, the kernel core, a probe, and a router (`test_layer_imports` failed while `lint-imports` stayed green); a route without `requires(...)`, an `ANY_MEMBER` declaration on a path without an organization, `authorize()` removed from `ProjectService.list`, and the fail-closed check disabled.

### `obs`

Browser sampling, analytics facade, telemetry flush, error context, wire harness.

Requirements: OBS-03, OBS-04, OBS-05, OBS-06, OBS-07, OBS-08 (new OBS-10). `mocked` and `local`.

| Command | Result |
|---|---|
| `ruff`, `lint-imports`, `mypy` (84 files) | Clean |
| Full API suite, once | **1013 passed** (the `apix` base had 974) |
| `pnpm --filter web lint`, `typecheck`, `format:check`; `pnpm --filter web test` | Clean; 31 files, **418 passed**, 1 skipped (the pending "applied by eslint.config.mjs" test). The first run had one failure, a fixed 50 ms wait in the real-SDK direct-capture test on a loaded machine; fixed by polling |
| `pnpm build` (placeholder Clerk key); `pnpm contracts:check` | Succeeded; up to date |
| `mocked` (real SDKs, captured transports) | The browser SDK sends `sentry-trace: ...-0` and `sentry-sampled=false` for a rate of 0 and no flag without a rate; `sentry-sdk` inherits `-0` and samples an undecided request at its rate. The wire harness (`test_telemetry_wire.py`, about 2 s) decodes everything a loopback stub received (envelopes, gzip, PostHog batches) and finds no canary in raw, URL-encoded, or JSON-escaped form; a 500, a 503, a logged error, and a capture had arrived by the time the client had its answer |

Mutation checks: browser `tracesSampleRate: 0` back; the old `presentError` default; `before_send` without `allowlistEvent`; no buffering in `track` (5 of 6 pending tests); no discard on a user change; a cap of 25; no `posthog-js` pattern in the lint guard (3 tests); `ErrorState` reference for 4xx; browser `beforeSend` without `prepareReport` (3 tests); tenant never bound, or Clerk IDs bound (4 tests each); no Sentry ID tags; middleware never flushing; PostHog queue not marking the request; `scrub_event` not marking; unbounded wait and no single-flight lock; contributions not registered, PostHog ignoring the registry, sensitive property names allowed; in the wire harness: no `before_send_transaction`, no `before_send`, PostHog unfiltered, log extras unredacted, no flush.

Not run: whether Vercel keeps the instance alive long enough after the response for the flush (**NOT VERIFIED**; needs the DEC-001 preview); a Sentry dashboard check of transactions with `SENTRY_TRACES_SAMPLE_RATE` above 0.

### `web`

Default-deny pages, Clerk CSP hosts, silent refresh, operator tools, Clerk failure detection.

Requirements: AUTH-06, FE-05, FE-07, SEC-02, ARCH-03 (new FE-15). `local` and `mocked`.

| Command | Result |
|---|---|
| `pnpm --filter web lint` (0 findings, `--max-warnings=0`), `typecheck`, `format:check` | Clean |
| `pnpm --filter web test`, twice | 33 files, **514 passed**, 1 skipped (already on the base) |
| `pnpm build` (placeholder Clerk key); `pnpm check:bundle-secrets` | Compiled; `/`, `/billing`, `/billing/success`, `/dashboard`, `/integrations`, `/pro` still prerendered static; bundle clean |
| Account-free end-to-end run (through a lock wrapper that is not in the repository) | `mocked`: **40 passed, 20 skipped**. The skipped files need `E2E_SANDBOX=1` and Clerk keys: **NOT RUN**, including the two this package edited |
| `pnpm test:new-module` | Passed |

Mutation checks: a proxy that protects a fixed list (2 tests); `isPublicPage` always true (9 or more); `/pro` listed public (3); `/__clerk/(.*)` removed (2); prefix match without the `/` boundary (3); CSP without `:*`, without Protect in `frame-src`, without it in `script-src` (one table case each); silent refresh without the clone (2), without retry (3), without a first token (1), outside the deadline (1); the Clerk `status` branch removed (2); `OperatorOnly` always rendering (3), a provider granting on any answer (5), not tied to the user (1), probing while signed out (1).

Not run: `pnpm test:init-template`, the API suite, `pnpm contracts:check` (no API or contract change). The real redirect after Clerk loads and the Clerk-drawn controls: **NOT VERIFIED** (DEC-006).

### `config`

Platform binding, origins, validator parity, optional LLM, Sentry build checks, one root `.env`.

Requirements: ENV-01 to ENV-06, LLM-06, LLM-09, OBS-02 (new ENV-09). `local` and `mocked`.

| Command | Result |
|---|---|
| Full API suite, once | **1441 passed, 5 skipped, 2 failed**. The failures were not from this package: the route manifest reported `POST /api/v1/billing/sync: declares 0 authorization requirements` (`apix`'s manifest against `stripe`'s new route). Fixed on the integrated base by `cc786c4` |
| `ruff`, `ruff format --check`, `lint-imports` (8 kept), `mypy` (85 files) | Clean |
| `pnpm --filter web lint`, `typecheck`, `format:check`; `pnpm --filter web test` | Clean; **506 passed**, 1 skipped |
| `pnpm contracts:check`; `node scripts/check-bundle-secrets.mjs --self-test` | Up to date; passes |
| `pnpm build` (once, placeholder Clerk key); `pnpm check:bundle-secrets` | Succeeds; finds nothing, so the new PostHog pattern matches nothing in a real bundle |
| `next build` runs that must fail at config load | Failed as designed: `VERCEL=1` without `APP_ENV` and `CLERK_SECRET_KEY`; `SENTRY_AUTH_TOKEN` without `SENTRY_PROJECT` or `SENTRY_ORG`; a stray root `.env.local` and `apps/web/.env` |
| `pnpm test:init-template` | Failed with "original database port remains in `docs-notes/db.md`": not from this package, and fixed on the integrated base, where init removes `docs-notes/` |

48 mutation reverts each failed their regression test (platform rules, derivation, `APP_BASE_URL` required and origin, explicit modes, DSN, PostHog key and host, preview derivation and its gates, hostname filter, default parties, normalization, secret-key format, the `azp` log, its throttle and echo guard, the LLM disabled mode in five places and the web card, the web platform rules, `VERCEL=1` rules, serving, source-map problems and `silent`, `VERCEL_ENV` as a web variable, `.env` only, the stray-file names, the sandbox origin, the scanner pattern). One revert first survived: the conftest prefix list is not observable with a clean environment, so a dedicated test now checks it.

Not run: `proofs/dec-001/run.sh` (the proof's `.env` was generated with `proof.py env` and loaded through the API's `load_settings` and the web's `loadServerEnv` instead: `local`); the sandbox end-to-end change against real Clerk (inferred from the code).

### `onboard`

`pnpm dev` preflight, `check:setup`, account-free build, adopter repository.

Requirements: REPO-07, REPO-08, REPO-11 (new REPO-13). `local` and `mocked`.

| Command | Result |
|---|---|
| `ruff`, `ruff format --check`, `lint-imports`, `mypy` | Pass; 162 files formatted; 8 contracts kept; 86 files |
| Full API suite (before the last test edits) | 1298 passed, 5 skipped, **3 failed**: the two base failures above, and `test_env_example_placeholders_fail_clearly`, which was replaced afterwards |
| `pytest tests/unit/test_config.py tests/unit/test_doctor.py tests/integration/test_diagnostics_cli.py` (after) | 77 passed |
| `pnpm --filter web typecheck`, `lint`, `format:check`, `test` | Pass; 433 passed, 1 skipped |
| `pnpm test:scripts` | 35 passed (Clerk/env constants, preflight including the real Next loader, dev health, Stripe helpers including a fake `stripe`, local env, doc links) |
| `pnpm build:check` (the one build) | The build, the scan self-test, and the scan pass ("1 checked") |
| `next start` of that build with the placeholder Clerk pair, `curl -H 'Accept: text/html'` on `/`, `/sign-in`, `/sign-up`, `/integrations`, `/billing` | `307` to the Frontend API handshake on every page (`local`; not seen in a browser). So the web app cannot show a page without a real Clerk instance, whatever the placeholder says |
| `pnpm test:init-template`; `pnpm test:new-module` (with `TEST_DATABASE_URL`); `pnpm contracts:check`; `pnpm check:placeholders`; `pnpm check:links` | Pass |
| Manual `pnpm check:setup` against a throwaway database (mock modes); manual `scripts/dev.mjs` with fake `pnpm`, `uv`, `stripe` on `PATH` | Database PASS, the rest SKIPPED, evidence block and 6 manual steps printed; a dead API was detected after the shortened timeout, all servers stopped, exit 1, last error line shown; with a healthy API the signing secret reached the API process and not the web process, the listener started, and `whsec_` was masked |

Mutation checks: the `diagnose` production refusal; test events only with the flag; the Clerk row wording; placeholder detection in the API and the scripts; no-op adapters without the flag; mock counted as pass; the key in argv; unmasked listener output; `rk_` accepted; a non-200 treated as healthy; the secret leaking into the environment or the log; `requireClerk`; the policy re-applied to a finished copy; template-only blocks kept; links to removed files left; `AGENTS.md` not replaced; `--keep-template-material` ignored; the `.env.example` secret back to `replace_me`. `test:init-template` ran about eight times, `test:new-module` twice.

Not run: any page of the web app in a browser; a real `stripe listen`; `diagnose` against any provider; Windows (`dev.mjs` is macOS and Linux only); the end-to-end suites.

### `deploy`

Root `vercel.json`, `deploy-config` CI stage, gated migration workflow, pinned gitleaks.

Requirements: DEC-001 (local part), MIG-04, MIG-05, SEC-04, SEC-05, ENV-07, §16.1. `local`; packaging evidence only (TEST-04).

| Command | Result |
|---|---|
| `pnpm test:vercel-build` (CLI 65.2.0), first run | PASS: `vercel build` ok; the bundle scan compared 11 values and found none; 13 checks passed (74.5 MiB, 23 native libraries, all Linux x86-64; services `web` and `api`; crons equal the job registry; region `iad1`) |
| The same with `VERCEL_BUILD_PYTHON` set to the production-only environment (as CI runs it); a third run with a fake `SENTRY_AUTH_TOKEN` in the caller's environment | PASS, the same 13 checks (the token is removed from the build environment) |
| Full API suite | **1298 passed, 5 skipped**, 397 s; `ruff`, `lint-imports`, `mypy` clean |
| `pnpm test:new-module` | Passed |
| `uv run pytest tests/unit/test_vercel_config.py tests/unit/test_ci_workflows.py`; `tests/integration/test_outbox_jobs.py -k vercel_cron` | 19 passed; 1 passed |
| `python scripts/check_vercel_build.py self-test` | Passed: 11 planted defects caught |
| Production-only `uv sync --frozen --no-dev --no-install-project`, then `python -I -c 'import index, app.cli'` (the CI step) | Imports ok |
| `node scripts/check-bundle-secrets.mjs --self-test` | Passed; with the URL-password loop removed: FAILED (missed both database URLs) |
| `node scripts/npm-audit-bulk.mjs` (npm's public advisory endpoint) | No known vulnerabilities (213 package versions of 209 packages); `--self-test` passed; with the status check removed: FAILED (a 410 was accepted); a direct request for `lodash@4.17.15` returns 6 advisories. `pnpm audit --prod` the same day also found none |
| gitleaks 8.30.1 (darwin arm64, SHA-256 checked) over `--full-history HEAD`, and over all refs | 100 commits, no leaks; all refs: 303 commits, no leaks (4 findings before the `.gitleaks.toml` change, all public or inert values) |
| gitleaks `dir` on a generated planted key | Exit 1 (flagged); an `sk_live_` pattern is still flagged, `pk_live_` is allowed |
| The pre-commit hook in a scratch repository (no host gitleaks, Docker running) | A clean commit passes through the pinned image with no network; a planted key is refused with the value redacted; Docker stopped, and neither present: each refuses with its own message |
| The migration job's commands in a fresh `git archive` copy with no `node_modules` (`uv sync --frozen --no-dev`, then `UV_NO_DEV=1 MIGRATION_DATABASE_URL=<scratch database> pnpm db:migrate --allow-remote`) | Applied all migrations. The workflow itself was **not run on GitHub** |
| `vercel build` region experiment (59.17.0, scratch project outside the repository) | A top-level `regions` leaves no field in the function; a per-function `regions` is present; an invalid code and an unknown key are not rejected |

Mutation checks (13 caught by the tests above): a `pull_request` trigger, an unpinned action, `environment: production`, `contents: write`, an echoed secret, a gitleaks version mismatch, no scanner self-test, a secret in the `deploy-config` job, an invalid region, a changed cron schedule, the `tests/**` exclusion removed (FAIL `no_test_files_or_caches`: 71 files from `tests/` in the bundle), swapped rewrites, a different hook image.

Not run: `pnpm test:e2e`, `pnpm audit --dev`, GitHub Actions (the workflows are validated as YAML and by the tests above, not by `actionlint` or on GitHub), the full DEC-001 proof (`bash -n` only), any Vercel, GitHub, or Supabase call.

**What a build cannot show.** The build output places `apps/web/src/proxy.ts` in the web service as the Node.js function `_middleware.func` with `"middlewarePath": "/_middleware"` on the matched routes. Whether the platform honors that inside a service is **NOT VERIFIED** ([DEC-001 record](dec-001-vercel-python.md#nextjs-proxy-under-services-not-verified)).

### `caps`

The project list says what the caller may do.

Requirements: ARCH-03. `local` and `mocked`.

| Command | Result |
|---|---|
| `uv run pytest tests/integration/test_project_capabilities.py` (shared database) | 7 passed |
| Related existing tests (`tests/architecture`, `test_authorization.py`, `test_api_security.py`, `test_project_write_rechecks.py`, `test_query_counts.py`, `test_platform.py`) | 138 passed |
| `ruff check`, `ruff format --check` (173 files), `mypy` (86 files), `lint-imports` (8 kept) | Clean |
| Full API suite, after the last API change | **1532 passed, 5 skipped**, 4 min 37 s |
| `pnpm contracts`, then `pnpm contracts:check` | Up to date |
| `pnpm --filter web lint`, `typecheck`, `format:check`; `pnpm --filter web test` | Clean; 40 files, **650 passed**, 1 skipped (new or rewritten: `projects-panel.test.tsx` 36, `workspace.test.tsx` 6, `use-active-tenant.test.tsx` 4, `no-client-roles.test.ts` 9) |
| `pnpm test:new-module` (final templates) | Passed, 2 min. A typical, a one-letter, and the longest module name pass lint, types, contracts, architecture, API with RLS, and web tests; one generated module ran 24 API tests (6 new) and 25 web tests |
| `pnpm build:check` (placeholder Clerk key); `pnpm check:placeholders` | Compiled, `/dashboard` still static, scan found nothing; clean |

Mutation checks: the specification's mutation, `PROJECT_DELETE` granted to `ALL_ROLES` with no web change, flipped the member's `can.delete` (`test_the_list_says_what_the_caller_may_do[organization member]` failed with `{'delete': True} != {'delete': False}`, and `test_every_page_carries_the_decision`) while the personal, admin, and changed-permissions cases passed; `delete` re-derived from the role (the changed-permissions test failed); `create=True` hard-coded; `create` and `delete` swapped (3 tests); in a scratch copy of the generator output: re-derived `delete` (1) and swapped permissions (2); panel: Delete always shown (4), form always shown (5), hint always shown (1), tenant gate removed (1), remount key removed (2), Clerk-unavailable branch removed (1); generated panel: 3, 3, 1, 2, 1; a scratch source file with `membership?.role === "org:admin"` made `no-client-roles.test.ts` fail, naming the file.

Not run: `pnpm test:init-template`, the end-to-end suites (the sandbox flows need Clerk keys).

### `web2`

Client page gate, authorized parties, diagnostics access probe.

Requirements: FE-05, AUTH-01 (`azp`), ADM-01, TEN-02, ENV-03 (new FE-15, FE-16). `mocked` and `local`.

| Command | Result |
|---|---|
| `pnpm --filter web lint`, `typecheck`, `format:check` | Clean (`--max-warnings=0`) |
| `pnpm --filter web test`, once, after all changes | 41 files, **789 passed**, 1 skipped (already on the base) |
| `pnpm build` once (placeholder Clerk key, `APP_BASE_URL`, `CLERK_AUTHORIZED_PARTIES`); `pnpm check:bundle-secrets` | Compiled; the seven page routes are `○ Static`; the parties are inlined in the proxy bundle and in nothing the browser receives; clean |
| `pnpm contracts`, then `pnpm contracts:check` | Regenerated in its own commit (`DiagnosticsAccess`, `/api/v1/diagnostics/access`); up to date |
| `apps/api`: `ruff check`, `ruff format --check`, `lint-imports` (8 kept), `mypy` (86 files) | Clean |
| Full API suite, once | **1651 passed, 5 skipped** (the opt-in Stripe sandbox suite), 336 s. The two route-manifest failures that `config` reported no longer occur |
| `uv run pytest tests/unit/test_config_cases.py` and `vitest src/lib/env-cases.test.ts` | 116 passed in each app (44 cases and 72 origins) |
| `test_diagnostics_access.py` (unit and integration) | 34 passed |
| `pnpm check:placeholders` | Ok |

Fidelity check of `browserOrigin` (one-off, not committed): 72 addresses through the API's `http_origin` and the port; the first version differed on two IPv6 forms and was fixed; now identical. The 72 are the committed table.

Mutation checks: the gate (no `<PageGate>` in the layout: 7 tests; a gate that lets every page through: 14; no redirect: 11; every path protected: 11; no per-path key: 1; no "shown" latch: 2; an unchecked return path: 12; a second root layout in a route group; a page listed in `PUBLIC_PAGES` only); the parties (the proxy ignoring the option: 1; `next.config.ts` not passing the value: 2; the branch URL not derived: 4; derived outside a preview deployment: 3; and nine more); the probe (an always-true operator policy, the local opening lost, diagnostics on in production, an impersonator allowed, the enabled flag ignored, an audited refusal, a spent rate-limit counter, a response that names an operator count, the listing on a different policy, and five web-provider variants), each failing a test. Survivors: dropping the `@`, backslash, and `%` pre-checks of `browserOrigin` changed nothing because the structural regex already refuses those addresses; the redundant pre-check was removed.

Not run: `pnpm test:vercel-build`, `pnpm test:new-module`, `pnpm test:init-template`, every end-to-end suite. Clerk's real session state around sign-out, organization switches, and `isLoaded` is read from the SDK, not observed: all gate tests mock Clerk.

### `test`

`pnpm verify`, `pnpm verify:sandbox`, the account-free browser tier, response conformance.

Requirements: REPO-03, TEST-03, API-02, FE-01, FE-05, FE-07, FE-09, FE-11 (new API-08). `mocked` and `local`.

| Command | Result |
|---|---|
| `uv run pytest --require-db -q`, full API suite, run 1 (before the exemption markers) | 1536 passed, 5 skipped, 1 failed (`test_doctor.py`, merge skew, fixed) and 3 responses reported by the new session check (one synthetic-error test, exempted) |
| The same, run 2 | **1539 passed, 5 skipped**, 4 min 27 s; the session check reported 1 response (the second synthetic-conflict test); that case is now marked; `test_outbox_jobs.py`, `test_request_scope.py`, and the contract tests then passed (80) with nothing reported. A third full run was not made |
| `node scripts/verify.mjs --only api-lint ... licenses --keep-going` (15 steps) | All PASS, 25.8 s: web **647 passed, 1 skipped** (648), scripts **95 tests, 95 pass** |
| `TEST_DATABASE_URL=... node scripts/verify.mjs --only init-template --only new-module --only build` | All PASS, 1 min 15 s |
| Account-free end-to-end run (lock wrapper outside the repository), run 1 with new tests and `--retries=0` | `mocked`: 56 passed, 14 skipped (the 14 are sandbox tests) |
| Run 2: `pages.spec.ts` three times plus 7 temporary mutants | `pages` 48 passed, no flake; every mutant failed with its intended message except one that the first version of the guard missed (a skipped handshake; fixed) |
| Run 3: full suite plus the mutants | 58 passed (the 56 real tests, plus the skipped-handshake mutant on both projects, which the guard still missed), 12 failed (6 mutants on 2 projects), 14 skipped |
| Run 4, final, committed state, `--retries=0` | **56 passed, 14 skipped**, 29.5 s |
| `pnpm verify:sandbox` with no `.env` | All three parts SKIPPED, the evidence record printed, exit 0 |
| `pnpm contracts:check`; `pnpm check:placeholders`; `pnpm check:links`; `pnpm install --frozen-lockfile --offline` after the hand edit of `pnpm-lock.yaml` | Pass; "Lockfile is up to date" |

Merge skew found by the new commands (test-only fixes, no behavior change): three tests passed in their own packages and failed once merged (`apps/web/src/lib/env-example.test.ts`; `scripts/tests/preflight.test.mjs`, found by `pnpm verify`; `apps/api/tests/unit/test_doctor.py`, found by the full API run). The new session check found tests whose responses did not match the OpenAPI document because they patch a route to raise an error it cannot raise (`test_request_scope.py`: a 409 and a 410; and the conflict case of `test_outbox_jobs.py`); both are now marked `contract_exempt`. Before the declarations were added, `GET /orgs/{org_id}/projects` could answer an undeclared 503, and errors were declared as `application/json` but sent as `application/problem+json`.

Mutation checks: the web unit tests for the sandbox runner (the switch accepting any value, the loader taking every variable, no precondition throw, parallel workers, no setup dependency, a placeholder key that drifts from `.env.example`, the unauthorized origin, live keys accepted, any e-mail accepted, a race in `ensureTestUser`, a required password, the unnamed Membership setting); the browser tier (a skipped handshake, aborted app scripts, a blocked script, a 404 asset, an uncaught error, an injected axe violation); `verify` (`--quick` ignored, no fail-fast, `--require-db` dropped, no database check, the end-to-end switch and the Stripe suite not pinned off, skips hidden in the verdict, a failure that exits 0, credentials kept, the Supabase job in the default set); `verify:sandbox` (a restricted key accepted, a remote database accepted, zero passed counted as a pass, a key or password echoed in three ways, a failure that exits 0, a blocked part that does not fail); the API (`--require-db` dropped, `pytest.fail` instead of `pytest.exit`, the URL printed); the checker, the recorder, the session hook, and eight edits of the committed document, each failing the test that should. Flakes: none found in the code under test.

Not run, and **NOT VERIFIED**: anything against Clerk, Stripe, or any provider, including the sandbox end-to-end run (the token fetch, user creation through the Backend API, the ticket sign-in and that Device Trust does not apply to it, the `currentTask` property names used to detect a pending session, and every signed-in flow); `pnpm audit` and `pip-audit` (network); gitleaks (not installed there); `pnpm test:supabase` (Docker); **`pnpm verify` as one command**.

## Verification of the merged tree

Only what the documentation agent for the maintainer documents ran, on `arch/docs-maint`: the merged tree `fed5c93` plus the documentation commits. Label `local`; nothing here contacts a provider. Run from the repository root unless a directory is named, one command at a time, with the machine at a load average of about 280. The shared test PostgreSQL 17 was `127.0.0.1:54360`.

| Command | Result |
|---|---|
| `pnpm check:links` | Before my edits: FAIL on one link, `dev-docs/evidence/dec-001-vercel-python.md -> ../../proofs/dec-001/vercel.json` (the `deploy` package moved that file to the repository root); fixed by pointing the link at the root `vercel.json`. After my edits: FAIL on one link, `SECURITY.md -> docs/limitations.md`, a file that the other documentation agent creates; it resolves when the two branches are merged |
| `pnpm check:placeholders` | Exit 0 (template mode: `{{SECURITY_CONTACT}}` and `{{CONDUCT_CONTACT}}` present, as expected) |
| `pnpm test:scripts` | **95 tests, 95 pass**, 0 fail, 4.1 s |
| `pnpm contracts:check` (with `UV_FROZEN=1 UV_OFFLINE=1`) | "API contracts are up to date.", 3.3 s: the committed OpenAPI document and generated types match the merged API code, including after the integrator's commits `d1e29f9` and `dcc2aea` |
| `pnpm test:init-template`, with the files this branch adds staged and a one-line stand-in for the missing `docs/limitations.md` staged and then removed | Passed, 6 s. The first run, before the new evidence record was staged, failed only on links to it (the copy holds tracked files). It also shows the failure that `config` and `deploy` reported, "original database port remains in `docs-notes/db.md`", is gone: init removes `docs-notes/` |
| `uv run --offline --frozen pytest -q -p no:cacheprovider tests/integration/test_platform.py -k manifest` (`apps/api`, `TEST_DATABASE_URL` set) | **4 passed**, 53 deselected, 0.7 s: every route declares what authorizes it (TEN-02). The API suite has no test that reads the specification's traceability matrix; this manifest test is the nearest requirement-level check |
| `uv run --offline --frozen pytest -q -p no:cacheprovider tests/unit/test_analytics_events.py` | **14 passed**, 0.7 s (its docstring names `docs/data-inventory.md`; it reads no document) |

Consistency checks of the documents themselves, with a throwaway script that is not in the repository: every requirement ID defined in the specification (178) appears in the traceability matrix and nothing else does; and every repository path, `pnpm` script, and environment variable named in the documents I edited exists in the tree. The only names that do not resolve are files of the other documentation agent (`docs/setup.md`, `docs/deployment.md`, `docs/limitations.md`), build-output and platform names (`services/web/functions/_middleware.func`, `VERCEL_REGION`), image names, and `STRIPE_PRODUCT_PRO`, the setting that option (B) of the retained-price choice proposes and that does not exist.

What these do not show: the full API and web suites, `pnpm verify`, the production build (`pnpm build:check`), and every end-to-end suite were not run on the merged tree (the round's instructions limited me to small commands on an overloaded machine). The package counts above stand as each package reported them.

## Not run, and why

| What | Why | What would verify it |
|---|---|---|
| Every sandbox suite: `pnpm test:stripe-sandbox`, `pnpm verify:sandbox`, the signed-in end-to-end tests, `pnpm check:setup` against real providers | No Clerk, Stripe, PostHog, Sentry, or LLM keys exist in this environment | The owner pastes sandbox keys into `.env` and runs `pnpm verify:sandbox`; record the printed record here as `sandbox` (DEC-006) |
| A Vercel preview deployment | Needs the owner's authorization and a Vercel account | The first-preview checks in [ADR-0004 §1](../adr/0004-cycle-1-adopted-decisions.md): `curl -sI https://<preview>/dashboard` signed out, then sign in and open `/dashboard`; then `proofs/dec-001/README.md` ("Probing a preview") |
| Whether the Next.js proxy runs inside a Vercel service; Framework Preset = Services; crons from the root file; the router's order; `VERCEL_REGION`; limits; Instant Rollback with crons; the shared environment | Only the platform can show them | The same preview, and one production deployment for the crons |
| A hosted Supabase project (Supavisor routing for `app_login.<ref>`, SCRAM verifiers, supautils on the hosted image, the IPv4 add-on, TLS) | No hosted project; the image runs are local | `pnpm db:api-login --allow-remote` and the hosted connection steps with a non-production project, then `pnpm test:supabase` with `17.11.0.004` |
| The full DEC-001 proof, `proofs/dec-001/run.sh` | Needs Docker Desktop, about ten minutes, and the developer's database on the default port, which was off limits for this round | Run it once on a quiet machine after `pnpm db:up` |
| `pnpm verify` as one command, `pnpm verify:quick`, `--job supabase` | Not run end to end by any package; `pnpm verify` takes the sum of its steps (the API suite 4.5 to 6 minutes on an idle machine) | Run `pnpm verify` on an idle machine with Docker and Chromium installed |
| `pnpm test:e2e` with sandbox keys; Clerk's real session behavior; the Clerk-drawn controls under axe | Needs Clerk development keys | `E2E_SANDBOX=1 pnpm test:e2e`, or `pnpm verify:sandbox` |
| GitHub Actions (every workflow), CodeQL, the secret scan in CI, the migration workflow, branch protection | Nothing was pushed; the workflows are checked as YAML and by unit tests | Authorize a push, then read the first CI run |
| `pnpm audit --dev`, `pip-audit`, gitleaks of the working tree on this machine | Network, or gitleaks not installed | `pnpm verify` with network and gitleaks |
| Coverage (TEST-03) | No coverage tool is installed | Add `pytest-cov` and `@vitest/coverage-v8`; not built |
| The strict web lint guards | Wiring them means editing `apps/web/eslint.config.mjs`, which agents may not change (§23 step 4) | The owner applies the change |
