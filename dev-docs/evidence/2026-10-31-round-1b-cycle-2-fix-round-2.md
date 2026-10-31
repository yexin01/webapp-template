# Evidence: Round 1b, Cycle 2 and Fix Round 2 (2026-10-31)

| Field | Value |
|---|---|
| Scope | The four round 1b packages (`polish`, `billing2`, `envparity`, `limits`, cut from `f1b54e9`), the cycle 2 review of `dd12f44` (six component reviewers and one independent session), the six fix round 2 packages (`web3`, `apitests`, `obs2`, `tooling2`, `dbtests`, `billing3`, cut from `dd12f44`) merged into `arch/integration` (`ecfd9d8`, then `1729bd0`), and the documentation pass that followed (this record, [ADR-0004](../adr/0004-cycle-1-adopted-decisions.md) §16 to §26, the specification, `docs/`). It continues [the cycle 1 record](2026-10-30-cycle-1-fix-round.md) |
| Method | Each package verified its findings against the code first, fixed what was true with a regression test, and checked each new test by **mutation**: the guarded code was broken, the test was seen failing, the code was restored. The cycle 2 reviewers did the same in scratch copies, to find tests that could not fail. The counts below are the ones each package or reviewer reported, produced on its own branch or copy; they are not comparable with each other. What ran on merged trees is in [Verification of the merged trees](#verification-of-the-merged-trees) |
| Environment | macOS (Darwin arm64); Node 25.8.2 (the repository pins 24 in `.nvmrc`; `engines` allows 22.12 or newer), pnpm 9.15.0, uv 0.11.1. PostgreSQL 17 in a shared local container for the API tests. The machine ran under heavy load for most of the work (a load average of several hundred at times), so timings are inflated and wall-clock-sensitive tests were given more room in places |
| Integration mode | `mocked` (test doubles, loopback stubs, captured transports) and `local` (a command run on this machine against local services: a local database, a container, a build). **Nothing is `sandbox` or `live`.** No Clerk, Stripe, Supabase, Vercel, PostHog, Sentry, GitHub or LLM account was used |
| Remote actions | None intended. Nothing was pushed, deployed, published or changed on a remote service. Package registries and the npm advisory endpoint were used by installs and audits. The reviewers and the documentation pass read public documentation pages (Vercel, Clerk, Stripe, Supabase, GitHub), and reviewer E checked the CI pins against the GitHub API (read-only) |

## Labels

- `mocked`: a test double stands in for the provider. It proves behavior against our model of the provider, never connectivity.
- `local`: a command run on one machine against local services. It is not `sandbox`.
- `sandbox` and `live` appear in this record only as things that were **not run**.
- `NOT RUN`: a test or command exists and was not run. `NOT VERIFIED`: no sufficient evidence exists. The two are never PASS.

## Round 1b

Four packages, cut from `f1b54e9`, merged before `dd12f44`.

| Package | What changed | Results (`local` or `mocked`, on the package branch) |
|---|---|---|
| `polish` | 9 items | `pnpm verify:quick` 10 of 10 steps; API 1692 passed, 5 skipped (the opt-in Stripe sandbox suite); web 858 passed, 1 skipped; `pnpm test:supabase` on `test_maintenance_commands.py`: 17 passed. Coverage reporting added without thresholds (API 96% statements, branches included, and web 94.2% statements were measured; the figures are not recorded as evidence). Mutation checks: the rendering-baseline guard, the `tunnelRoute` test, the lint format check, the migrate-twice test |
| `billing2` | Pro granted by the Stripe product (option B, [ADR-0004 §16](../adr/0004-cycle-1-adopted-decisions.md)) and feature entitlements | API 1742 passed, 5 skipped; about 35 mutations each caught; the sandbox suite collects 5 tests and skips (NOT RUN) |
| `envparity` | `.env.example` completeness tests (API and web; 16 drift cases run permanently), `CLERK_JWT_KEY` in the proxy, setup messages, a PEM-aware bundle scan (the old scan did not see a planted PEM copy) | API 1718 passed, 5 skipped; web 873 passed, 1 skipped; `pnpm build:check` compared 8 values; `pnpm test:vercel-build` compared 18 values in 13 checks |
| `limits` | A per-user write limit and single-flight, spaced jobs (migration `20261031000050_job_runs.sql`, [ADR-0004 §17](../adr/0004-cycle-1-adopted-decisions.md)) | API 1727 passed, 5 skipped; new tests `test_write_limit.py` (17), `test_job_guard.py` (12), `test_limit_settings.py` (7); 8 write-limit mutations, 6 job-guard mutations, and a generator mutation caught by `pnpm test:new-module`. The first placement of the counter, before the request gate, failed a 16-write burst test and was moved inside the admitted request |

## Cycle 2 review

Reviewers read the snapshot `dd12f44` read-only and ran their mutations and experiments in scratch copies. The independent session's full text is an external working note, `/private/tmp/claude-501/-Users-xinye-Documents-GitHub-webapp-template/35c1d41a-7a2b-4ce4-a1d3-b330a3804d83/scratchpad/cycle2-findings.md`, not part of the repository.

| Reviewer | Area | What was run |
|---|---|---|
| A | Database, tenancy, identity | About 25 mutation runs; about 700 tests |
| B | Billing, webhooks, jobs, rate limits | 3 source mutants and 2 mocked experiments; 103 billing tests |
| C | Web app, access control, Clerk | 66 single-line mutations, each against the full web suite (887 tests) |
| D | Observability, privacy | 159 API and 139 web telemetry tests; mocked wire experiments with the real SDKs |
| E | Tooling, onboarding, CI, cold start | A cold start in a fresh clone (slug `c2e-probe`, database port 54377), and the CI pins checked against the GitHub API |
| F | Deployment and documentation accuracy | About 25 Vercel pages and the Clerk, Stripe, Supabase and PostHog pages; 112 documented variable names, 329 paths, every documented `pnpm` script and 78 cited test files checked against the tree; 11 planted defects in the Vercel build checker (all caught) |
| Independent session | Cross-cutting, by flow | 122 requirement IDs checked (91 OK, 23 WEAK, 8 DRIFT); 9 mutations (M1 to M9), of which 6 or more survived the suite and became findings; positive controls showed the harness ran the mutated code |

The cold start at `dd12f44` (reviewer E; warm caches, so not a clean-cache figure): `pnpm init-template` 0.9 s, `pnpm bootstrap` 10.5 s, `pnpm db:up` 3.7 s, `pnpm db:migrate` 5.8 s, `pnpm check:setup` 8.7 s, `pnpm dev:api` with `curl`, `pnpm dev` stopping at the Clerk keys, `pnpm build:check` 33 s, `pnpm db:seed` and `pnpm db:reset`, `pnpm verify:quick` 6 min 1 s (API 1804 passed, 7 skipped; web 887 passed, 1 skipped). Each step behaved as documented. The reviewer re-recorded REPO-11 as passing `local` except for the Clerk keys; the specification records it as PARTIAL because the environment was not isolated.

Findings logged: 66 rows. None critical.

## Cycle 2 findings by disposition

Counted from the cycle 2 ledger (a working file outside the repository: one row per finding, with the reviewer's evidence and proposed fix). The ledger's status column was not updated after the fix round, so the dispositions below are read from the merged commits and from this documentation pass. A row counts as fixed when a commit on `arch/integration` names it, or, for a document, when a commit of the documentation pass (branch `arch/docs2`) does. **No row is recorded as refuted**: no package's outcome that reached this record refuted a claim, and if one did, its package report holds the reason. Three rows are duplicates of another (`docs-bill-13-pass-without-measurement`, `init-template-leaves-dangling-pointers`, `0f-F-12`) and share its disposition.

| Severity | Rows | Fixed in code or tests | Fixed in the documents | Refuted | Deferred |
|---|---|---|---|---|---|
| High | 2 | 2 | 0 | 0 | 0 |
| Medium | 23 | 20 | 3 | 0 | 0 |
| Low | 38 | 29 | 9 | 0 | 0 |
| Not rated (reviewer "not checked", to verify) | 3 | 2 | 1 | 0 | 0 |
| **Total** | **66** | **53** | **13** | **0** | **0** |

"Fixed in code or tests" includes rows whose documents were completed by the fix round package or by this pass (for example 0f-F-01, 0f-F-09 and 0f-F-10). Rows that were fixed in documents only: the first-preview order, BILL-13 in the matrix (two rows), what `pnpm verify` runs, the DEC-001 status, the function duration ceiling, the data inventory (the processors and flows, and the active-refresh key), the platform citations and labels, the Protection Bypass secret's scope, the count drift, the repository-level secret warning, and the claim about a `*.vercel.app` domain and Clerk.

Deferred after the fix round (found while merging, not rows of the ledger): operator runs of `POST /api/v1/llm/check` are not audited (ADM-01); Sentry events still carry `server_name` and `extra.sys.argv` by SDK default; an ID shorter than ten characters after its prefix is not replaced in Sentry paths (documented as a limit). All three are in [limitations](../../docs/limitations.md#known-limits-by-area). The optional test that ties a configured `maxDuration` to the two 900 s leases was not built.

### High and medium rows, with the commits that fixed them

| Row | Fixed by |
|---|---|
| `new-module-leaves-pnpm-test-red` (high) | `497862f` |
| `web-credentials-omit-breaks-protected-previews` (high; confidence medium, not verified on a deployment) | `60d9ded` |
| `billing-delayed-payment-methods-grant-pro-before-funds` | `2f4f244` |
| `billing-deleted-stripe-customer-locks-user-out` | `d5cbdfb` |
| `tests-user-sync-customer-lock-unguarded` | `21a2492` |
| `docs-stripe-config-error-logged-as-warning` | `4a4a565` |
| `billing-reconcile-default-report-only-switch-untested` | `d08c42e` |
| `0f-F-01` (a user deletes their Clerk account, the subscription keeps billing) | `8bca686`; the Clerk Dashboard step is S12 in `docs/setup.md` |
| `0f-F-02`, `0f-F-03` (policy clauses, audit and outbox grants) | `cb92360`, `b891244` |
| `0f-F-04`, `login-role-runtime-checks-untested` | `921f5d7` |
| `sentry-carries-provider-ids-in-urls-and-the-test-is-blind` | `f19e74b`, `4490bcd` |
| `ci-calls-verify-is-unguarded` | `4c475bf` |
| `setup-md-says-dev-runs-without-stripe-cli` | `dbffebe` |
| `uv-lock-staleness-undetected` | `d91b420` |
| `csp-and-header-tests-cannot-fail` | `389b9f9`, `a5607fe`, `b32b72b` |
| `tenant-keys-and-pro-paywall-untested` | `41040ef` |
| `sandbox-e2e-skips-the-csp-and-error-guard` | `b3ecfc5` |
| `api-login-hint-wrong-pooler-port` | `9c50c71` |
| `grant-floor-untested` | `cb92360` |
| `verifier-bounds-untested` | `1c2eff5` |
| `first-preview-needs-url-before-it-exists`, `matrix-bill-13-pass-without-measurement`, `data-inventory-omits-processors-and-flows` | this documentation pass |

## Fix round 2 by package

Six packages, cut from `dd12f44`. The decisions they adopted are [ADR-0004 §20 to §26](../adr/0004-cycle-1-adopted-decisions.md). Every figure is the package's own, `local` or `mocked`.

| Package | Commits | Results | Not run |
|---|---|---|---|
| `web3` | 14 | Web 972 passed, 1 skipped (baseline 887 and 1). A vitest mutation suite of 81 mutations: 80 killed on the first run, the survivor fixed. Account-free end-to-end run: 56 passed, 14 skipped. 5 mutations of the rebuilt app killed (no HSTS, HSTS with `includeSubDomains`, no Referrer-Policy, no Permissions-Policy, no `signInUrl`) | The sandbox tier; any deployment (credentials `same-origin` on a protected preview is NOT VERIFIED) |
| `apitests` | 8 | API 1882 passed, 5 skipped (323 s). New tests for the verifier's bounds, HS256, HS384 and HS512 with the PEM as key, Clerk webhook replay, CORS, provider hosts and headers, and the configuration guards. `CLERK_AUDIENCE`: 5 mutations killed | Whether Clerk issues a custom `aud` (NOT VERIFIED) |
| `obs2` | 13 | API 1896 passed, 5 skipped; web 925 passed, 1 skipped; scripts 96; `pnpm build:check` passed twice. Every new test mutation-checked. The installed `@sentry/bundler-plugins` 11.7.0 defaults `setCommits` to `{auto: true}` once a token is set (checked locally) | The Vercel platform's own logs |
| `tooling2` | 13 | API 1818 passed, 5 skipped; scripts 113; web 890; `pnpm test:new-module` 6 min 22 s (it now runs the whole API and web suites on the generated copy); `pnpm test:vercel-build` ok. With uv 0.11.1, `--frozen` accepts a stale lock and `--locked` refuses it | Remote CI (never run) |
| `dbtests` | 10 | API 1957 passed, 5 skipped. `pnpm test:supabase`: 757 passed, 5 skipped, and 11 script assertions ok (it was 464 passed). The constraint sweep has 101 cases (about 4 s). On PostgreSQL 15.1 a member of a `CREATEROLE` role made itself a member of an unrelated role; on 17.6 that grant is refused ([ADR-0004 §22](../adr/0004-cycle-1-adopted-decisions.md)) | A hosted Supabase project |
| `billing3` | 14 | API 1897 passed, 5 skipped; web 890 passed, 1 skipped; about 50 mutations each caught; migration `20261031000070_billing_customer_relink.sql` (it drops `subscriptions_stripe_customer_id_fkey`; reviewed squawk ignore) | Four real Stripe calls: `allowed_payment_method_types`, `customers.retrieve` of a deleted ID, `subscriptions.list(limit=1)`, and `cancel_at_period_end`. All **NOT RUN** |

## Verification of the merged trees

Full `pnpm verify`, run through a locking wrapper (outside the repository) that holds the end-to-end port lock. Label `local`; the secrets step was **SKIPPED** in both runs because gitleaks is not installed on this machine.

| Tree | Result |
|---|---|
| `dd12f44` (end of round 1b) | 22 of 23 steps passed on the first run. The failing step was the web dependency audit (new advisories GHSA-3g3m-2fjr-6pw4 for `glob` and GHSA-g3hv-75wg-m249 for `minimatch`, both reached through `@sentry/bundler-plugins` at build time). It was fixed with a pnpm override (`glob` 14.0.3, `minimatch` 11.0.3; the plugin's one call, `glob(patterns, {absolute, nodir})`, was run against it) and the steps were rerun green. Counts at that tree: API 1806 passed, 5 skipped (the opt-in Stripe sandbox suite); web 887 passed, 1 skipped; scripts 95; `init-template`, placeholders, links, `new-module` (1 min 32 s), `build:check`, the audits and the licenses passed; the account-free end-to-end run 56 passed, 14 skipped (the sandbox tier). `pnpm test:vercel-build` ok (13 checks) |
| `ecfd9d8` (all six fix round 2 packages merged) | 21 of 23 steps passed on the first run. API 2226 passed, 2 failed, 5 skipped (200 s); web 1016 passed, 1 skipped; scripts 114 passed; `init-template`, placeholders, links, `build:check`, the audits and the licenses passed; the account-free end-to-end run 56 passed, 14 skipped. The two failures, and the `new-module` step (which runs the whole API suite on a generated copy, 4 min 17 s), were one merge seam: two constraint cases of `dbtests` expected the foreign key `subscriptions_stripe_customer_id_fkey` that `billing3` drops on purpose ([ADR-0004 §21](../adr/0004-cycle-1-adopted-decisions.md)) |
| `1729bd0` | The two cases now pin the key's absence, and restoring the key fails both (mutation-checked). **NOT RECORDED in this pass:** the rerun of the `api-tests` and `new-module` steps at `1729bd0` had not finished when this record was written; the integrator adds its result here |

Earlier merged-tree findings fixed on the way: a stale install (`@clerk/backend` missing), a dangling link, and the account-free 404 spec (the page gate hides the 404 page from a signed-out visitor).

## The documentation pass

Run in `arch/docs2` at `1729bd0` plus the documentation commits, label `local`. Checks that read documents:

| Command | Result |
|---|---|
| The checklist and guide agreement command of `docs/setup.md` | Prints "checklist and guide agree" (S12 added to both) |
| `pnpm check:links` | Every relative Markdown link, its `#fragment` and the `docs/…#fragment` pointers in code resolve |
| `pnpm check:placeholders` | Exit 0 (template mode: the two contact placeholders are present, as expected) |
| `pnpm test:init-template` | Passed, 6.6 s (the copy renames, applies the adopter policy once, and every link in it resolves) |
| `pnpm test:scripts` | 114 tests, 114 pass, 3.4 s |

Not run in this pass: the API and web suites, `pnpm verify`, `pnpm build:check`, and the end-to-end tests. No code, test, script, workflow or lint configuration changed.

Platform facts were re-read from Vercel, Clerk, Stripe, Supabase and GitHub pages on 2026-10-31; each page used is cited where its fact is written (deployment, operations, limitations, data inventory, the DEC-001 record). Nothing from them was observed on a deployment.

## Not run, and why

| What | Why | What would verify it |
|---|---|---|
| Every sandbox suite: `pnpm test:stripe-sandbox`, `pnpm verify:sandbox`, the signed-in end-to-end tests, `pnpm check:setup` against real providers | No Clerk, Stripe, PostHog, Sentry or LLM keys exist in this environment | The owner pastes sandbox keys into `.env` and runs `pnpm verify:sandbox`; record the printed record here as `sandbox` (DEC-006) |
| Four Stripe calls of fix round 2: card-only Checkout (`allowed_payment_method_types`), retrieving a deleted customer, `subscriptions.list(limit=1)`, and updating a subscription with `cancel_at_period_end`; that wallets and Link behave as cards under the filter | Only loopback stubs have answered | The Stripe sandbox suite and `pnpm check:setup` with a real sandbox key |
| A Vercel preview deployment, and what only it shows: the proxy inside a service, the Framework Preset, the crons (production only), the router's order, limits, Instant Rollback, the shared environment, HSTS on the API, and that a protected preview lets the app's own API calls through | Needs the owner's authorization and a Vercel account | The first-preview order in [deployment.md](../../docs/deployment.md#the-first-preview-in-order) and the [two-minute check](../../proofs/dec-001/README.md#first-preview-the-two-minute-check) |
| `proofs/dec-001/run.sh` | Needs Docker Desktop and about ten minutes; last passed at `966d3b3` | Run it once on a quiet machine after `pnpm db:up` |
| A hosted Supabase project (Supavisor routing for `app_login.<ref>`, the transaction pooler on port 6543 with `db:api-login`'s output, SCRAM verifiers, TLS) | No hosted project; the image runs are local | `pnpm db:api-login --allow-remote` with a non-production project, then `pnpm test:supabase` with a newer image |
| Whether Clerk issues a session token with a custom `aud` (`CLERK_AUDIENCE`), the default of "Allow users to delete their accounts", and Clerk's handling of an inbound `redirect_url` | Needs a Clerk instance | The sandbox run, and reading the Dashboard |
| GitHub Actions (every workflow), CodeQL, the secret scan in CI, the migration workflow, branch protection | Nothing was pushed | Authorize a push, then read the first CI run |
| gitleaks in `pnpm verify` | Not installed on this machine | Install gitleaks and run `pnpm verify` |
| A clean-cache run of the onboarding flow (REPO-11) | The recorded run had warm caches | A fresh container or VM, following only the README |
| The strict web lint guards | Wiring them means editing `apps/web/eslint.config.mjs`, which agents may not change (specification §23, step 4) | The owner applies the change |
