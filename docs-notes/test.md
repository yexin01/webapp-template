# Package `test`: one verify command, a browser tier without accounts, a sandbox run that can start, and a contract check

Branch `arch/test`, on top of `apix`, `db`, `stripe`, `web`, `obs`, `config` and `onboard`. Test code, scripts, the API's
declared errors, the regenerated contracts, `.env.example` (the end-to-end block) and this file. `README.md`, `AGENTS.md`,
`CONTRIBUTING.md`, `docs/`, `dev-docs/` and `.github/` are untouched; what they need is listed below. Evidence is `mocked`
or `local`. **Nothing here ran against Clerk or any other provider: the sandbox end-to-end run is NOT VERIFIED** (DEC-006).

## What changed

### 1. The sandbox end-to-end run can start, and says why when it cannot (`clerk-sandbox-e2e-cannot-pass` and its three duplicates)

Checked first, all still true on the integrated base except where noted:

| Claim | Result |
|---|---|
| The runner never read the root `.env`, so the Clerk keys and `E2E_CLERK_USER_*` were unset and the suite skipped | confirmed; fixed |
| The API rejected the e2e page's tokens (`azp` is `http://127.0.0.1:3100`) | already fixed by `config` (`APP_BASE_URL` and `CLERK_AUTHORIZED_PARTIES` set in the API process); kept, test extended |
| Password sign-in meets Device Trust (`needs_client_trust` on a new device) | Clerk's documentation says so ("Device Trust only applies to password-based sign-ins", default on for applications created after 2025-11-14); not observed. Replaced by a sign-in token (ticket), a passwordless strategy that the same page calls out as unaffected |
| The user's session is pending when the instance requires an organization (Membership required, default since 2025-08-22), so every test times out | confirmed from Clerk's documentation; the run now stops on it with the dashboard setting named |
| `E2E_SANDBOX` in `.env` cannot switch the sandbox branch on (the verifier's correction) | right: it is a command-line switch only; `.env.example` says so; only the value `1` counts (`Boolean("0")` used to be true) |
| Two ambiguous locators | already fixed in the base (`exact: true` for the Clerk card, the alert and the list item are filtered by their text); not run |

- `playwright.config.ts`: with `E2E_SANDBOX=1` it loads **three** variables from the root `.env` into the runner
  (`NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`, `CLERK_SECRET_KEY`, `E2E_CLERK_USER_EMAIL`), through the existing `loadEnvFiles`. The web
  server inherits the runner's environment, so nothing else is loaded (a unit test feeds a `.env` holding `DATABASE_URL`,
  `STRIPE_SECRET_KEY`, `LLM_API_KEY` and `E2E_SANDBOX` and sees only the three arrive). Then it **refuses to start, before building
  anything**, naming each variable: missing, still the `.env.example` placeholder, a production key (`pk_live_`/`sk_live_`: the run
  creates a user and signs in, so it only runs against a development instance), or a test address that is not a Clerk test address.
  Messages carry names, never values. Sandbox runs use one worker (one shared Clerk user and workspace), 60 s tests and 15 s
  assertions.
- A `clerk-setup` Playwright project (`e2e/clerk.setup.ts`) that the browser projects depend on: `clerkSetup({ dotenv: false })`
  (the testing token and Frontend API host reach the dependent workers only this way; I read Playwright 1.64's runner: a worker's
  changed `process.env` is passed on to dependent projects, and a function-based `globalSetup` is documented by Clerk as not
  reaching them), `ensureTestUser` (below), and one real sign-in. If it fails, nothing else runs and the single failure says what to fix.
- **The test user is created by the run**, in the development instance, through `@clerk/backend` (now a direct devDependency, the
  version already in the lockfile): `e2e+clerk_test@example.com` by default, or `E2E_CLERK_USER_EMAIL` (must contain `+clerk_test`),
  without a password. So the owner no longer creates a user or stores a password; `E2E_CLERK_USER_PASSWORD` is gone.
- Every sign-in is `clerk.signIn({ page, emailAddress })` (a sign-in token), then waits for an **active** session; a pending one
  fails with `pendingSessionMessage`: "Organization settings > Membership optional (or `npx clerk@latest enable orgs` without
  `--force-selection`)". The integrations test first asks the API for `/diagnostics/integrations` and names `APP_ENV=local` /
  `DIAGNOSTICS_OPERATOR_IDS` if it answers 404.
- Commits: `698e55c`.

**NOT VERIFIED (needs a Clerk development instance):** the token fetch, user creation through the Backend API, the ticket sign-in
and that Device Trust does not apply to it (documentation only: the page says passwordless methods are unaffected and does not
mention tickets), the `currentTask` property names used to detect a pending session (taken from clerk-js's types), and the signed-in
flows themselves. `pnpm verify:sandbox` is the run that produces this evidence.

### 2. A browser tier that needs no account (`onboarding-account-free-e2e-renders-no-page`, `testing-ci-e2e-no-browser`)

**Tested empirically, and it works.** With the placeholder pair `.env.example` ships (host `.invalid`), `curl` with browser headers on
the production build gives `307` to `https://clerk.not-configured-yet.invalid/v1/client/handshake?...&__clerk_hs_reason=dev-browser-missing`
without a `__clerk_db_jwt` cookie, and `200` (`x-clerk-auth-status: signed-out`, reason `session-token-and-uat-missing`) with any
non-empty one. So each account-free browser starts with an arbitrary `__clerk_db_jwt` cookie and every request that is not to
`127.0.0.1` is aborted (nothing leaves the machine; clerk-js cannot load, which the app must handle). `e2e/support/browser.ts` is
the fixture; `e2e/pages.spec.ts` uses it for both tiers (the sandbox tier skips the cookie and the abort).

- Tests (desktop and Pixel 7): the landing page, `/sign-in` and `/sign-up` with axe (WCAG 2.2 AA; Clerk's form region is excluded,
  it never renders account-free); keyboard (skip link first, brand link second, Shift+Tab back, Enter moves focus to `#main`); a
  missing file shows the 404 page with axe; `/dashboard` and an unlisted path send a signed-out visitor to
  `/sign-in?redirect_url=` and the URL names the page asked for; and the **"Sign-in is unavailable." notice with Reload** appears when
  clerk-js cannot load (fake clock, no 30 s wait) and Reload reloads. That notice is drawn by a client component after hydration,
  so it is the one check that the app's own scripts run under the real CSP.
- Every browser test also fails on: an uncaught page error (`failed_to_load_clerk_js` excepted account-free), a
  `securitypolicyviolation` event, a first-party response of 400 or more (not the document, not `/favicon.ico`), and a navigation
  to another host, which it names as "Clerk's development handshake was not skipped" (the redirect hop fails at the host lookup
  before Playwright's route sees it, so a `request` listener reports it).
- `playwright.config.ts` builds the account-free web app with the `.env.example` pair (it used the CI placeholder
  `placeholder.clerk.accounts.dev`, a name anyone could register) and pins `CLERK_JWT_KEY=""`; a unit test pins the pair to the file.
- Results (`mocked`): locked run 1 **56 passed, 14 skipped** (the 14 are the sandbox tests); run 2 repeated `pages.spec.ts` three
  times: 48 passed, no flake; the final run, on the committed state, again **56 passed, 14 skipped**, no retries. `ci.yml` needs no
  change for this (it already installs Chromium).
- Limitations: Clerk-drawn controls (sign-in form, user menu) and the header's signed-out buttons render only after clerk-js
  loads, so axe never sees them account-free; they stay in the sandbox tier. The 404 page is reached through a file-like path
  (`/missing.png`), because the proxy sends any other unknown path to sign-in.
- Commit: `5f217b8`.

### 3. `pnpm verify` and `pnpm verify:quick` (`testing-verify-single-command`)

`scripts/verify.mjs` (steps in `scripts/lib/verify.mjs`, runner and report in `scripts/lib/steps.mjs`) runs, in CI's order and grouped
by CI job: **api** (ruff check, ruff format --check, lint-imports, squawk over the migrations, mypy, pytest with the database
required, pip-audit); **web** (prettier --check, eslint, tsc, vitest, `contracts:check`, `test:scripts`, `test:init-template`,
`check:placeholders`, `check:links`, `test:new-module`, `build:check`, `pnpm audit --prod`, the license scanner self-test and check,
the account-free end-to-end run); **secrets** (gitleaks when installed, else SKIPPED with the reason). `--job supabase` adds
`pnpm test:supabase` (Docker; not in the default set).

- **The database is required.** `pytest --require-db` (new option in `tests/conftest.py`) ends the run once, with host and port
  (never credentials) and `pnpm db:up`, instead of skipping about half of the API tests; `pnpm test:api` now passes it, a bare
  `uv run pytest` keeps the local skip. `--no-db` allows the tests to skip and the verdict says so; steps that cannot run without
  a database are SKIPPED with the reason. The URL is `TEST_DATABASE_URL`, else the default in `conftest.py` (read from that
  file, so `init-template`'s port rewrite stays enough).
- **Preflight** (nothing is started, Docker included): Node, pnpm, uv, installed dependencies, Chromium, PostgreSQL reachable;
  each failure prints its fix command and exits 2 before any step.
- **Report**: fail fast (`--keep-going` to continue), then a table with each step's result, time and the runner's own result line
  (pytest, vitest, Playwright, `node --test`), the commit, whether the working tree is dirty, and PASS/FAIL. Exit 1 on any failure.
  On GitHub Actions each step is a group and a failure an annotation. Also `--quick`, `--only`, `--skip`, `--offline` (skips the
  audits), `--list`, `--help`.
- It pins what must not leak in from the shell: the end-to-end step runs with `E2E_SANDBOX=""` and the API tests with
  `STRIPE_SANDBOX_TESTS=""`.
- **Its first run on the merged tree found a failing test nobody had seen** (below). Commits: `f83036a`, `15902f5`.

Not done from the finding: coverage (TEST-03: no `pytest-cov`/`@vitest/coverage-v8`; the matrix row should say NOT VERIFIED until
someone adds them), making root `pnpm lint` include the web format check (`package.json` was "add only your scripts"; verify runs
both), and the "migrate twice" CLI step as a pytest test (the API tests already migrate an empty database and re-apply, so only
the CLI wrapper is CI-only; CI keeps that step).

### 4. Response conformance against the OpenAPI document (`testing-contract-conformance`)

- `tests/openapi_contract.py` (standard library only; a keyword it does not understand raises, so a new one is never silently
  ignored; objects with declared properties are closed): finds the operation a request belongs to (most literal template),
  and checks the status is declared, the content type is the declared one, and the body validates. 413 and 500 are platform
  statuses: not declared per operation, but their body must still be a `Problem`.
- `Recorder`, an ASGI middleware that `make_harness` adds to every app it builds, and a hook in `tests/conftest.py` that **fails the
  run when any response of the session does not match** (the summary lists each distinct mismatch with a count). It is
  `pytest_sessionfinish`, not a fixture, so the failure is not blamed on a random test. Partial runs check what they ran.
- A test that makes a route raise an error the route cannot raise (to test the platform around it) is no evidence about the
  contract: `@pytest.mark.contract_exempt` pauses the recorder for it. The first full run with the check found exactly one such test
  (`test_request_scope.py`, which monkeypatches the project service to raise a 409 and a 410) and nothing else: the other 1500
  tests' responses conformed. The marker has one user.
- `tests/integration/test_contract_conformance.py`: every operation signed in with representative input, signed out (401), invalid
  (400, 404, 422) and a 503 (identity provider down), and the test requires each operation of the document to succeed once.
  `tests/unit/test_openapi_contract.py` tests the checker, the document's policy (every authenticated operation declares 401 and
  503, organization routes 404, errors are `application/problem+json`) and the session hook in a subprocess.
- **The finding was true on today's tree** (run before the fix): `GET /orgs/{org_id}/projects -> 503` undeclared, errors declared as
  `application/json` and sent as `application/problem+json`, `/me` and `/entitlement` with a 200 only.
- Declarations (API): `401, 403, 503` on every authenticated router; `404` on the two organization routes that lacked it (entitlement and Pro insight); `400` on
  project list and create, checkout and the portal; `403`/`503` on LLM and diagnostics. `_Application.openapi()` documents problem
  responses as `application/problem+json`. `test_platform.py`'s 422 test follows. Regenerated contracts are a separate commit
  (`b03d0bf`); `pnpm contracts:check` and the web type check pass with them.
- **Refuted** (verifier): LLM-09 does not require a 503 from `/llm/check`; the 503 declared there is the database one.
- Commits: `8c9574f`, `b03d0bf`.

### 4a. The sandbox web server gets the page's origin (integrator request, after `web2`)

`web2`'s proxy checks a session token's `azp` against `APP_BASE_URL`/`CLERK_AUTHORIZED_PARTIES`, inlined by `next.config.ts` at build
time. In a sandbox run the page is at `http://127.0.0.1:3100`, so `playwright.config.ts` now sets both on the web server's env (one
entry: the build and start share it), as it already did for the API; without it every sandbox session would be signed out. The
unit test asserts it (mutation-checked: no origin, a different party). Account-free runs stay on `APP_ENV=test`, which `web2` says
needs nothing. Commit `c92699c`. Not runnable here: `web2` is not in this branch.

### 5. `pnpm verify:sandbox` (`testing-sandbox-procedure-manual`, the rest)

The owner's one command after pasting keys (`scripts/verify-sandbox.mjs`, logic in `scripts/lib/verify-sandbox.mjs`). It runs three
parts, each only when its keys are there, and skips the others with the reason:

| Part | Command | Runs when |
|---|---|---|
| setup | `pnpm check:setup` (`--send-test-events` only if passed) | a `.env` exists and Clerk development keys or a `*_MODE=sandbox` is set |
| stripe | `pnpm test:stripe-sandbox --require-db` | `STRIPE_SECRET_KEY` is an unrestricted `sk_test_` key, both `STRIPE_PRICE_PRO_*` are set, the test database answers; `STRIPE_SANDBOX_TESTS=0` leaves it out |
| e2e | `E2E_SANDBOX=1 pnpm test:e2e` | Clerk development keys, `DATABASE_URL` is a **local** database that answers (the run writes test rows; a hosted one is BLOCKED), Chromium installed |

It ends with the table, a **spec section 20 evidence record** (date, commit, environment, per part: command, integration mode,
outcome, status `PASS`/`FAIL`/`NOT VERIFIED`/`BLOCKED`; the integration table of `check:setup` is carried in; limitations; no secrets)
and the next steps. A suite that exits 0 but ran no test is SKIPPED, never PASS. **With nothing configured every part is SKIPPED,
the exit status is 0, and the record says NOT VERIFIED** (this is what runs here). A part that failed or could not run exits 1.
Tests assert that no key, password or URL credential reaches the output. Commit: `31626d9`.

### 6. Merge skew the new commands found on the integrated base

Three tests passed in their own packages and failed once merged; all fixed (test-only, no behaviour change):
`apps/web/src/lib/env-example.test.ts` (onboard's test called `loadEnvFiles` with the four-argument form that `config` removed:
"include is not a function"; commit `53c3cb5`), and two that expected "is malformed" for the `replace_me` publishable key where
`config`'s validators now say "is still the .env.example placeholder": `scripts/tests/preflight.test.mjs` (commit `5e868d9`, found
by `pnpm verify`) and `apps/api/tests/unit/test_doctor.py` (commit `884d165`, found by the full API run).

## New commands, scripts and variables

| Item | Notes |
|---|---|
| `pnpm verify`, `pnpm verify:quick`, `pnpm verify:sandbox` | above |
| `pnpm test:api` | now `uv run pytest --require-db` (existing script changed) |
| `pytest --require-db` | database tests required; also implied by `CI` and `TEST_DATABASE_URL` |
| `E2E_SANDBOX=1` | command line only (never from `.env`), only the value `1` |
| `E2E_CLERK_USER_EMAIL` | optional, must contain `+clerk_test`; default `e2e+clerk_test@example.com` |
| `E2E_CLERK_USER_PASSWORD`, `E2E_SANDBOX` in `.env.example` | removed |
| `STRIPE_SANDBOX_TESTS=0` | leaves the Stripe part out of `verify:sandbox` (the suite itself still needs `=1`, which its script sets) |
| `@clerk/backend` 3.23.2 | devDependency of `apps/web` (`pnpm-lock.yaml` edited by hand to match the existing resolution; `pnpm install --frozen-lockfile --offline` accepts it) |

## Manual steps (owner or adopter)

1. Account-free work needs one browser, once: `pnpm --filter web exec playwright install chromium` (CI already does; `pnpm verify`
   checks and prints this).
2. Database for the API tests: `pnpm db:up` (or `TEST_DATABASE_URL` for your own local server). `pnpm test`/`pnpm verify` fail without
   it; `pnpm verify --no-db` is the explicit exception.
3. **DEC-006, the sandbox run.** In the Clerk dashboard, on a **development** instance: enable Organizations and choose
   **Membership optional** (Organization settings; or `npx clerk@latest enable orgs` without `--force-selection`; applications created
   since 2025-08-22 default to Membership required, which the template's personal workspace does not support). Paste
   `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` and `CLERK_SECRET_KEY` into the root `.env`. Make sure no sign-up restriction blocks
   `+clerk_test` addresses (Restrictions > "Block email subaddresses" is off by default). Device Trust needs no change (the run
   signs in with a token, not a password). Point `DATABASE_URL` at a disposable local database (`pnpm db:up`, `pnpm db:migrate`).
   For the Stripe part also: a sandbox `sk_test_` key and `pnpm setup:stripe ...` (see the `stripe` notes).
4. Run `pnpm verify:sandbox`, read the table, and paste the evidence record it prints into a new file under `dev-docs/evidence/`.
   The test user is created by the run; nothing is stored.

## CI changes needed (for the `deploy` package or the integrator; I did not edit `.github/`)

`ci.yml`, **api** job (its steps run in `apps/api`; the script finds the repository itself): replace "Lint and format", "Import
boundaries", "Migration lint", "Type check", "Tests" and "Dependency audit" with one step, keep the "Migrations apply cleanly" step:

```yaml
      - name: Lint, types, tests and audit (scripts/verify.mjs)
        working-directory: .
        env:
          TEST_DATABASE_URL: postgresql://postgres:postgres@127.0.0.1:5432/app
        run: node scripts/verify.mjs --job api
```

**web** job: after `pnpm api:sync`, keep a Playwright install step, then replace every check step down to "End-to-end" (format,
lint, types, tests, contracts, init-template, placeholders, new-module, production build and bundle scan, audit, licenses, e2e)
with one; keep the report upload:

```yaml
      - run: pnpm --filter web exec playwright install --with-deps chromium
      - name: Everything the web job checks (scripts/verify.mjs)
        run: node scripts/verify.mjs --job web
```

That also adds `pnpm test:scripts` and `pnpm check:links` (the `onboard` notes asked for them) and replaces the two build steps with
`build:check`. The **secrets** job (the gitleaks action) and **api-supabase** stay. The web job's PostgreSQL service on the
repository's default test port is what `test:new-module` uses with no `TEST_DATABASE_URL`.

## When `web2` and `caps` are merged (integrator)

My branch was cut before them; these are the places my tests will say something, with the fix for each:

1. **`GET /api/v1/diagnostics/access`** (web2) is a new operation of the document, so:
   - `tests/unit/test_openapi_contract.py::test_the_committed_document_declares_what_every_authenticated_operation_can_answer`
     and the problem-details test fail until the route declares what the others do: `responses=errors(401, 403, 503)` (and the
     regenerated contract). Regenerate with `pnpm contracts`; do not hand-merge `openapi.json` or `schema.d.ts`.
   - `tests/integration/test_contract_conformance.py::test_every_operation_answers_as_the_document_says_when_signed_in` names it as
     an operation it never saw succeed. Add, next to the diagnostics lines: `sweep("GET", f"{api}/diagnostics/access", 200, headers=a)`.
     The signed-out test already covers its 401 (it walks every operation).
   - `apps/web/e2e/http.spec.ts` can then check it signed out, next to "integration diagnostics require authentication":
     `expect((await request.get("/api/v1/diagnostics/access")).status()).toBe(401)`. I did not add it: on this branch the route does
     not exist, so the check would fail here.
2. `playwright.config.ts` also merges with `config`'s sandbox-origin hunk (the same lines); keep both web and API origins.
3. `EXPECTED_PUBLIC_ROUTES` and the route walker moved to `apps/web/src/lib/app-routes.testing.ts` in web2: none of my specs imports
   them (`http.spec.ts` lists `/`, `/sign-in`, `/sign-up` literally), so nothing to change.
4. web2's web build needs `APP_BASE_URL` on a non-local `APP_ENV`; the account-free e2e (`APP_ENV=test`) and `pnpm build:check`
   (`APP_ENV=local`) are not affected. `pnpm verify` runs both.

## Documentation changes needed (integrator)

- `AGENTS.md` ("Verify before claiming anything works"), `CONTRIBUTING.md` (setup block), `.github/pull_request_template.md`,
  `README.md` (Commands and Testing rows): replace the lists with `pnpm verify` (`pnpm verify:quick` while iterating,
  `pnpm verify:sandbox` for the provider checks), and say `pnpm test`/`pnpm verify` need the database (`pnpm db:up`).
  **Keep `scripts/templates/adopter/AGENTS.md` in step** (its test checks that every `pnpm` command it names exists).
- `README.md`: the `test:e2e` row: the account-free run now opens pages in Chromium (public pages, axe, keyboard, the proxy
  redirect, the sign-in-unavailable state); `E2E_SANDBOX=1` or `pnpm verify:sandbox` for signed-in flows; the Testing line.
- `docs/integrations.md`: Clerk step 2: Organizations with **Membership optional**, with the dashboard path, the `npx clerk@latest
  enable orgs` form and why; "Verifying an integration": `pnpm verify:sandbox` and the evidence record; drop the test-user and
  password instructions (the run creates `e2e+clerk_test@example.com`); troubleshooting row: "the sandbox e2e stops with 'session is
  pending ... choose-organization'" -> Membership optional.
- `dev-docs/engineering-implementation-plan.md`: §18 P6 and §21 FE-01/FE-05/FE-07/FE-09/FE-11 and the §23 Gate B reason:
  public-page browser and axe e2e **PASS (mocked: clerk-js blocked; the Clerk-drawn controls are not scanned)**, signed-in flows
  **NOT VERIFIED** (DEC-006); API-02/DEC-007: response conformance **PASS (local)**, the
  document declares its errors; REPO-03: one command, `pnpm verify`, and CI calling it (PASS once CI does); TEST-03: **NOT
  VERIFIED**, no coverage tool; §23 next step 1: `pnpm verify:sandbox`; stop restating test counts in §23 and link the latest
  evidence record; `dev-docs/evidence/`: a record of the runs below.
- `docs/operations.md`/`docs/architecture.md` "Adding a feature": a new route's errors are declared on its router
  (`responses=errors(...)`), and the session fails if the API sends one the contract does not declare.

## Decisions taken (owner delegated; reversible)

- **A sign-in token (ticket) instead of a password** for the sandbox user, and a user created by the run. Alternative: keep password
  sign-in and document turning Device Trust off ("Protections" in the dashboard; Clerk does not document the toggle) plus an owner-made user.
- **`@clerk/backend` as a devDependency** to create the user. Alternative: no new dependency and the owner creates the user.
- **One worker in sandbox runs.** Alternative: parallel projects with per-project users.
- **The cookie-and-abort fixture rather than a faked clerk-js**: simple and honest about what it does not cover.
- **Session-wide conformance** (any test that provokes an undeclared status fails the run), not only an explicit sweep. Alternative:
  the sweep alone. A short list of platform statuses (413, 500) is allowed without a declaration; their bodies are still checked.
- **`--require-db` ends the run (`pytest.exit`)** instead of failing each test.
- **`@pytest.mark.contract_exempt`** for the two tests that patch a route to raise an error it cannot raise, instead of listing 409 and
  410 as allowed everywhere. Alternative: an allowlist of statuses (it would hide a real undeclared 409).
- **`verify:sandbox` blocks a non-local `DATABASE_URL`** (the e2e writes test rows). Alternative: a flag to allow it; run
  `E2E_SANDBOX=1 pnpm test:e2e` yourself if you mean it.
- **Not built:** an `ErrorCode` enum and exhaustive web typing (needs kernel, handler and `errors.ts` changes and a contract change of its
  own); coverage.

## Limitations and follow-ups

- Everything in the sandbox tier is unrun (see section 1); the sandbox-mode guard (CSP, page errors, failing first-party
  requests) will surface any real violation on the first run; each names the directive or URL.
- `pnpm verify` takes the sum of its steps: here the API suite 4.5 to 6 minutes, the end-to-end run 30 s to 2 minutes, the rest about
  3 minutes. `--quick` is the iteration loop.
- **`pnpm verify` did not run as one command here.** I ran all of its steps except the audits (network: `--offline` exists for
  that), gitleaks (not installed) and `--job supabase` (Docker), in three pieces through `verify` itself and the full pytest and the
  end-to-end run through their own commands (evidence below). The preflight, the table and the exit codes are unit-tested and
  were seen on real output.
- The stale-bytecode trap when mutation-checking Python: restoring a same-size file within the same second leaves a `.pyc` that
  Python trusts (`max` to `min` made a passing test fail on the unmutated file). Delete `__pycache__` after each mutation.
- The eslint guards (`eslint.guards.mjs`) are not wired into `eslint.config.mjs` yet (spec §23 step 4). When they are, add
  `src/lib/env-example.test.ts`, `src/lib/env-cases.test.ts`, `src/playwright-config.test.ts` and `src/e2e-support.test.ts` to
  `serverConfigTests` (they import server-only configuration modules), or lint will fail them. A lint-config edit is blocked for agents.
- `docs-notes/` is removed from an adopter's copy by `init-template`.

## Evidence (`local` or `mocked`; nothing `sandbox` or `live`)

Machine: heavily loaded for most of the work (load average 330 to 440), quieter at the end. PostgreSQL 17 `arch-pg` for the API runs.

| Command (from the worktree) | Result |
|---|---|
| `uv run pytest --require-db -q -p no:cacheprovider` (full API suite, run 1, before the exemption markers) | 1536 passed, 5 skipped, 1 failed (`test_doctor.py`, merge skew, fixed) and 3 responses reported by the new session check (one synthetic-error test, exempted) |
| same (full API suite, run 2) | **1539 passed, 5 skipped**, 4 min 27 s; the session check reported 1 response (the second synthetic-conflict test); that case is now marked, and `test_outbox_jobs.py` + `test_request_scope.py` + the contract tests then passed (80) with nothing reported. A third full run was not made (two allowed) |
| `node scripts/verify.mjs --only api-lint ... licenses --keep-going` (15 steps: ruff, format, imports, squawk, mypy, prettier, eslint, tsc, vitest, scripts tests, contracts, placeholders, links, license self-test and check) | all PASS, 25.8 s: web **647 passed, 1 skipped (648)**, scripts **95 tests, 95 pass** |
| `TEST_DATABASE_URL=... node scripts/verify.mjs --only init-template --only new-module --only build` | all PASS, 1 min 15 s (the generator run uses the changed module template) |
| `e2e-locked.sh` run 1 (new tests, `--retries=0`) | 56 passed, 14 skipped |
| `e2e-locked.sh` run 2 (`pages.spec.ts` x3, plus 7 temporary mutants) | pages 48 passed; every mutant failed with its intended message except one that the first version of the guard missed (a skipped handshake; fixed, see below) |
| `e2e-locked.sh` run 3 (full suite plus the mutants) | 58 passed (56 real + the skipped-handshake mutant on both projects, which the guard still missed), 12 failed (6 mutants x 2 projects), 14 skipped |
| side run: a temporary Playwright config against a `next start` of the same build on port 3199 (not the fixed ports; the run-3 build) | the 8 `pages.spec.ts` tests passed; the 7 mutants all failed, the skipped-handshake one now with "the browser was sent to clerk.not-configured-yet.invalid: Clerk's development handshake was not skipped" |
| `e2e-locked.sh` run 4, final, committed state, `--retries=0` | **56 passed, 14 skipped**, 29.5 s |
| `pnpm verify:sandbox` with no `.env` | all three parts SKIPPED, evidence record printed, exit 0 |
| `pnpm contracts:check`, `pnpm check:placeholders`, `pnpm check:links` | pass |
| `pnpm install --frozen-lockfile --offline` after the hand edit of `pnpm-lock.yaml` | "Lockfile is up to date" |

Not run: anything against Clerk, Stripe or any provider; `pnpm audit` and `pip-audit` (network); gitleaks (not installed); `pnpm
test:supabase` (Docker); `pnpm verify` as a single command.

**Mutation checks** (the change reverted or broken, the new test seen failing, then restored). Web unit: the sandbox switch accepting
any value, the loader taking every variable or `E2E_SANDBOX`, no precondition throw, parallel workers, no setup dependency, a
placeholder key that drifts from `.env.example`, the unauthorized origin (API and web server), live keys accepted, any e-mail
accepted, a placeholder host not recognised, the race in `ensureTestUser`, always creating, a required password, the unnamed
Membership setting, a stack that is not trimmed. E2E (temporary spec): a skipped handshake, aborted app scripts, a blocked script,
a 404 asset, an uncaught error, an injected axe violation. Scripts: `--quick` ignored, no fail-fast, `--require-db` dropped, no
database check, the end-to-end switch and the Stripe suite not pinned off, skips hidden in the verdict, a failure that exits 0,
credentials kept, a preflight failure that runs anyway, the Supabase job in the default set; for `verify:sandbox` a restricted key
accepted, a remote database accepted, zero passed counted as a pass, the sandbox switch on every part, `--require-db` dropped, a key
or database password echoed (three ways), a failure that exits 0, `.env` not required, test events not passed through, the
`STRIPE_SANDBOX_TESTS=0` opt-out ignored, a blocked part that does not fail, a skipped part given a mode. API: `--require-db`
dropped, `pytest.fail` instead of `pytest.exit`, the URL printed; the checker (open objects, unknown keywords ignored, no status
check, no content-type check, no body validation, booleans as integers, the least literal template winning), the recorder
(not attached, dropping the body), the session hook (no exit status, no summary), and eight edits of the committed document
(a dropped 503, 409, 404 or 400; a problem declared as `application/json`; an undeclared property; a narrowed enum; a wrongly typed
field), each failing the test that should; the missing marker on the conflict case fails the session.

**Flakes:** none found in the code under test. Three repeats of the browser tests, two full API runs under load average 330 to 440 and
four end-to-end runs each passed what they should. Two things that looked like flakes were mine: a mutation harness leaving stale
bytecode (above), and two waiting loops of mine polling a log at a wrong relative path (stopped).
