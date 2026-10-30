# Package `config`: one validated configuration, bound to the platform

Branch `arch/config`, on top of `apix`, `db` and `stripe`. API and web code, tests, `.env.example` and this file only;
`README.md`, `docs/`, `dev-docs/` and `proofs/dec-001/README.md` are untouched. The documentation changes are listed for
the integrator. No migration, no OpenAPI change (the LLM check keeps its response schema), no new `package.json` script.

## What changed

### Platform binding (`config-app-env-not-bound-to-platform`, `secarch-env-binding`, `obs-web-app-env-defaults-local-on-deploy`)

Verified first: nothing read `VERCEL_ENV`; a bundle with `APP_ENV=local VERCEL_ENV=preview` loaded (diagnostics open to
every signed-in user), and so did production settings on a preview. The web app tagged a Vercel deployment `local`.

One rule set, mirrored in `apps/api/app/kernel/infrastructure/config.py` (`platform_problems`) and
`apps/web/src/lib/env.server.ts` (`platformProblems`). Messages name variables and enum labels only.

- `APP_ENV` unset: taken from `VERCEL_ENV` (`production`, `preview`; `development` is `local`). A derived value counts as set
  for the bundle rule ("APP_ENV is required outside a repository checkout").
- Refused: `APP_ENV=production` when `VERCEL_ENV` is `preview` or `development`; `local` or `test` when `VERCEL_ENV` is
  `preview` or `production`; API only: `local` in a deployed bundle unless `VERCEL_ENV=development`.
- Still valid: `APP_ENV=preview` on `VERCEL_ENV=production` (a first deployment, or a staging project), `local` or `test`
  without `VERCEL_ENV`, `preview` on `development`.
- `DatabaseSettings` and `require_development_environment` are untouched (ENV-05): `VERCEL_ENV=development` with `APP_ENV`
  unset is still refused by `db:reset`, `db:seed`.
- Web only: `VERCEL=1` (set by `vercel build` and on the platform, never by the account-free GitHub CI build) means a Vercel
  build or deployment. There `APP_ENV` (or `VERCEL_ENV`) is required ("APP_ENV is required on Vercel (set preview or
  production)") and `CLERK_SECRET_KEY` is required at build time (below). `VERCEL_ENV` is also read from the root `.env`
  by the web app, like the API does (`SERVER_VARIABLES`); `VERCEL` is not (a file must not imitate the platform).
- Tests: `tests/unit/test_platform_config.py` (matrix, derivation, bundle, ENV-05), `test_deployed_layout.py` (bundle
  refusals through `import index`), the `VERCEL_ENV x APP_ENV` matrix and the `VERCEL=1` cases in `env.server.test.ts`.

DEC-001 proof expectations (it sets its own env): `vercel build` sets `VERCEL=1` and no `VERCEL_ENV` (read from the CLI
source: only `vercel dev` sets `VERCEL_ENV=development`); the proof's `.env` has `APP_ENV=local`, both Clerk keys, and
`APP_BASE_URL`/`CLERK_AUTHORIZED_PARTIES` set to the same origin; the packaged runtime overrides `APP_ENV=preview` with no
`VERCEL_ENV`. All three pass. `proofs/dec-001/run.sh` itself was not re-run (needs Docker Desktop, about ten minutes, and
the machine was overloaded): I generated the proof's `.env` with `proof.py env` and loaded it through the API's
`load_settings` (checkout, `VERCEL_ENV=development`, and `APP_ENV=preview` as the runtime does) and through the web
`loadServerEnv` (`VERCEL=1` build, and `VERCEL=1 VERCEL_ENV=development`). Label: `local`.

### Origins (`config-preview-origins-and-local-defaults`, `clerk-preview-origins-rejected`, `deploy-preview-origins`)

Verified: preview with no `APP_BASE_URL` loaded with `http://localhost:3000`; `CLERK_AUTHORIZED_PARTIES=localhost:3000, not an
origin` was accepted; `APP_BASE_URL` need not be in the list.

- `APP_BASE_URL`: required (explicitly set) when `APP_ENV` is `preview` or `production`; always an http(s) origin (no path,
  query, fragment, credentials); stored in the form a browser sends (`HTTPS://App.Example.com:443/` becomes
  `https://app.example.com`, which also fixes the CORS origin and the Stripe redirects); `https` in production. Local and
  test keep the localhost default. Not derived from any `VERCEL_*` variable.
- `CLERK_AUTHORIZED_PARTIES`: each entry parsed as an origin and normalised; anything else is refused by name (the value is
  not echoed). Unset: the origin of `APP_BASE_URL`. Outside `APP_ENV=test`, the origin of `APP_BASE_URL` must be listed.
  Under `APP_ENV=test` nothing is defaulted (no Clerk instance: every token is refused, as before).
- Preview derivation (add only): when `APP_ENV=preview` **and** `VERCEL_ENV=preview`, `https://$VERCEL_URL` and
  `https://$VERCEL_BRANCH_URL` are added to the effective list, never replacing it. Only bare hostnames are taken (a value
  with a scheme, port or path is ignored). Never in production, never on Vercel production, never locally. The effective
  list is what the verifier uses and what the diagnostics page shows ("Authorized origins").
- Diagnosability: a validly signed token whose `azp` is not authorized logs `clerk_azp_refused` (warning) with `cause`
  (`azp_not_authorized` or `azp_missing`) and `azp` (the claim's origin, only when it looks like an origin; otherwise
  `null`), at most once a minute per process. The token is never logged.
- Explicit modes: in `preview` and `production`, every integration mode field (`BILLING_MODE`, `LLM_MODE`, `ANALYTICS_MODE`,
  `ERROR_REPORTING_MODE`, and any `Mode` field a future module adds, found by type) must be set ("X is required when
  APP_ENV=preview"). The web app requires the two telemetry modes the same way. Local and test keep their defaults.
- `playwright.config.ts`, sandbox branch: the API process gets `APP_BASE_URL` and `CLERK_AUTHORIZED_PARTIES` set to
  `http://127.0.0.1:3100` (the page origin; process values win over `.env`). Before, signed-in sandbox flows would have got
  401 under the default `.env`. Inferred from the code, not run (no sandbox keys).

### Validator drift (`config-validators-drift`)

- API: `CLERK_SECRET_KEY` must match `sk_(test|live)_\S+` and not contain `replace_me` (the web's exact message);
  `SENTRY_DSN` is parsed with `sentry_sdk.utils.Dsn` whenever set (the `BadDsn` text, which quotes the DSN, is never
  shown); `NEXT_PUBLIC_POSTHOG_HOST`/`POSTHOG_HOST` must be an http(s) URL with a host. Under `APP_ENV=test` the Clerk checks
  stay skipped.
- Both apps: the placeholder publishable key (`pk_test_replace_me`) gets its own message ("... is still the .env.example
  placeholder (copy it from Clerk Dashboard > API keys)"), so a fresh `cp .env.example .env` says what to do.
- Web: on Vercel the build requires `CLERK_SECRET_KEY` and `APP_ENV` (above). Reason: Vercel serves the build output
  (`vercel build` sets `VERCEL=1`; the `@vercel/next` launcher hands `NextServer` the saved config), so `next start` never
  runs, and a missing secret surfaced as a 500 on every request. The stale "rules mirror the API's" header of
  `env.server.ts` now says what each app enforces.
- Drift guard: `packages/contracts/config-cases.json` (24 cases, a test fixture, nothing reads it at runtime) is run by
  `apps/api/tests/unit/test_config_cases.py` and `apps/web/src/lib/env-cases.test.ts`. Each case lists the variables both
  apps must name, or that both must accept. A rule one validator learns and the other lacks fails a test in the lagging app.
  The two validators agreed on all 24 cases from the start.

### PostHog public key (`config-posthog-public-key-unchecked`)

- Verified: the key is inlined into the browser bundle even with `ANALYTICS_MODE=disabled`, and nothing checked it then.
  Both validators now check it whenever it is set: `ph[xsar]_` is refused as a secret credential (personal, secret, OAuth,
  and the `phr_` key) and anything else must match `phc_\S+`. The value is never echoed. (PostHog documents no
  character set for `phc_` tokens, so only "non-empty after the prefix, no whitespace" is enforced.)
- `scripts/check-bundle-secrets.mjs`: pattern `\bph[xsar]_[A-Za-z0-9]{20,}`; its self-test plants `${"phx"}_${token(40)}`,
  built at runtime. `node scripts/check-bundle-secrets.mjs --self-test` passes.
- **Refuted** (verifier correction): "an adopter following PostHog's guide gets analytics silently switched off". With only
  `NEXT_PUBLIC_POSTHOG_PROJECT_TOKEN` set and the mode enabled, both validators fail naming `NEXT_PUBLIC_POSTHOG_KEY`; with the
  mode disabled analytics is off by design. No alias was added. `.env.example` now notes PostHog's name for the variable.

### Sentry source maps (`config-sentry-sourcemaps-silent`, `obs-web-sourcemaps-never-upload`)

- Verified against the installed plugin: with `silent: true` the "No project/org provided. Will not upload source maps."
  warnings, upload errors and the success line are all dropped.
- `sentryBuildOptions(env)` (in `env.server.ts`): `silent` only when there is no token; `sourcemaps.disable` when there is
  none. `sourceMapUploadProblems(env)`: `SENTRY_PROJECT` is required with a token, `SENTRY_ORG` unless the token is an
  organization token (`sntrys_`); names only. `next.config.ts` throws a `ConfigError` with them in `next build` only
  (runtime Sentry init and `next dev` are unaffected). `SENTRY_AUTH_TOKEN`, `SENTRY_ORG`, `SENTRY_PROJECT` stay out of
  `SERVER_VARIABLES`: they come from the build environment, not `.env`.

### One root `.env` (`config-env-file-asymmetry`)

- `loadEnvFiles(dir, include, target)` reads only `<root>/.env` (the mode argument and the `.env.local`/mode files are gone).
- `strayEnvFiles(repoRoot, webDir)` lists, by exact name, `.env.local` and `.env.{development,production,test}{,.local}` in
  the root and `.env`, `.env.local` and the same six in `apps/web`. `.env.example` and synced `" 2"` copies never match.
  `next build`, `next dev` and `next start` throw the web `ConfigError` naming those files (never their contents).
  Checked by running `next build` with a stray `.env.local` and `apps/web/.env`: it fails at config load with the message.
- API, `scripts/check-bundle-secrets.mjs` and `scripts/dev.mjs` are unchanged: they already read only the root `.env`.

### LLM is optional (`docs-dx-llm-mandatory-in-production`)

- The claim held: `LLM_MODE=disabled` was refused everywhere, so production needed a live key and model.
- `LLM_MODE=disabled` is valid in every environment, needs no key or model, and no provider is wired
  (`LLMService(None, ...)`). `mock` and `sandbox` stay refused in production; `sandbox`/`live` still need key and model.
- `POST /api/v1/llm/check` still authorizes first, then answers `200 {ok:false, provider:"none", model:"", latency_ms:0,
  error_code:"not_configured"}` without touching the rate limiter or analytics. The diagnostics page lists the
  integration as `disabled` and reports it as skipped (the service already skipped `disabled`; the probe agrees if asked).
  The web card says the check is turned off and names `LLM_MODE=disabled` instead of "Check failed".
- The OpenAPI schema is unchanged (`error_code` was already a free string); `pnpm contracts` produced no diff by design.
- `reserved_request_threads` still counts `LLM_MAX_CONCURRENT_CHECKS` when disabled (conservative; harmless).

## New and changed environment variables

- `VERCEL_ENV` (API and web), `VERCEL_URL`, `VERCEL_BRANCH_URL` (API): set by Vercel; read, never written. Listed in
  `.env.example` prose only, not as assignments.
- `SENTRY_PROJECT`, `SENTRY_ORG`: new, build environment only, in `.env.example` after `SENTRY_AUTH_TOKEN` (empty).
- `LLM_MODE` accepts `disabled`. `APP_BASE_URL`, `CLERK_AUTHORIZED_PARTIES`, `APP_ENV`, the four `*_MODE` variables, and
  `NEXT_PUBLIC_POSTHOG_KEY` have new rules and new `.env.example` comments (values unchanged).
- `.env.example` header: only the root `.env` is read; restart `pnpm dev` after editing; `vercel env pull .env`.

## Manual steps (owner / adopter)

1. Vercel project settings: keep "Automatically expose System Environment Variables" on (the default). `VERCEL_ENV`,
   `VERCEL_URL` and `VERCEL_BRANCH_URL` come from it; without `VERCEL_ENV`, `APP_ENV` must be set explicitly (the API
   bundle and the Vercel build both refuse otherwise).
2. Scope production values (live Clerk/Stripe keys, production database, `APP_ENV=production`) to the Production scope
   only. Never import the local `.env` into a project (`APP_ENV=local` is refused on Vercel). Store keys as Secret.
3. Optional, dashboard only, plan availability unconfirmed: the team Security policy "Separate Production Secret Values"
   (Require Separate Values), which forces different secret values in Production and Preview.
4. Each Preview/Production environment sets `APP_BASE_URL` (the web origin; Preview: any origin you own, for example the
   stable branch URL or a staging domain) and names `BILLING_MODE`, `LLM_MODE`, `ANALYTICS_MODE`, `ERROR_REPORTING_MODE`
   (`mock`/`disabled` are fine on a preview; production: `disabled` or `live`, with `LLM_MODE=disabled` if no LLM is used).
   `CLERK_AUTHORIZED_PARTIES` may stay empty. A preview behind a custom domain must list that domain (or make it
   `APP_BASE_URL`); previews on `*.vercel.app` need Clerk development keys (Clerk's documentation).
5. Sentry source maps (optional): in the web project's build environment set `SENTRY_AUTH_TOKEN` (an organization token,
   `sntrys_`) and `SENTRY_PROJECT`, plus `SENTRY_ORG` for a personal token. Sentry's Vercel integration sets all three.
6. Anyone with a root `.env.local`, `.env.development*`, `.env.production*`, `.env.test*` or `apps/web/.env*` file moves
   its settings into the root `.env` and deletes it (the web build says which files).

## Documentation changes needed (integrator)

- `docs/operations.md`
  - Step 2 and the line that says a deployed bundle refuses to start without `APP_ENV`: on Vercel `APP_ENV` is derived from
    `VERCEL_ENV` and a contradiction is refused; the API refuses an unset one in a bundle, the web build refuses it when
    `VERCEL=1`; the web build also needs `CLERK_SECRET_KEY` (this was wrongly attributed to the web as "serving" only).
    Add steps 1 to 3 of the manual steps above. Document `APP_ENV=preview` on the Production scope as the setting for a
    pre-launch or staging project.
  - Environments table: `LLM` is `disabled` or `live` in production.
  - "Before a first deployment": `APP_BASE_URL` and the explicit modes per environment; `CLERK_AUTHORIZED_PARTIES`
    optional with the preview derivation.
- `docs/integrations.md`: Clerk step 4 and the callback table (origins, default, preview derivation, the 401 diagnosis:
  look for `clerk_azp_refused` in the API logs); LLM section (`disabled`); PostHog key (project token `phc_...`, never
  `phx_`/`phs_`; PostHog's guide names it `NEXT_PUBLIC_POSTHOG_PROJECT_TOKEN`); Sentry step 3 (build environment only:
  token, `SENTRY_PROJECT`, `SENTRY_ORG` for personal tokens; `next build` fails if the token is set without them).
- `README.md`: the troubleshooting row for sign-in/401 on previews (cause and the log line); "Edit `.env`": only the root
  `.env` is read; line 66 says everything else can stay mock or disabled: this now includes the LLM provider.
- `proofs/dec-001/README.md`, "Probing a preview", step 2: the preview's `APP_BASE_URL` must be set, and
  `CLERK_AUTHORIZED_PARTIES` (the proof's `azp`) must also list the origin of that `APP_BASE_URL`; on a preview of a
  preview application the deployment's own origins are added automatically.
- `dev-docs/engineering-implementation-plan.md`
  - ENV-01 traceability row (:1364): record the automated guard (stays Operational / NOT VERIFIED until the DEC-001 preview).
  - §5.2 ENV-02/ENV-03: explicit modes in preview and production; `disabled` valid for the LLM mode; §10 LLM-06 and LLM-09:
    the LLM check is optional. Credentials table (:320) Sentry row: "CI-only auth token plus org/project slugs for source maps".
  - ADR-0004: the preview derivation (gated on `APP_ENV=preview` and `VERCEL_ENV=preview`), the explicit modes.
  - `dev-docs/evidence/`: the mutation runs below.

## Decisions taken (owner delegated; reversible)

- **Explicit modes in preview and production** (verifier "Do now" 3, the original proposal called it optional hardening).
  Alternative: keep the silent defaults. Reversal: delete `explicit_mode_problems` and the `raw === undefined` branch of
  `telemetryMode`; the shared cases and tests name the lines.
- **No `API_ORIGIN` requirement in the web app** (verifier "Do now" 4). Under Vercel Services the router sends `/api/v1/*`
  to the API before Next.js sees it, so the build must not demand a variable the platform never uses. Deferred.
- **The LLM check while disabled is a 200 with `error_code: "not_configured"`**, not `503 NOT_CONFIGURED`/a problem
  `LLM_DISABLED`. Alternative: a 503 needs a contract change and would raise a Sentry warning on every click.
- **The refused-`azp` log line carries the claim's origin** when it parses as an origin (otherwise `null`), because the origin
  is what the operator must list. Alternative: cause only.
- **`CLERK_AUTHORIZED_PARTIES` is not defaulted under `APP_ENV=test`**, keeping "no Clerk in tests: every token refused".
- **The web app reads `VERCEL_ENV` from the root `.env`** so both apps agree on a file made by `vercel env pull`.
- Placeholder-key messages (optional item 3 of the drift finding) adopted in both apps.

## Limitations and follow-ups

- Preview URL derivation covers the deployment URL and the branch URL. A custom domain assigned to a branch must be listed
  or be `APP_BASE_URL`. Whether Vercel custom environments report `VERCEL_ENV=preview` was not verified.
- `APP_BASE_URL` stays a hand-set value per environment (the brief forbids deriving it from `VERCEL_*`).
- `proofs/dec-001/run.sh` was not re-run (see above). The sandbox e2e change is untested against real Clerk.
- IDN origins must be listed in punycode (what browsers send); not normalised.
- Not touched, by instruction: `scripts/dev.mjs`, doctor, init-template (a doctor check for stray env files or the new
  required variables belongs to `onboard`), CI workflows, `vercel.json`.

## Evidence (all `local` or `mocked`; nothing `sandbox` or `live`)

Commands run in this worktree, against the shared test PostgreSQL 17 on port 54360:

- `pytest -q -p no:cacheprovider` from `apps/api/.venv` (the full API suite, once): 1441 passed, 5 skipped, 2 failed.
  The two failures are not from this package and also fail on the merged base:
  `tests/integration/test_platform.py::test_route_manifest_every_route_declares_what_authorizes_it` and
  `::test_the_route_manifest_reports_a_route_without_a_valid_authorization_declaration` report
  `POST /api/v1/billing/sync: declares 0 authorization requirements` (`apix`'s manifest versus `stripe`'s new route).
  Suggested fix, for the integrator or the billing owner: `dependencies=[requires(BILLING_MANAGE)]` on `sync_billing`
  in `apps/api/app/modules/billing/routes.py`, after checking that `sync_from_stripe` authorizes it.
- `ruff check`, `ruff format --check`, `lint-imports` (8 contracts kept), `mypy` (85 files): clean.
- `pnpm --filter web lint`, `typecheck`, `format:check`: clean. `pnpm --filter web test`: 506 passed, 1 skipped.
- `pnpm contracts:check`: up to date. `node scripts/check-bundle-secrets.mjs --self-test`: passes.
- `pnpm build` (once, placeholder Clerk key): succeeds; `pnpm check:bundle-secrets` on its output finds nothing, so the
  new PostHog pattern matches nothing in a real bundle.
- `next build` runs that fail at config load, as designed: `VERCEL=1` without `APP_ENV` and `CLERK_SECRET_KEY`;
  `SENTRY_AUTH_TOKEN` without `SENTRY_PROJECT`/`SENTRY_ORG`; a stray root `.env.local` and `apps/web/.env`.
- `pnpm test:init-template` fails with "original database port remains in docs-notes/db.md" (the `db` package's note
  carries the default port that init-template scans for); not from this package, whose notes carry no port.

Mutation checks: each regression test fails when its fix is reverted (48 reverts: platform rules, derivation, `APP_BASE_URL`
required and origin, explicit modes, DSN, PostHog key and host, preview derivation and its gates, hostname filter, default
parties, normalisation, secret-key format, listed `APP_BASE_URL`, the azp log, its throttle and its echo guard, the LLM
disabled mode in five places and the web card, the web platform rules, derivation, `VERCEL=1` rules, serving, explicit
modes, PostHog key, source-map problems and `silent`, `VERCEL_ENV` as a web variable, `.env` only, the stray-file names,
the sandbox origin, and the scanner pattern). One revert first survived: the conftest prefix list is not observable
with a clean environment, so a dedicated test now checks it (and fails when the revert is applied).
