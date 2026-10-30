# Notes for the integrator: package `onboard`

Branch `arch/onboard`, on top of `apix`, `db` and `stripe`. Scope: what a person who just cloned the template runs first, plus a one-command health check.
Code: `scripts/` (dev, check-setup, build-check, check-doc-links, init-template, `scripts/lib/*`, `scripts/tests/*`, `scripts/templates/adopter/*`), `apps/api/app/doctor.py` and `cli.py`,
`.env.example` (two values and a comment), `package.json` (five scripts). Documentation files are yours; this file says what they need. Evidence is `mocked` or `local`; nothing here
was run against Clerk, Stripe, PostHog, Sentry or an LLM provider.

## What changed

### 1. A fresh clone builds and tests without accounts (`onboarding-build-fails-without-clerk-key`)

- `.env.example` now ships a **well-formed but fake Clerk pair**: `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` names the host `clerk.not-configured-yet.invalid` (the reserved `.invalid` top-level domain, which
  nobody can register, so no Clerk instance can ever match it; CI's `placeholder.clerk.accounts.dev` is a name anyone could register as a Clerk instance), and
  `CLERK_SECRET_KEY=sk_test_clerk_not_configured_yet`. Both apps' existing validators accept it as it is (**no validator was changed**), so `pnpm build`, the API, the unit/integration tests and
  `pnpm check:setup` need no account. `pk_test_` is refused in production by both apps already, so the pair cannot be deployed there (tests in both apps).
- **What it does not do: serve pages.** Checked against a production build with the pair, `curl -H 'Accept: text/html'` on `/`, `/sign-in`, `/sign-up`, `/integrations`, `/billing` all answer `307` to
  `https://<key's host>/v1/client/handshake?...&__clerk_hs_reason=dev-browser-missing`: with a development publishable key, `clerkMiddleware` sends every page request to the Frontend API first
  (`apps/web/e2e/pages.spec.ts` says the same: "Clerk's development instances complete a browser handshake"). So the web app cannot show a page without a real Clerk instance, whatever the placeholder says.
  Hence `pnpm dev` stops at the preflight with one line asking for the two keys (below), and "paste your Clerk keys" is the first real step. (Evidence `local`, from `next start`; not seen in a browser.)
- The API reports the pair as "Clerk: mock, Not configured (placeholder keys from .env.example)" (`identity/module.py`, `clerk_not_configured`), so `/integrations` and the command line skip it instead of calling
  Clerk with a fake key. `scripts/lib/clerk.mjs` recognises it and the older `replace_me` pair (which both apps reject as malformed).
- **`pnpm build:check`** (`scripts/build-check.mjs`): the account-free production build plus the bundle scan, run the same way on a fresh clone and in CI, reading no `.env`. It pins `APP_ENV=local`, telemetry off, the
  placeholder publishable key, and a **synthetic `CLERK_SECRET_KEY` made up for each run**, so `check-bundle-secrets` proves the one server-only value the build can see stays out of the bundle (CI's scan could not,
  because CI has no secret at all). It prints that the result is a scan-only build. Ran once here: build and both scan steps pass ("1 checked").
- Tests: `apps/web/src/lib/env-example.test.ts` (the shipped `.env.example` passes the web validators for a build and for serving, and fails in production), `tests/unit/test_doctor.py` and `test_config.py`
  (the same for the API), `scripts/tests/clerk-env.test.mjs` (the scripts' constants equal the file). `test_env_example_placeholders_fail_clearly` described the old design ("a fresh .env.example must fail") and was
  replaced by two tests: the shipped pair loads and is "not configured", and the older `replace_me` pair still fails by name.

### 2. `pnpm dev` failure modes (`onboarding-pnpm-dev-failure-modes`)

`scripts/dev.mjs` now, in order:
1. **Stripe webhook secret** (only with `BILLING_MODE=sandbox`, an `sk_test_` key and the Stripe CLI): runs `stripe listen --print-secret` once (the key in `STRIPE_API_KEY`, never in argv; 15 s limit) and keeps the
   `whsec_` for the API process only. A missing CLI, a failed run or odd output gives **one line** and the run goes on without a listener. A restricted (`rk_test_`) key prints one line and skips the CLI.
2. **Preflight** (`scripts/lib/preflight.mjs`): the API's `check-config` and the web app's own validators (next.config.ts loaded through Next's `next/dist/server/config` in the dev-server phase, in `scripts/lib/check-web-config.mjs`),
   run in parallel with the signing secret in the API's environment, so a sandbox configuration validates. A problem list replaces the traceback ("dev: the configuration has problems", one line per variable, `Edit .env ...`), exit 1,
   nothing started. The placeholder Clerk pair is a problem here (see above); the older `replace_me` pair is one problem that says what to paste. The web check reports `skipped` (never blocks) if a Next upgrade removes the loader;
   `preflight.test.mjs` exercises the real loader, so that would be noticed. (Next resolves `next.config.ts`'s relative imports from the working directory: the check runs in `apps/web`.)
3. Starts both servers (the API's stderr is tee'd so its last error can be repeated), then **polls `/api/v1/health`** (2 s per request, 60 s in total). No answer: it prints the API's last error line, tells the person to fix `.env` and
   restart (the API does not reload when `.env` changes), and stops everything (exit 1). Only the first start is checked: uvicorn's reloader after a later bad edit is normal dev behaviour.
4. Starts `stripe listen --forward-to http://127.0.0.1:8000/api/v1/webhooks/stripe` as a third process group after the API is up, masks `whsec_...`, `sk_...` and the exact values in its output, and warns without
   stopping the servers if it exits. (`127.0.0.1`, not `localhost`: the API binds IPv4 only.)

The signing secret never touches `.env`, the web process, the log, or a command line. The CLI forwards all event types; the handler records those it does not use as ignored (the 14-type `--events` list from the stripe notes was not
added: it would have to be kept in step with `SYNC_EVENT_TYPES`).

### 3. `check-config`, `diagnose`, `pnpm check:setup` (`config-doctor-cli`, the command-line half of `testing-sandbox-procedure-manual`)

- `python -m app.cli check-config [--env-file PATH]` (`app/doctor.py`): `load_settings` only. Prints `APP_ENV` and each integration's mode, or the problems one per line (variable names only, no traceback), exit 1.
- `python -m app.cli diagnose [--env-file PATH] [--send-test-events]`: builds the container, runs **the probes behind `/integrations`** as a synthetic user (`DiagnosticsService._check` is now the shared `run_probe`, so the skip rules
  and the `DomainError` mapping are the page's own; `Container.probes` exposes the assembled list), and prints `PASS / FAIL / SKIPPED / SENT` per integration with the page's summaries, then an **evidence block** (spec section 20: date,
  commit, `APP_ENV`, command, providers contacted, test events sent, per-integration mode `local / mocked / sandbox / live`, outcome, status `PASS / FAIL / NOT VERIFIED`, limitations), then **"Manual steps still needed"**.
  Refuses `APP_ENV=production` (exit 2). Exit 1 if a check failed. No audit rows or rate limits (like `reconcile` and `job`). Probe-library log records (a failed connection names its host) are not printed.
- Without `--send-test-events` the container gets `NoopAnalytics` and `NoopErrorReporter`, so even the LLM check's `llm_check_run` event stays local, and PostHog and Sentry are reported as skipped ("Not sent: run ... to send one ...").
  With it, one PostHog event and one Sentry issue go to the sandbox projects (only for integrations in sandbox mode).
- **Clerk row**: the command line has no session, so the identity probe (a membership read for a synthetic user) can only show that the secret key was accepted; its PASS text says "no session token was verified" instead of the page's
  "Session token verified". It does **not** detect a publishable/secret key from different instances (a 404 for the synthetic user is read as zero memberships): that is the Clerk key-pair check of the config briefing, not built here.
- **`pnpm check:setup [--send-test-events]`** = the preflight above (the placeholder Clerk pair is only a warning here), then `diagnose` (stdio inherited; same exit status). It ends with the manual steps.
  **It is not called `pnpm doctor`**: pnpm 9.15 has a built-in `pnpm doctor` ("Checks for known common issues") that takes precedence over a script of that name. I ran it with a `doctor` script present: it silently ran the built-in
  (`pnpm run doctor` ran the script). A trap, so the name is `check:setup`, like `check:placeholders`.
- Manual steps are derived from what was skipped or failed and from what no API can check: database not up; Clerk keys (placeholder) or the Clerk dashboard items (Organizations, authorized parties, membership webhook, and the
  sign-in run of "Run all server checks"); Stripe mock (`pnpm setup:stripe`) or the dashboard-only portal and retry settings and webhook endpoints; LLM mode and spending limit; PostHog and Sentry (optional, and confirm the test events).

### 4. The adopter's repository (`docs-dx-template-material-in-adopter-repo`)

`pnpm init-template` (first run, unless `--keep-template-material`):
- removes `proofs/`, `docs-notes/` and the files in `dev-docs/evidence/` (which keeps a new `README.md` for the adopter's own evidence). `perf/results/` is git-ignored output and is never part of a clone, so it is left alone.
- replaces `AGENTS.md` with `scripts/templates/adopter/AGENTS.md`: the engineering rules (no remote changes, no secrets, no fabricated evidence, no weakened tests, preserve work and ask before destructive operations, ADRs for
  security/billing/architecture decisions), the doc map, the verify commands, and a pointer to the specification as inherited reference. No §3, no `DEC-`, no open-decisions regime.
- keeps the specification and the ADRs (about 400 requirement IDs in code and tests point into the specification; they would dangle), and adds a banner to the specification saying it is inherited reference whose decision register,
  phase plan, status and evidence links are the template's history.
- strips the template-only blocks (markers below) from every Markdown file, and turns relative links to removed files into plain text, so `pnpm check:links` (new, `scripts/check-doc-links.mjs`) passes.
- applies only while the copy is unfinished (package.json is written last), so a part-way run resumes harmlessly and a finished copy is never touched again; `--dry-run` lists `removed`. A copy initialized by an older
  init-template is not changed (delete the material by hand, or re-copy).
- `scripts/test-init-template.mjs` asserts all of it (removal, AGENTS rules kept and regime gone, every `pnpm` command in the adopter AGENTS exists, banner once, markers stripped, links resolve, keep mode changes nothing, a later run
  leaves the adopter's own files, dry run lists removals); every assertion was mutation-checked.

## New commands, scripts and environment variables

| Item | Notes |
|---|---|
| `pnpm check:setup [--send-test-events]` | preflight + `diagnose`; see above |
| `pnpm build:check` | account-free build + bundle scan |
| `pnpm test:scripts` | `node --test "scripts/tests/*.test.mjs"` (35 tests); **not wired into CI**: add a step |
| `pnpm check:links` | relative Markdown links resolve; add it to CI next to `check:placeholders` |
| `python -m app.cli check-config`, `diagnose` | see above |
| `pnpm init-template ... --keep-template-material` | opt out of the removals |
| `.env.example` | the two Clerk values and their comment. **No new environment variable** |
| Markdown markers | start and end markers `template-only:start` / `template-only:end` written as HTML comments (`<!-- ... -->`). The strip is textual (a code fence does not protect it): when you document the markers, do not write a start and an end marker in the same file |

## Manual steps for the owner / adopter

1. Create a free Clerk development instance, enable Organizations, paste the two keys into `.env`. `pnpm dev` stops until you do (it says so); `pnpm check:setup` lists it while the placeholders remain.
2. For local Stripe webhooks: install the Stripe CLI (https://docs.stripe.com/stripe-cli). No `stripe login` and no secret copying are needed: `pnpm dev` passes the sandbox key to the CLI as `STRIPE_API_KEY`.
3. Everything else is listed by `pnpm check:setup` itself (Stripe sandbox and dashboard-only settings, LLM key and spending limit, PostHog, Sentry, the Clerk dashboard items).
4. `pnpm check:setup --send-test-events` creates one PostHog event and one Sentry issue in the sandbox projects; run it only with the owner's go-ahead. Plain `pnpm check:setup` contacts the providers that are in
   sandbox mode (Clerk membership read, Stripe prices and portal read, LLM model metadata), nothing in mock or disabled mode.

## Documentation changes needed

- `.github/workflows/ci.yml` (deploy package): replace the two steps "Production build (placeholder public key; no secrets)" and "No server-only values in the browser bundle" with
  `run: pnpm build:check`; add `pnpm test:scripts` and `pnpm check:links`. `apps/web/playwright.config.ts` keeps its own placeholder (`placeholder.clerk.accounts.dev`, APP_ENV=test): it works, and I did not touch it.
- `README.md`:
  - line 58: replace `cp -n .env.example .env` with "If you skipped init-template: `cp .env.example .env`", and drop the "if you are only trying the template out" remark: there is no no-account try-out of the web app (Clerk's handshake, above).
  - Quick start: "`pnpm build`, the tests and `pnpm check:setup` need no accounts; `pnpm dev` needs the two keys of a free Clerk development instance (it stops with that message until you paste them)".
  - Commands: add `pnpm check:setup`, `pnpm build:check` ("account-free build with a placeholder Clerk key plus the bundle secret scan, what CI runs"), `pnpm test:scripts`, `pnpm check:links`; `pnpm build` = "deployable production build; needs your Clerk publishable key".
  - Troubleshooting row at line 146 ("The API is not running on API_ORIGIN; run pnpm dev"): "the API is not running or stopped at startup: look for `Invalid configuration` in the pnpm dev output or run `pnpm check:setup`, fix .env, restart `pnpm dev` (the API does not reload when .env changes)".
  - Wrap the template-only parts in the template-only markers: the `> **Status:** pre-release ...` paragraph (line 16) and the "Verification status" table (lines 119-128), which describe the template, not the adopter's product.
- `AGENTS.md`: line 33 `pnpm build && pnpm check:bundle-secrets` -> `pnpm build:check`; add `pnpm check:setup`, `pnpm test:scripts`, `pnpm check:links` to the verify block. **Keep `scripts/templates/adopter/AGENTS.md` in step** (the test only checks that
  every `pnpm` command it names exists).
- `CONTRIBUTING.md`: setup block: add `pnpm build:check`; wrap line 3's "Thanks for helping improve the template ..." paragraph (and the open-decisions bullet) in template-only markers.
- `docs/operations.md` lines 18-30 (the "DEC-001 is open" note and "Complete the DEC-001 proof") and the DEC-001 evidence line ~215: wrap in template-only markers (links to the removed proof already become plain text).
- `docs/integrations.md`: new section "Checking your setup without signing in" (`pnpm check:setup`, what it contacts, `--send-test-events`, the evidence block, "Clerk: no session token is verified"); Stripe step 5 (Local): "`pnpm dev` starts
  `stripe listen` for you when `BILLING_MODE=sandbox`, an `sk_test_` key is set and the Stripe CLI is installed; only a deployed environment needs the endpoint's own `STRIPE_WEBHOOK_SECRET`"; "Verifying an integration": point to
  `pnpm check:setup` for the server checks and the evidence block.
- `.github/ISSUE_TEMPLATE/feature_request.yml` line 2 ("Propose an improvement to the template") and `docs/*` prose that says "the template": adopters' copies keep them (YAML cannot carry the markers); reword neutrally if wanted.
- `dev-docs/engineering-implementation-plan.md`: §21 REPO-11 row: partial, then re-record the clean-clone run (bootstrap, init-template, db:up, db:migrate, test, build:check, `pnpm check:setup`) with the one human step "paste Clerk keys for pnpm dev";
  §23 next step 1 can point at `pnpm check:setup`. `dev-docs/evidence/`: record this package's runs (the table below).
- After your merge decide whether `docs-notes/` stays in the template; if it does, init removes it from adopters' copies (it is in `TEMPLATE_ONLY_PATHS`, `scripts/lib/template.mjs`).

## Found, not mine, not fixed

- **Two API tests fail on the integrated base** (`8451058`), independent of this package (no route or auth file differs from the base): `tests/integration/test_platform.py::test_route_manifest_every_route_declares_what_authorizes_it`
  and `::test_the_route_manifest_reports_a_route_without_a_valid_authorization_declaration`. `POST /api/v1/billing/sync` (added by `stripe`) has no `requires(...)`, which `apix`'s route manifest demands. Fix (billing code, left to you):
  `dependencies=[requires(Access.OWN_ACCOUNT)]` on `@router.post("/billing/sync", ...)` in `apps/api/app/modules/billing/routes.py` (it re-reads the caller's own subscriptions; the route sits next to `/entitlement`, which declares `OWN_ACCOUNT`).

## For the `config` package or the web owner (I did not edit `config.py`, `env.server.ts`, `env.ts`, `next.config.ts`, `proxy.ts`)

1. **Optional: let `pnpm dev` run without Clerk keys.** In `apps/web/src/proxy.ts` export a pass-through when the publishable key's host ends in `.invalid`:
   `const unconfigured = (clerkFrontendApiOrigin(process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY ?? "") ?? "").endsWith(".invalid");` then
   `export default unconfigured ? () => NextResponse.next() : clerkMiddleware(...)`. Pages would load, the Clerk script would fail, and the header would show "Sign-in is unavailable." after 30 s (derive a flag in `next.config.ts`'s `env`
   block to show it at once). Then drop `requireClerk: true` from `scripts/dev.mjs`. **Not built or tried**: it changes route protection (FE-05) and needs a browser check; protected pages would no longer be gated by the proxy while unconfigured.
2. Optional: refuse the placeholder pair when `APP_ENV=preview` (host ends in `.invalid` -> "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY is still the .env.example placeholder") in `identity_problems` and `clerkProblems`. Production is already refused.
3. Optional message polish: for `/replace_me/i` say "... is still the old .env.example placeholder; copy the current pair from .env.example or paste your Clerk keys"; when the publishable key is missing (a clone with no `.env`) mention `pnpm build:check`.
4. The Clerk key-pair check from the `config-doctor-cli` briefing (compare `GET /v1/jwks` with `<issuer>/.well-known/jwks.json` in `IdentityProbe`) is not built; `diagnose` would gain it for free.

## Decisions taken (owner may reverse)

- **Placeholder host `.invalid`** instead of keeping CI's `placeholder.clerk.accounts.dev`: anyone could register a Clerk dev instance named `placeholder` and mint tokens that a deployment keeping the placeholder would accept
  (given a matching `azp`). Alternative: keep the old host and rely on the production rule.
- **`pnpm check:setup`, not `pnpm doctor`** (built-in clash; see above). Alternative: `pnpm run doctor`.
- **`pnpm dev` refuses the placeholder pair** (a clear stop) instead of starting a web app whose every page redirects to an unreachable host. Alternative: the pass-through proxy above.
- **No validator change:** the validators accept the placeholder as an ordinary well-formed key; the cost is that Clerk would reject it (sign-in unavailable). Alternative: an "unconfigured" mode in each app.
- **`check:setup` runs the Stripe CLI** (`listen --print-secret`) when it applies, so a sandbox configuration validates without a hand-copied secret. Alternative: skip it and let the API's own error name STRIPE_WEBHOOK_SECRET.
- **Specification and ADRs stay in the adopter's copy** (as inherited reference, with a banner) instead of being removed with a generated requirements catalogue: removing them would orphan about 400 requirement IDs in code. Alternative: the
  reviewer's `docs/requirements.md`.
- **Removal is default-on** (`--keep-template-material` opts out), applied only on the first run. A copy initialized earlier is left as it is.
- `perf/results/` is not touched (git-ignored local output, possibly the adopter's).

## Limitations and follow-ups

- Not verified in a browser: any page of the web app (see above), real `stripe listen` (a fake `stripe` on PATH covers the CLI's behaviour in tests and in a manual `dev.mjs` run), `diagnose` against any provider, Windows
  (dev.mjs is macOS/Linux only, as before). No e2e run.
- `diagnose` does not detect a Clerk key pair from two instances, and does not check webhook endpoints (the Stripe probe says so).
- The dev health probe is fixed to `127.0.0.1:8000` (what `dev:api` binds). The 60 s limit is generous for a cold start; a slower machine would need it raised (`API_START_TIMEOUT_MS` in `scripts/dev.mjs`).
- `scripts/templates/adopter/AGENTS.md` can drift from `AGENTS.md`; keep them in step.
- The sandbox verification procedure (`verify:sandbox`, Clerk/Stripe lifecycle automation, CI sandbox workflow) from `testing-sandbox-procedure-manual` is **not** built: only its command-line server-check and evidence-block part is.
- One accident, disclosed: during a manual `dev.mjs` run my PATH still contained the real `stripe` CLI, so `stripe listen --print-secret` ran once with a synthetic, invalid `sk_test_` key (Stripe rejected it; nothing was created
  or changed). The committed tests never use the real CLI (injected runners, or a fake `stripe` first on a restricted PATH).

## Evidence

All `local` or `mocked`; nothing `sandbox` or `live`. Machine under heavy load (load average 280 to 340).

| Command (from the worktree) | Result |
|---|---|
| `uv run ruff check app tests index.py ../../perf`, `ruff format --check`, `lint-imports`, `mypy` | pass; 162 files formatted; 8 contracts kept; 86 files |
| `uv run pytest -q -p no:cacheprovider` (full API suite, once, before the last test edits) | 1298 passed, 5 skipped, **3 failed**: the two base failures above and `test_env_example_placeholders_fail_clearly` (replaced afterwards) |
| `pytest tests/unit/test_config.py tests/unit/test_doctor.py tests/integration/test_diagnostics_cli.py` (after) | 77 passed |
| `pnpm --filter web typecheck`, `lint`, `format:check`, `test` | pass; 433 passed, 1 skipped (pre-existing) |
| `pnpm test:scripts` | 35 passed (clerk/env, preflight incl. the real Next loader, dev health, Stripe helpers incl. a fake `stripe`, local env, doc links) |
| `pnpm build:check` (the one build) | build, scan self-test and scan pass ("1 checked") |
| `next start` of that build with the placeholder pair, `curl -H 'Accept: text/html'` | `307` to the Frontend API handshake on every page (the finding above) |
| `pnpm test:init-template` | pass (also after the last change) |
| `pnpm test:new-module` | pass with `TEST_DATABASE_URL` on `arch-pg` (a first run without it failed: no PostgreSQL on 54329) |
| `pnpm contracts:check`, `pnpm check:placeholders`, `pnpm check:links` | pass |
| manual `pnpm check:setup` against a throwaway database on `arch-pg` (mock modes) | database PASS, the rest SKIPPED, evidence block and 6 manual steps; failure cases: old placeholders, sandbox billing without values |
| manual `scripts/dev.mjs` with fake `pnpm`, `uv`, `stripe` on PATH | dead API detected after the (shortened) timeout, all servers stopped, exit 1, last error line shown; healthy API: the signing secret reached the API process and not the web process, listener started, `whsec_` masked |

Mutation checks (the change reverted, the test seen failing, the change restored): `diagnose` production refusal, test events only with the flag, Clerk row wording, placeholder detection (API and scripts), no-op adapters
without the flag, mock counted as pass, key in argv, unmasked listener output, `rk_` accepted, non-200 treated as healthy, secret leaking into the environment or the log, `requireClerk`, policy re-applied to a finished copy,
template-only blocks kept, links to removed files left, AGENTS.md not replaced, `--keep-template-material` ignored, the `.env.example` secret back to `replace_me` (web test). `test:init-template` was run about eight times in all
(the mutation runs); `test:new-module` twice.
