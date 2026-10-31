# Evidence: Cycle 3 and Fix Round 3 (2026-10-31)

| Field | Value |
|---|---|
| Scope | The cycle 3 review of `73e14e8` (tag `arch-fixround2-complete`: three component reviewers, G, H and I, and one independent session), the three fix round 3 packages (`billing4`, `tooling3`, `web4`, cut from `73e14e8`) merged into `arch/integration` (`21332fe`), and the last documentation pass (`docs3`: [ADR-0004](../adr/0004-cycle-1-adopted-decisions.md) §27 to §29 and the passages they changed, the specification, `docs/`, this record). It continues [the cycle 2 record](2026-10-31-round-1b-cycle-2-fix-round-2.md) |
| Method | The reviewers read the snapshot read-only and ran their mutations and experiments in scratch copies. Each fix package verified its findings against the code first, fixed what was true with a regression test, and checked each new test by **mutation**: the guarded code was broken, the test was seen failing, the code was restored. The counts below are the ones each package or reviewer reported, produced on its own branch or copy; they are not comparable with each other. What the documentation pass ran is in [The documentation pass](#the-documentation-pass) |
| Environment | macOS (Darwin arm64); Node 25.8.2 (the repository pins 24 in `.nvmrc`; `engines` allows 22.12 or newer), pnpm 9.15.0, uv 0.11.1. PostgreSQL 17 in a shared local container for the API tests. The machine was busy for most of the work, so timings include that |
| Integration mode | `mocked` (test doubles, loopback stubs, captured transports) and `local` (a command run on this machine against local services: a local database, a container, a build, a scratch copy of the tree). **Nothing is `sandbox` or `live`.** No Clerk, Stripe, Supabase, Vercel, PostHog, Sentry, GitHub or LLM account was used |
| Remote actions | None intended. Nothing was pushed, deployed, published or changed on a remote service. Package registries and the npm advisory endpoint were used by installs and audits, and reviewer H started a Docker PostgreSQL container for a clean-clone walkthrough and removed it afterwards. The reviewers and this pass read public documentation pages (Stripe, Clerk). **One disclosed accident:** reviewer I ran the Vercel CLI with an unknown subcommand and no `--help`, which started the CLI's device-login flow once: one request to vercel.com's OAuth endpoint, nothing authorized or stored (an isolated home directory, deleted). This pass ran the pinned Vercel CLI only as `--version` and `--help`, with an empty home directory, telemetry off and no login |

## Labels

- `mocked`: a test double stands in for the provider. It proves behavior against our model of the provider, never connectivity.
- `local`: a command run on one machine against local services. It is not `sandbox`.
- `sandbox` and `live` appear in this record only as things that were **not run**.
- `NOT RUN`: a test or command exists and was not run. `NOT VERIFIED`: no sufficient evidence exists. The two are never PASS.

## Cycle 3 review

The reviewers read the snapshot `73e14e8` and ran their experiments in scratch copies. The independent session's full text is an external working note, not part of the repository.

| Reviewer | Area | What was run |
|---|---|---|
| G | API: money, lifecycle, limits, jobs | 62 mutations of the code fix round 2 added (53 killed, 9 survived); a 60,000-state fuzz of `decide`, `summarize` and `checkout_block` (no invariant broken); a route inventory; `runtime_url_template` on 9 URL shapes |
| H | Web, tooling, CI, the generator; an adopter's clean-clone walkthrough | On a Docker PostgreSQL 17.6 (port 54378, removed afterwards): `init-template --db-port`, `bootstrap`, `db:up`, `db:migrate` twice, `check:setup`, `pnpm dev` stopping at the placeholder Clerk keys, API health and ready, `db:reset`. `pnpm new:module widgets`, then migrate, contracts, `test:api` (2254 passed) and `test:web` (1041 passed); a second module `gadgets`, then `verify:quick` (10 steps; 2282 and 1066 tests); 30 plausible, hyphenated and colliding module names; a ZIP tree without `.git`; CRLF migrations; a cold pnpm store (18 s) and a cold uv cache (2.5 s). 55 web mutations (52 killed), 2 generator mutations, 1 CI workflow mutation |
| I | Documentation accuracy and the owner's path | About 90 claims audited, about 35 traceability rows, 93 variable names and 148 ADR identifiers checked; `init-template`, `migrate`, `check:setup`, `dev:api`, `seed`, `reset` and a build run on a scratch copy |
| Independent session | Cross-cutting, by flow | 110 mutations of the code that the cycle 2 tests guard (money, tenancy, privacy, the login role, rate limits, headers, tooling): 101 killed, 9 survived. The ID-length-floor survivors are not findings: floors 15 and 20 are killed, and real IDs have 14 or more characters. A fresh flow trace of what is new; a generator trace (2256 API and 1041 web tests passed). Verdict: the fix round holds, and nothing blocks the owner's goal |

Findings logged: 26 rows, none critical and none high.

## Cycle 3 findings by disposition

Counted from the cycle 3 ledger (a working file outside the repository: one row per finding, with the reviewer's evidence and proposed fix). The ledger's status column was not updated after the fix round, so the dispositions are read from the merged commits and from this documentation pass. A row counts as fixed in code or tests when a commit on `arch/integration` names it, and as fixed in the documents when a commit of this pass does. **No row is recorded as refuted or deferred**: no outcome that reached this record refuted or deferred a claim, and if one did, its package report holds the reason. `c3-0f-F-01` and `c3-G-1` are one finding that two reviewers made, and share a disposition.

| Severity | Rows | Fixed in code or tests | Fixed in the documents | Closed, no action |
|---|---|---|---|---|
| Medium | 6 | 5 | 1 | 0 |
| Low | 17 | 11 | 6 | 0 |
| Note, nit, non-finding | 3 | 1 | 1 | 1 |
| **Total** | **26** | **17** | **8** | **1** |

"Fixed in code or tests" includes rows whose documents the package or this pass completed (the Stripe passages of [operations.md](../../docs/operations.md#a-user-deleted-in-clerk) and [limitations.md](../../docs/limitations.md#assumptions-not-verified), the data inventory's Clerk row, the specification rows). The one closed row is the ID-length floor (`c3-0f-nonfinding`).

### Medium rows, with the commits that fixed them

| Row | Fixed by |
|---|---|
| `c3-G-1`, `c3-0f-F-01` (the Checkout Session a deleted user left open can still be paid, and what it buys is never ended; an `incomplete` subscription paid after the deletion is the same hole) | `5e062f6` |
| `c3-G-2` (the sandbox suite never reached the Stripe calls of fix round 2; a deleted customer whose listing answers with a list, not a rejection, left the user's checkout at `400` for good) | `c268808` (the retry), `6fd0755` and `43d3b57` (sandbox tests, NOT RUN) |
| `c3-I-1` (the A3 list omits `pnpm verify:sandbox`, which creates a Clerk user and runs the Stripe lifecycle suite) | this documentation pass |
| `c3-H-1`, `c3-H-2` (the generator check failed late and confusingly on files git does not track, and passed with a module's own tests skipped) | `999c6d3` |
| `c3-H-3` (the CI pin test ignored job-level keys such as `if`) | `3204377` |

### Low rows and the rest

| Row | Fixed by |
|---|---|
| `c3-0f-F-02`, `c3-G-3` (guards that no test pinned: the customer lock of a replacement, the `billing_customer_relinked` log, 22 of 36 personal-data words, and the other survivors of the reviewers' mutations) | `12303be`; `8052a07` gave the lock test the headroom its neighbours have |
| `c3-G-4` (the Stripe check passes without write access to Subscriptions) | `8d33455` |
| `c3-0f-note` (a status Stripe adds later was stored as `incomplete` and could take Pro from a payer) | `7b3fb5d` ([ADR-0004 §27](../adr/0004-cycle-1-adopted-decisions.md)) |
| `c3-I-4` (dependency install scripts ran on machines that hold provider keys; the deferral gave a half-invalid reason) | `0ce4a4c`, with tests `86d6916` and `b8a337c` ([ADR-0004 §28](../adr/0004-cycle-1-adopted-decisions.md)) |
| `c3-I-5` (Clerk SDK telemetry: the CSP allowed its host, the data inventory said otherwise) | `d27180b`, `599a669`, with tests `7534653` and documents `f719573` ([ADR-0004 §29](../adr/0004-cycle-1-adopted-decisions.md)) |
| `c3-H-4` (`pnpm build:check` passed the shell's environment to the build) | `9baba78` |
| `c3-H-5` (the command that fixes a stale `uv.lock` could not run from the repository root) | `629888c` (`pnpm api:lock`) |
| `c3-H-6` (a migration whose line endings changed was reported as edited) | `dfeb0cf` |
| `c3-H-7` (the proxy timeout was unasserted) | `112b214` |
| `c3-I-10` (the checklist claimed it "cannot drift" and nothing compared it with the guide; S12 said "either way") | `0d20358` (a test in `pnpm test:scripts`), and this pass (S12) |
| `c3-I-2`, `c3-I-3`, `c3-I-6`, `c3-I-7`, `c3-I-8`, `c3-I-9`, `c3-I-note` | this documentation pass (below) |

## Fix round 3 by package

Three packages, cut from `73e14e8`. The decisions they adopted are [ADR-0004 §27 to §29](../adr/0004-cycle-1-adopted-decisions.md). Every figure is the package's own, `local` or `mocked`.

| Package | Commits | Results | Not run |
|---|---|---|---|
| `billing4` | 11 | API 2296 passed, 9 skipped (the sandbox suite, now 9 tests). About 70 mutations, 11 reproduced survivors, all killed. The unknown-status case was reproduced first through the real adapter, a loopback Stripe and the database (plan `pro` became `free`); now it stays `pro`. New provider methods: expire a customer's open Checkout Sessions, cancel an `incomplete` subscription at once | Four new sandbox tests (collected, skipped without keys). What listing a deleted customer's subscriptions or sessions answers, `DELETE /v1/subscriptions/{id}` on an `incomplete` subscription, and the card-only Checkout parameters stay NOT VERIFIED against Stripe |
| `tooling3` | 11 | API 2244 passed, 5 skipped (4 min 30 s); web 1016 passed, 1 skipped; scripts 128 passed; `pnpm test:new-module` 6 min 52 s; account-free end-to-end 56 passed, 14 skipped; `pnpm test:vercel-build` 13 checks. Mutation checks: the CI pin (the old test stayed green for 6 of 8 job-level mutations, the new one fails all 8), the template skips (the old floor passed both), the untracked-file stop, the migration checksum message, `api:lock`, the manual-steps test, the install-scripts test. In scratch clones a fresh frozen install ran 3 scripts (`core-js`, `esbuild`, `unrs-resolver`); with `pnpm.onlyBuiltDependencies: []` it runs none, the lockfile is byte-identical, and web lint and tests, `build:check`, the end-to-end run and `test:vercel-build` pass | Remote CI (never run); that Vercel's own installer honors the field |
| `web4` | 5 | Web 1021 passed, 1 skipped; `build:check` passed; end-to-end 56 passed, 14 skipped. `<ClerkProvider telemetry={false}>` plus Clerk's documented `NEXT_PUBLIC_CLERK_TELEMETRY_DISABLED` as a build-time constant in `next.config.ts` (a local probe showed the proxy's server-side Clerk client printed the telemetry notice without it); `https://clerk-telemetry.com` left the CSP; the proxy timeout is pinned | That the clerk-js file Clerk's CDN serves honors `telemetry={false}` (the public source was read) |

## Verification of the merged trees

| Tree | Result |
|---|---|
| `73e14e8` (the base of fix round 3) | The full `pnpm verify` passed on it (reported by the integrator in the fix round 3 brief; not re-run here) |
| `21332fe` (all three packages merged) | **Not recorded.** No result of a full `pnpm verify` of this tree was supplied to this pass, and this pass did not run one. The integrator's run is to be recorded here |

## The documentation pass

Run in `arch/docs3` at `21332fe` plus the documentation commits, label `local`. Checks that read documents:

| Command | Result |
|---|---|
| `node --test scripts/tests/manual-steps.test.mjs` (the agreement of the checklist and the guides; the shell command that `docs/setup.md` used to give was replaced by this test in fix round 3, and it also runs inside `pnpm test:scripts`) | 2 tests, 2 pass |
| `pnpm check:links` | Every relative Markdown link, its `#fragment` and the `docs/…#fragment` pointers in code resolve |
| `pnpm check:placeholders` | Exit 0 (template mode: the two contact placeholders are present, as expected) |
| `pnpm test:init-template` | Passed, 6.6 s (the copy renames, applies the adopter policy once, and every link in it resolves) |
| `pnpm test:scripts` | 128 tests, 128 pass |
| The tests that read `.env.example` and other documents: the web `next-config`, `e2e-support`, `env-example` and `env-example-parity` tests, and the API `test_env_example.py`, `test_vercel_config.py`, `test_doctor.py`, `test_analytics_events.py`, `test_clerk_verifier.py`, `test_write_limit.py` and `test_diagnostics_cli.py` (against the shared test server) | Web 4 files, 64 tests passed; API 238 passed, and `test_env_example.py` alone 20 passed |

Not run in this pass: the API and web suites as a whole, `pnpm verify`, `pnpm build:check`, `pnpm test:new-module` and the end-to-end tests. No code, test, script, workflow or lint configuration changed; one document template under `scripts/templates/adopter/` (`AGENTS.md`) was edited to keep it in step with `AGENTS.md`.

What this pass did on scratch copies of the tree (outside the repository, `local`):

| What | Result |
|---|---|
| `pnpm init-template` on a clean copy, then again with the same name and slug and `--db-port 54330` | The second run moved the port in `.env.example`, `docker-compose.yml`, `.github/workflows/ci.yml`, `README.md`, `docs/integrations.md`, `apps/api/tests/conftest.py`, `apps/web/playwright.config.ts` and `perf/server.py`, needed no contact options, and printed that the existing `.env` was not changed (`c3-I-3`) |
| `node scripts/verify.mjs --only new-module` on the initialized copy | The preflight stopped: "1 file is not in git yet (dev-docs/evidence/README.md)". `pnpm init-template` creates that file. The setup guide now says `git add -A` after the rename (found on the owner's path, not a ledger row) |
| The pinned `vercel@65.2.0`, as `firewall rules --help`, `firewall rules add --help`, `firewall rules update --help` and the `publish`, `diff` and `discard` help, with an empty home directory, telemetry off and no login | `rules` has `list`, `inspect`, `add`, `update`, `enable`, `disable`, `remove` and `move`; `update` takes `--rate-limit-action` (`c3-I-7`). No account was contacted |
| `isRequestEligibleForHandshake` in the installed `@clerk/backend` 3.23.2 (`dist/internal.js`) | A request is handshaken only if it is a GET and carries `Sec-Fetch-Dest: document` or `iframe`, or no such header and an `Accept` that starts with `text/html`. A plain `curl -sI` is a HEAD request, so it is answered with the sign-in redirect (`c3-I-note`) |
| A production build of the web app (`next build`, then `next start` on a private port, the placeholder Clerk pair, `APP_ENV=local`, no `.env` read; the server was stopped afterwards), probed with `curl` | `curl -sI /dashboard`: `307` to `/sign-in?redirect_url=...` with `x-clerk-auth-status: signed-out`. A GET with `Accept: text/html`: `307` to `https://clerk.not-configured-yet.invalid/v1/client/handshake?...` with `x-clerk-auth-status: handshake`. A HEAD with `Accept: text/html`, and any request with a `__clerk_db_jwt` cookie: `/sign-in`. This is the answer the guide's check 1 now describes. Reviewer I's `next dev` log shows a `404` for a HEAD of `/dashboard`; this pass did not run the development server, so that difference is not explained here (the first preview is a production build) |

Public pages read by this pass (2026-10-31): [Stripe: webhooks](https://docs.stripe.com/webhooks) for the Dashboard route to a signing secret (Workbench, the Webhooks tab, **Create an event destination**, **Reveal secret**), and [Clerk: telemetry](https://clerk.com/docs/guides/how-clerk-works/security/clerk-telemetry) for the opt-out (`telemetry={false}`, `NEXT_PUBLIC_CLERK_TELEMETRY_DISABLED=1`). The Stripe labels are from the page and were not seen in a Dashboard.

### Rows closed by this pass

| Row | What changed |
|---|---|
| `c3-I-1` | The A3 row of the checklist and the Authorizations item name `pnpm verify:sandbox` and `E2E_SANDBOX=1 pnpm test:e2e`; the Part 2 closing step is marked (A3); the README, `docs/integrations.md`, `AGENTS.md` and `CONTRIBUTING.md` say the sandbox runs change accounts |
| `c3-I-2` | Step 4 of S8 says that a hosted `DATABASE_URL` makes the browser part of `pnpm verify:sandbox` BLOCKED (exit 1), and gives the two ways out. Read in `scripts/lib/verify-sandbox.mjs`; not run, as there is no hosted database |
| `c3-I-3` | The port fallback re-runs `init-template` with `--db-port`, or sets `TEST_DATABASE_URL` |
| `c3-I-6` | S7 and the secret step describe the Dashboard route to a `whsec_` secret and say the labels are NOT VERIFIED in a Dashboard. The same step now lists `pnpm verify:sandbox` among the commands that need the secret in `.env` (its browser part starts the API from `.env`, which requires `STRIPE_WEBHOOK_SECRET` once `BILLING_MODE` is `sandbox`: read in `apps/api/app/modules/billing/settings.py`) |
| `c3-I-7` | `vercel firewall rules update`, not `edit` |
| `c3-I-8` | `pnpm build` with the shipped placeholder pair builds (`apps/web/src/lib/env-example.test.ts` loads the shipped values through the build's validator); only a deployed build needs real keys |
| `c3-I-9` | Three optional checks after the numbered ones in [Verify the first preview](../../docs/deployment.md#questions-the-first-preview-can-also-answer): whether the Framework Preset is required, whether the web service receives the API's secrets, and performance on a preview. [limitations.md](../../docs/limitations.md#assumptions-not-verified), the specification status and this record carry the same NOT VERIFIED list |
| `c3-I-10` | S12 says the subscription ends only once the Clerk webhook (D12) reaches the API, which a local sandbox has not; the checklist paragraph says the test compares IDs, not wording |
| `c3-I-note` | The handshake wording of check 1 and of Part 3 step 6 |

Also brought up to the final code: [operations.md](../../docs/operations.md) (the deleted-customer passage, the line-endings message), [limitations.md](../../docs/limitations.md) (the Clerk telemetry premise and decision, the unknown-status and write-access limits, the untracked-file limit), [data-inventory.md](../../docs/data-inventory.md) (the stored session row and the deletion checklist), [architecture.md](../../docs/architecture.md) (the billing bullets), [integrations.md](../../docs/integrations.md), `.env.example` (the `BILLING_ON_USER_DELETED` comment), `README.md` (the command table), the specification (§21 and §23) and [ADR-0004](../adr/0004-cycle-1-adopted-decisions.md) (the header, the consequences of §28, the open-for-the-owner paragraph).

## Not run, and why

| What | Why | What would verify it |
|---|---|---|
| Every sandbox suite: `pnpm test:stripe-sandbox` (9 tests), `pnpm verify:sandbox`, the signed-in end-to-end tests, `pnpm check:setup` against real providers | No Clerk, Stripe, PostHog, Sentry or LLM keys exist in this environment | The owner pastes sandbox keys into `.env` and runs `pnpm verify:sandbox` (authorization A3); record the printed record here as `sandbox` (DEC-006) |
| Six Stripe calls that only stubs have answered: card-only Checkout, retrieving a deleted customer, `subscriptions.list(limit=1)`, `cancel_at_period_end`, listing a customer's open Checkout Sessions, and cancelling an `incomplete` subscription; what Stripe answers when the subscriptions or sessions of a deleted customer are listed | Only loopback stubs have answered | The Stripe sandbox suite and `pnpm check:setup` with a real sandbox key |
| A Vercel preview deployment, and what only it shows: the proxy inside a service (with the web app as a Service), the Framework Preset, the shared environment, credentials same-origin under Deployment Protection, the router, limits, Instant Rollback, HSTS on the API, that Vercel's installer honors `pnpm.onlyBuiltDependencies`, and the other platform premises of [limitations.md](../../docs/limitations.md#assumptions-not-verified); the crons need a production deployment | Needs the owner's authorization and a Vercel account | The first-preview order in [deployment.md](../../docs/deployment.md#the-first-preview-in-order), its numbered checks and its optional questions |
| Hosted Supabase pooler behavior (Supavisor routing for `app_login.<ref>`, port 6543, SCRAM, TLS) | No hosted project | `pnpm db:api-login --allow-remote` with a non-production project, then `pnpm test:supabase` with a newer image |
| That the clerk-js Clerk's CDN serves honors `telemetry={false}`; the Stripe Dashboard labels of the webhook route | Needs a Clerk instance and a Stripe Dashboard | The sandbox run (Clerk's network log) and a look at the Dashboard |
| Whether Clerk issues a session token with a custom `aud` (`CLERK_AUDIENCE`), the default of "Allow users to delete their accounts", Clerk's handling of an inbound `redirect_url`, the sign-in-token flow of the sandbox run, and a page of the web app in a browser with the placeholder Clerk pair; real PostHog and Sentry delivery and source-map upload | Needs a Clerk instance and PostHog and Sentry projects | The sandbox run, `pnpm check:setup --send-test-events` (authorization A3), and reading the Dashboards |
| GitHub Actions (every workflow), CodeQL, the secret scan in CI, the migration workflow, branch protection | Nothing was pushed; remote CI has never run | Authorize a push, then read the first CI run |
| gitleaks in `pnpm verify` | Not installed on this machine | Install gitleaks and run `pnpm verify` |
| The full `pnpm verify`, `pnpm build:check`, `pnpm test:e2e`, `pnpm test:new-module` and the API and web suites on the documentation tree | This pass changed documents only | The integrator's run on the merged tree |
| `proofs/dec-001/run.sh` | Needs Docker Desktop and about ten minutes; last passed at `966d3b3` | Run it once on a quiet machine after `pnpm db:up` |
| A clean-cache run of the onboarding flow (REPO-11) | The recorded runs had warm caches (reviewer H's cold-store figures are for `pnpm install` and `uv sync` only) | A fresh container or VM, following only the README |
| The strict web lint guards | Wiring them means editing `apps/web/eslint.config.mjs`, which agents may not change (specification §23, step 4) | The owner applies the change |
