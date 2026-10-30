# Notes for the documentation integrator: package `deploy`

Branch `arch/deploy`, cut from the integrated `apix`, `db`, `stripe`, `web`. Evidence is labelled `local` (a command run
on this machine, including `vercel build` with the pinned CLI and a local gitleaks) or `mocked`. Nothing here is
`sandbox` or `live`: no Vercel project, GitHub Environment, or hosted database was contacted, and nothing was deployed.

## What changed

| Area | Change |
|---|---|
| Topology (DEC-001, adopted B) | **Root `vercel.json`** = one Vercel project with Services: `web` (`apps/web`) and `api` (`apps/api`, entrypoint `index.py`), `/api/v1/(.*)` rewritten to `api` before the `/(.*)` catch-all, the four crons, and the API function region. `apps/api/vercel.json` (crons only, ignored by Services, no `excludeFiles`) and `proofs/dec-001/vercel.json` are gone. |
| API bundle | `services.api.functions["index.py"].excludeFiles` removes `tests/**`, `.venv/**` and the pytest, mypy and ruff caches. Without it, `vercel build` shipped 71 files from `apps/api/tests/` (local evidence below). |
| Region | `services.api.functions["index.py"].regions = ["iad1"]` (Vercel's default, a placeholder for the adopter's database region). **Placement was checked, see Services facts.** |
| Tests | `apps/api/tests/unit/test_vercel_config.py` (Services layout, routing order, one valid region, exclusions, no second `vercel.json`), the cron parity test in `test_outbox_jobs.py` now reads the root file, `apps/api/tests/unit/test_ci_workflows.py` (workflows, below). |
| DEC-001 proof | `proofs/dec-001/run.sh` builds the committed root `vercel.json` (it exits if `HEAD` has none) instead of copying a candidate. Nothing else changed. Its README row 1 follows. |
| CI `deploy-config` job | New job (spec 16.1 "Deployment configuration"): production dependencies installed without the dev group import `index` and `app.cli`, then `pnpm test:vercel-build`. |
| `pnpm test:vercel-build` | `scripts/test-vercel-build.mjs` + `scripts/check_vercel_build.py`: copies the working tree, runs the **pinned `vercel` CLI** (`vercel@65.2.0`, an exact root devDependency, published 2026-10-22) with no token, organization, project, or telemetry, builds the web service with generated canary values for the server-only variables, requires `check-bundle-secrets` to compare them, and asserts the output (below). |
| Migration gate | `.github/workflows/db-migrate.yml`: `workflow_dispatch` only, choice input `preview`/`production` = the GitHub Environment, secret `MIGRATION_DATABASE_URL`, runs `pnpm db:migrate --allow-remote`. No automatic migration on deploy. |
| Secret scan | `gitleaks-action` replaced by the pinned gitleaks CLI 8.30.1 (SHA-256 checked, self-test with a generated planted secret, full history reachable from the checked-out commit). The pre-commit hook falls back to the same version as a container image pinned by digest. `.gitleaks.toml` allows publishable keys (`pk_test_`/`pk_live_`) and the bare RS256 JWT header. |
| Bundle scan | `check-bundle-secrets.mjs` also compares the password inside `DATABASE_URL` and `MIGRATION_DATABASE_URL` (as written and decoded) and `API_DB_PASSWORD`; `--require-values` fails a scan that had nothing to compare. Self-tested. |
| npm audit | Production audit moved off the legacy endpoint: `scripts/npm-audit-bulk.mjs` (self-test with a local endpoint) replaces `pnpm audit --prod` in CI and `pnpm security:audit`. |
| CI | `cancel-in-progress` is true for pull requests only (a push to `main` no longer cancels the previous run). |

## What `check_vercel_build.py` asserts (CI job `deploy-config`, `pnpm test:vercel-build`)

Both services are declared; the first matching route sends `/api/v1/health` and `/api/v1/jobs/drain-outbox` to `api` and `/`,
`/dashboard`, `/api/other` to `web`; the built crons equal the committed ones **and** the API's job registry; exactly one API
function whose `.vc-config.json` has `runtime` `python3.12`, `architecture` `x86_64`, and `regions` equal to the committed
single known region; no `tests/` or tool-cache files in it; every native library is ELF x86-64 by its header (Mach-O fails);
under 500 MB; the web service built. `check_vercel_build.py self-test` plants eleven defects (a `tests/` file, a cache file, a
Mach-O library, a missing and an extra cron, a wrong region, swapped routes, one service, Python 3.11, arm64, an unscheduled job) and
fails unless each is caught. This is **local packaging evidence, not platform evidence** (TEST-04).

## New commands, files, variables

- `pnpm test:vercel-build [--keep]`. Needs `pnpm install`, `uv`, network access to npm and PyPI. Env `VERCEL_BUILD_PYTHON`:
  an interpreter with the API's runtime dependencies (CI passes the production-only environment; default
  `uv run python` in `apps/api`).
- `python scripts/check_vercel_build.py check --root <export> --output <export>/.vercel/output` and `... self-test`.
- `node scripts/npm-audit-bulk.mjs [--self-test]`; `node scripts/check-bundle-secrets.mjs --require-values`.
- Dev dependencies: `vercel@65.2.0` (root `package.json`), `pyyaml>=6,<7` (API dev group, for the workflow tests; already
  in the lock through `uvicorn[standard]`).
- GitHub secret (per Environment): `MIGRATION_DATABASE_URL`. No new `.env.example` variable.
- Pins that **Dependabot does not read**: gitleaks version and SHA-256 (`ci.yml` `secrets` job env), the gitleaks image
  digest (`scripts/git-hooks/pre-commit`; a test requires both to name the same version). Bump procedure below.

## Services facts (what was verified, and how)

Sources: vercel.com/docs/services (updated 2026-08-10), /docs/services/config-reference (2026-06-30), /docs/services/routing
(2026-06-30), /docs/build-output-api/services (2026-09-15), /docs/project-configuration/vercel-json (2026-08-14), /docs/regions
(2026-08-11). The Vercel plugin skill `vercel-services` agrees.

- **Beta, all plans.** "Services are available in Beta on all plans." The earlier `experimentalServices` model is documented
  separately and "replaced for new projects" by `services`. **`experimentalServicesV2` in the build output
  (`.vercel/output/config.json`) is the CLI's name for the same thing**; it is not a separate maturity tier (CLI 59.17.0
  rejects mixing the two keys; 65.2.0 still prints the V2 name). The DEC-001 evidence record recommended B only "provided Services
  is generally available": that condition is **not** met, so adopting B accepts Beta.
- **Schema used.** `services.<name>.root` (required, relative to `vercel.json`), `entrypoint` (`index.py`; the FastAPI app is
  `app` in it and is found without a framework key), `functions` per service. Public routing is top level: `rewrites` with
  `destination: {"service": "<name>"}`, evaluated in order, "routing into a service is final". The service receives the
  original path (`/api/v1/...`), which the API already serves. A service is private unless a top-level rewrite targets it.
- **Where keys live.** With `services` present, `functions`, `framework`, `buildCommand`, `installCommand`, `devCommand`,
  `ignoreCommand`, `outputDirectory` must be inside the owning service (not top level). `crons` stay in the **root** `vercel.json`
  (a service's own file is ignored; the DEC-001 proof found this). `vercel.json` takes no comments, so the reasons are here.
- **Function region: verified placement.** The docs describe a top-level `regions` and a per-function `regions`, and do not say
  what a top-level `regions` does under Services. A local experiment (CLI 59.17.0) with `"regions": ["fra1"]` at top level and
  no per-function value produced a service function with **no `regions` field** in `.vc-config.json`; with
  `services.api.functions["index.py"].regions = ["cdg1"]` the function's `.vc-config.json` has `"regions": ["cdg1"]`. The
  file therefore uses the per-function key, and a test forbids a top-level `regions`. `vercel build` validates neither region codes
  nor unknown keys (it built with `nowhere9` and a bogus key), so `test_vercel_config.py` checks the code against Vercel's 19
  regions and that exactly one is set (Hobby allows one). The web service keeps Vercel's default region; it never queries the database.
- **Framework Preset.** Vercel's guide says the project's Framework Preset (Project Settings, Build and Deployment) must be
  **Services**, otherwise the `services` key is not used. The page's text could not be re-read verbatim here (the fetch tool
  summarizes); the proof's local `.vercel/project.json` uses `"framework": "services"` and `vercel build` honours it.
  **NOT VERIFIED** until a project exists. It is an owner step below.
- **Shared environment.** Services "deploy together with shared routing, environment variables, and a unique domain", and the
  configuration reference has no per-service environment field. Every project variable reaches both services. See
  "Shared-environment consequence".
- **Limits in the Build Output API reference:** "Middleware is not supported inside a service" and the Edge runtime is not
  supported inside a service. See "NOT VERIFIED" for the Next.js proxy.

## Shared-environment consequence (deploy-services-shared-env)

Under B (this baseline), the web service's server processes hold the API's secrets because the platform injects every project
variable into both services: `DATABASE_URL` (the `app_login` transaction pooler string), `STRIPE_SECRET_KEY`,
`STRIPE_WEBHOOK_SECRET`, `CLERK_WEBHOOK_SIGNING_SECRET`, `LLM_API_KEY`, `CRON_SECRET`; and the API function gets the web
build's `SENTRY_AUTH_TOKEN`. FE-02 ("the web app holds no server secrets") and ADR-0001 therefore hold **in the code** (the web
app reads only its own variables; `loadEnvFiles` filters the repository-root `.env`) **but not at the environment level**.
The comment at `apps/web/next.config.ts:15-17` ("API secrets ... never enter it") is true only for the root `.env` filter; on
Vercel it must say that every project variable reaches the web process. Mitigations in this round:

1. Keep API-only secrets minimal and never put `MIGRATION_DATABASE_URL` or `API_DB_PASSWORD` in the Vercel project (the
   migration credential lives in a GitHub Environment secret only, and the runtime role cannot own tables, DEC-008 A).
2. The value-based bundle scan: `check-bundle-secrets.mjs` already compared the values of server-only variables found in the
   process environment and the root `.env`; it now also compares the password inside the two database URLs and
   `API_DB_PASSWORD`. In the `deploy-config` job the web service is built with generated canary values for every server-only
   variable and `--require-values` fails if nothing was compared (the `web` job's build has no secrets, so its comparison was
   empty). **Not done (apps/web is another package's):** chaining the scan into `apps/web`'s `build` script
   (`next build && node ../../scripts/check-bundle-secrets.mjs --require-values`) would run it in every Vercel build, where the
   real values exist. Recommended to the integrator.
3. Owner acceptance is still needed: B departs from FE-02 at the environment level, and the DEC-001 ADR must say so.

## Owner steps (nothing below was done; all need an account or a human decision)

### Vercel (one project)

1. Create the project from the Git repository (Root Directory: the repository root).
2. Project Settings, Build and Deployment, **Framework Preset: Services**. Without it the `services` key is not used.
3. **Function region:** read the region from the pooled `DATABASE_URL` host (`aws-<n>-<region>.pooler.supabase.com`) and set
   `services.api.functions["index.py"].regions` in `vercel.json` to the matching Vercel region (one value; preview and
   production use one value, so create both Supabase projects in the same region, which cannot be changed later):

   | Supabase / AWS | Vercel | Supabase / AWS | Vercel |
   |---|---|---|---|
   | us-east-1 | iad1 | eu-west-3 | cdg1 |
   | us-east-2 | cle1 | eu-central-1 | fra1 |
   | us-west-1 | sfo1 | eu-central-2 | fra1 (no Zurich region) |
   | us-west-2 | pdx1 | eu-north-1 | arn1 |
   | ca-central-1 | yul1 | ap-south-1 | bom1 |
   | eu-west-1 | dub1 | ap-southeast-1 | sin1 |
   | eu-west-2 | lhr1 | ap-northeast-1 | hnd1 |
   | sa-east-1 | gru1 | ap-northeast-2 | icn1 |
   | ap-southeast-2 | syd1 | | |

   The shipped `iad1` fits `us-east-1` only. A mismatch costs about one network round trip per database statement of every
   API request. `test_vercel_config.py` rejects an unknown code or more than one.
4. Environment variables (Production and Preview separately, sandbox keys in Preview): everything the API and web read, from
   `.env.example`, **except** `MIGRATION_DATABASE_URL` and `API_DB_PASSWORD`. `DATABASE_URL` is the `app_login` transaction
   pooler string (`docs-notes/db.md`, steps 1-4). `APP_ENV=preview` / `production` explicitly (the API refuses a bundle without it).
5. Deployment Protection: Standard Protection covers previews; under Services there is one URL per deployment, so the
   web-to-API 401 problem of two projects does not arise. Production access and a protection-bypass secret for automation
   (`VERCEL_AUTOMATION_BYPASS_SECRET`, used by `proofs/dec-001/proof.py probe`) are the owner's decisions.
6. Authorize the first **preview** deployment (with sandbox credentials) and then run the probe in `proofs/dec-001/README.md`.

### GitHub (once the remote exists and push is authorized)

1. Settings, Environments: create `preview` and `production`.
2. On each, add the secret **`MIGRATION_DATABASE_URL`**: the migration role's Supabase **session pooler** string
   (`postgresql://postgres.<ref>:<url-encoded password>@aws-0-<region>.pooler.supabase.com:5432/postgres`). Direct
   connections are IPv6-only without the IPv4 add-on, and GitHub-hosted runners are IPv4-only. Preview and production
   hold different credentials. The workflow never prints the value, but GitHub masks only the exact string; an error message
   may show the host.
3. On `production`: Required reviewers = the owner; Deployment branches = the default branch only. (`preview` may skip
   reviewers.) **Plan limits (docs.github.com, check before relying on them):** Environments and their secrets exist for public
   repositories on every plan; private repositories need Pro, Team, or Enterprise, and required reviewers on private
   repositories need Enterprise. Without reviewers the click on "Run workflow" is the only gate: restrict who has write access.
4. Branch protection for the default branch with these CI jobs as required checks: `API — lint, types, tests, audit`,
   `API — Supabase Postgres image`, `Web — lint, types, tests, build, e2e`, `Deployment configuration — vercel build, no
   deployment`, `Secret scan`.
5. To migrate: Actions, "Database migrate", Run workflow **from the default branch**, choose the environment. For production,
   approve the run when GitHub asks. The job is skipped on any other branch. Run it after the code that needs the migrations is
   merged and before that code is promoted. Never apply an unmerged branch's migrations to a shared database (once a later
   version is recorded, every earlier-numbered migration is refused). Never migrate in the Vercel build step.
6. Optional: a Vercel Deployment Check on the production project that waits for the "migrate (production)" run (a
   dispatch run reports no commit status by itself; this is not built).

### Local, optional

`pnpm hooks:install` (the hook now works with a running Docker even without `gitleaks`). The `bootstrap` script does not run it;
that is `onboard`'s file.

## Documentation changes needed (integrator)

- `dev-docs/evidence/dec-001-vercel-python.md`, topology table: Maturity row = Beta on all plans (docs/services, updated
  2026-08-10), `experimentalServicesV2` = the CLI's deprecated alias, Framework Preset must be Services; B's "Secrets" row (above);
  B has one Protection/Firewall surface and Instant Rollback reverts web, API and crons together; state that the "generally
  available" condition is not met. Leave historical links; add "the file moved to the root".
- `dev-docs/engineering-implementation-plan.md`: §3.2/§3.3 DEC-001 resolved to B with the Beta and shared-environment acceptance;
  §21 rows: MIG-04 = "controlled step exists (manual `workflow_dispatch`, owner-gated Environment); hosted run NOT VERIFIED";
  a "Deployment configuration" row = local packaging evidence (`pnpm test:vercel-build`, CI `deploy-config`); SEC-05 = pinned gitleaks
  CLI in CI, container fallback in the hook; SEC-04 = bulk advisory endpoint; §16.1 gets the `db-migrate` row (Environments, MIG-05).
  §23 step 3 reflects that the root file exists.
- `docs/operations.md` (line ~20-26 topology, ~220-226 `excludeFiles`, ~32/41 migrations): one project with Services, root
  `vercel.json`, crons only in the root file, `excludeFiles` is in the root file's api service, the migration workflow and the
  session-pooler wording (see `docs-notes/db.md`), a "Function region" row, a "GitHub and Vercel settings" checklist (above).
- `docs/architecture.md`, `dev-docs/adr/0001`, `0003`, `dev-docs/evidence/2026-10-14-adr-0003-modular-architecture.md`: replace
  references to `apps/api/vercel.json` with the root `vercel.json`. New ADR-0004 records the topology, Beta, and the shared environment.
- `docs/integrations.md`: Supabase step 1 (same specific region for preview and production; the region table), the secrets
  scan paragraph (no license needed; fork pull requests work).
- `README.md`: the pre-commit hook is no longer "optional" friction (Docker is enough); `pnpm test:vercel-build` row.
- `apps/web/next.config.ts:15-17` and `apps/web/src/lib/env-files.ts:12-15` comments (web's files): the guarantee covers the
  root `.env` only.
- `perf/run-plan-template.md`: record function and database region (they must match).

## Decisions taken (reversible)

- **Pinned CLI version 65.2.0**, the newest release at least 7 days old on 2026-10-30 (published 2026-10-22). The evidence
  record used 59.17.0; both build the repository the same way (13 checks). A newer CLI bundles a newer `@vercel/python`; the
  platform's own builder version is independent of the local CLI. Alternative: 59.17.0.
- **Region key in the service function, value `iad1`**, not a top-level key and not absent: a test and the CI check keep one
  valid code in the file. The adopter changes the value. Alternative: leave the region to the dashboard only.
- **gitleaks CLI tarball with a committed SHA-256 in CI, container digest in the hook.** The tarball needs no Docker, no root,
  and no `safe.directory` workaround on runners; the image needs no host install. Alternative: the image in both places.
- **History scan = every commit reachable from the checked-out commit** (`--log-opts="--full-history HEAD"`), not every remote
  branch (gitleaks' default `--all`), so an unrelated unmerged branch cannot fail a pull request.
- **Migration workflow runs on the default branch only** (job `if`), uses `pnpm db:migrate`, the production dependency group only
  (`UV_NO_DEV`), and `contents: read`. Alternative: allow any branch (rejected: shared database).
- **`npm-audit-bulk.mjs` instead of a pnpm 11 upgrade**: the upgrade touches `packageManager`, the lockfile, and the build-script
  approvals, and belongs in its own change. Alternative: upgrade pnpm.
- **`.gitleaks.toml` allows `pk_` publishable keys and the bare JWT header** (see Integration below).

## Limitations / NOT VERIFIED until a preview deployment (the owner authorizes it)

Everything here is `local`/`mocked`. A preview must still show:

- that Vercel accepts the Services configuration on a real project (Beta) and that the Framework Preset takes effect;
- that the four crons register from the root file and call the routes on a **production** deployment (previews never run crons);
- that the platform router sends `/api/v1/*` to the API ahead of the catch-all, with raw bodies for webhooks;
- that `VERCEL_REGION` in the API function equals the configured region (the build output only carries the field);
- cold starts, duration, memory, payload limits, the 500 MB bundle limit on the platform (locally 74.5 MiB), and Instant Rollback behaviour for crons;
- that the web service's Next.js **proxy** (`apps/web/src/proxy.ts`, Clerk's `auth.protect()`) runs under Services. The Build Output API
  reference says middleware is not supported inside a service. See the finding recorded below; the API authorizes every operation
  itself, so this would affect page redirects only (FE-05);
- the shared-environment consequence on the platform (what the web process can read);
- that a build on Vercel's build machines installs the Python dependencies like the local `uv` install (the local CLI uses the same builder code, not the same machine).

Other limits:

- `pnpm test:vercel-build` needs a few minutes and network access; its web build installs the workspace (including the Vercel CLI
  tree) once more.
- `vercel build` emits `experimentalServicesV2` in `config.json`; the checker reads only `services`, `routes`, `crons`.
- `npm-audit-bulk.mjs` audits the production tree installed on the machine it runs on: native packages for other platforms are not
  checked (CI audits linux x64, which is what Vercel builds). `pnpm audit --dev` (report only) still uses the legacy endpoint.
- The gitleaks stdin fallback in the hook scans the staged diff text (without the `.gitleaksignore` file's own changes); the host
  binary path keeps using `git --staged`.
- The topology A alternative (two projects) is not shipped: it would need `apps/api/vercel.json` with the crons again.
- Dependabot does not read the gitleaks pins. Bump: take the new `gitleaks_<v>_linux_x64.tar.gz` SHA-256 from the release's
  `gitleaks_<v>_checksums.txt`, update `GITLEAKS_VERSION` and `GITLEAKS_SHA256` in `ci.yml`, and set the hook's image to
  `docker buildx imagetools inspect zricethezav/gitleaks:v<v>` (the index digest); `test_ci_workflows.py` keeps the two versions equal.

## Integration notes (for the integrator)

- **Conflicts likely:** `.github/workflows/ci.yml` (new job, secrets job rewritten, concurrency, audit step), `package.json` (new
  script `test:vercel-build`, new `devDependencies`), `pnpm-lock.yaml` (regenerate with `pnpm install` after merging; do not hand
  merge), `apps/api/pyproject.toml` + `apps/api/uv.lock` (one dev dependency; `uv lock`), `scripts/check-bundle-secrets.mjs`
  (arch/config also edits it: PostHog keys), `scripts/security-audit.mjs`, `.gitleaks.toml`.
- **gitleaks and the other branches:** `gitleaks git` over all refs of this repository reported 4 `generic-api-key` findings on
  the other packages' branches (not on this one): `a732cb66` `packages/contracts/config-cases.json` (lines 5 and 110, Clerk
  publishable keys), `0bdc6390` `.env.example:79` (a Clerk placeholder publishable key), `5dfa505b`
  `apps/api/tests/telemetry_child.py:32` (the bare RS256 JWT header `eyJhbGciOiJSUzI1NiJ9.`). All four are public or inert values;
  with this branch's `.gitleaks.toml` the same scan reports no leaks (303 commits). Without it the new full-history CI step fails
  after those branches are merged.
- **Order:** merge `config` before re-running `pnpm test:vercel-build`: if the web configuration starts requiring variables on
  `VERCEL=1`, add them to `buildEnvironment()` in `scripts/test-vercel-build.mjs` (it sets `APP_ENV=preview`, a placeholder Clerk
  publishable key, and canary server-only values; `CLERK_JWT_KEY` and `SENTRY_AUTH_TOKEN` stay unset on purpose: the first is a
  public key that must be a PEM, the second turns on source map upload).
- `proofs/dec-001/run.sh` still uses the `vercel` on `PATH`; `node_modules/.bin/vercel` is the pinned one.
- No migrations, no contract changes, no `.env.example` change.

## Evidence

All `local` unless stated. Machine under heavy load from unrelated processes.

Next.js proxy under Services (`local`, from the build output of `pnpm test:vercel-build`): `apps/web/src/proxy.ts` is emitted as the
Node.js function `services/web/functions/_middleware.func` (`runtime` `nodejs24.x`, not Edge) and the web service's `config.json`
routes carry `"middlewarePath": "/_middleware"` for the matcher's paths and for `/__clerk/(.*)`. All 17 web functions are
`nodejs24.x`, so the "Edge runtime is not supported inside a service" limit does not apply. Whether the platform honours
`middlewarePath` inside a service, given "Middleware is not supported inside a service", is **NOT VERIFIED**.

| Command | Result |
|---|---|
| `pnpm test:vercel-build` (CLI 65.2.0, dev Python environment), first run | PASS: `vercel build` ok, bundle scan compared 11 values and found none, 13 checks passed (74.5 MiB, 23 native libraries, all linux x86-64; services web and api; crons = registry; region iad1). |
| Same, second run with `VERCEL_BUILD_PYTHON` = the production-only environment (as CI runs it) | PASS, the same 13 checks (the check script needs only the production dependencies) |
| Mutation: the `excludeFiles` removed, `vercel build`, `check_vercel_build.py check` | FAIL `no_test_files_or_caches`: 71 files from `tests/` in the bundle. Restored. |
| `python scripts/check_vercel_build.py self-test` | passed, 11 planted defects caught |
| Production-only `uv sync --frozen --no-dev --no-install-project`, then `python -I -c 'import index, app.cli'` (CI step) | imports ok |
| `vercel build` region experiment (59.17.0, scratch project outside the repo) | top-level `regions`: no field in the function; per-function `regions`: present; invalid code and unknown key not rejected |
| `uv run pytest tests/unit/test_vercel_config.py tests/unit/test_ci_workflows.py` | 19 passed |
| `TEST_DATABASE_URL=...54360/app uv run pytest tests/integration/test_outbox_jobs.py -k vercel_cron` | 1 passed |
| Mutations (each reverted): a `pull_request` trigger, an unpinned action, `environment: production`, `contents: write`, an echoed secret, a gitleaks version mismatch, no scanner self-test, a secret in the deploy-config job, an invalid region, a changed cron schedule, the `tests/**` exclusion removed, swapped rewrites, a different hook image | all 13 caught by the tests above |
| `node scripts/check-bundle-secrets.mjs --self-test` | passed; with the URL-password loop removed: FAILED (missed `MIGRATION_DATABASE_URL`, `DATABASE_URL`) |
| `node scripts/npm-audit-bulk.mjs` | No known vulnerabilities found (213 package versions of 209 packages); `--self-test` passed; with the status check removed: FAILED (a 410 was accepted); a direct request for `lodash@4.17.15` returns 6 advisories |
| `pnpm audit --prod` (for comparison, same day) | No known vulnerabilities found |
| gitleaks 8.30.1 (darwin arm64, SHA-256 checked) `git ... --log-opts="--full-history HEAD"` on this branch | 100 commits, no leaks; all refs: 303 commits, no leaks (4 before the `.gitleaks.toml` change) |
| gitleaks `dir` on a generated planted AWS-style key | exit 1 (flagged); `sk_live_` pattern still flagged, `pk_live_` allowed |
| Hook in a scratch repository, no host gitleaks, Docker running | clean commit passes (`gitleaks stdin` in the pinned image, no network); a planted key is refused (value redacted); `docker` present but stopped, and neither present: each refuses with its own message |
| `docker buildx imagetools inspect zricethezav/gitleaks:v8.30.1` | index digest `sha256:c00b6bd0...bbb7f` (linux/amd64 and linux/arm64) |
| `bash -n proofs/dec-001/run.sh` | syntax ok. **The full proof was NOT run** (it needs Docker Desktop, the developer's `pnpm db:up` database, and ten minutes). |

Not run: the full API suite (no API code changed; only tests and `index.py`'s docstring), `pnpm test:e2e`, GitHub Actions
(workflows are validated as YAML and by the tests above, not by `actionlint` or on GitHub), any Vercel, GitHub or Supabase call.
