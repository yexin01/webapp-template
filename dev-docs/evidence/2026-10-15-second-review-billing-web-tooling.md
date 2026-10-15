# Evidence: Second Review and Fixes — Billing, LLM, Web, Tooling, Performance (2026-10-15)

| Field | Value |
|---|---|
| Scope | Every commit up to `dbf8a9b`, including the first review's fixes. This record covers billing, LLM, input validation, the web app, repository scripts and CI, the module generator, the performance harness, and the DEC-001 proof. Identity and the database kernel were reviewed and fixed in a parallel session; see [`2026-10-15-second-review-identity-db.md`](2026-10-15-second-review-identity-db.md) |
| Method | Two independent sets of adversarial reviews, run in parallel sessions, that reported the same defects in large part. Each finding was reproduced (or traced in code) before it was fixed. Regression tests were **mutation-checked**: reverting the fix makes them fail. A second-pass review of the fixes then found 17 more problems (one a regression: see Input validation), all fixed and re-checked |
| Environment | Apple M5, macOS; Python 3.12 (uv); Node 25; Next.js 16.4; PostgreSQL 17.6 in Docker (local); k6 1.3.0 in Docker |
| Integration mode | **mocked** for every external provider. No `.env` exists |
| Remote actions | **None.** No push, deployment, publication, or remote setting change |

## Gates (combined tree of both sessions, after the last code change)

| Check | Command | Result |
|---|---|---|
| Lint, format, import contracts | `pnpm lint` | PASS (8 contracts kept) |
| Strict types | `pnpm typecheck` | PASS |
| API tests | `pnpm test:api` | PASS: **466** (was 344) |
| Web tests | `pnpm test:web` | PASS: **219**, 1 skipped (the lint-guard wiring test, owner action) (was 146) |
| E2E, account-free | `pnpm test:e2e` | PASS: **32**; 18 skipped (sandbox only) (was 20) |
| Contract drift | `pnpm contracts:check` | PASS |
| Template init, placeholders | `pnpm test:init-template`, `pnpm check:placeholders` | PASS. At `dbf8a9b`, `test:init-template` **failed** (see Tooling 1); the previous record's PASS for it was wrong |
| Module generator | `pnpm test:new-module` | PASS, including a one-letter and a longest-name module with RLS tests |
| Build and bundle scan | `pnpm build && pnpm check:bundle-secrets`, plus `--self-test` | PASS (placeholder `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`). Every page is now static except sign-in and sign-up |
| Dependency audit | `pnpm security:audit` | PASS. npm production dependencies now block at any severity. One high advisory in a dev-only dependency (`braces` via `eslint-config-next`, GHSA-vfj7-8cjw-p6xm) is reported, not blocking: no patched version exists |
| Licenses | `pnpm security:licenses`, plus `--self-test` | PASS (134 npm, 40 PyPI) |

## Defects found and fixed

### Billing

1. **A webhook burst stalled the whole API.** A delivery waiting for a synchronization slot held a threadpool thread for up to 5 s. About 40 concurrent deliveries took every thread, so `/health`, `/ready`, and every synchronous route waited (reproduced: `/health` 4 ms → 4.6 s). The signature is now verified first, so forged requests get `400` at once; deliveries that synchronize then wait for a slot in the event loop, holding no thread, and get `503` after `DATABASE_CONNECT_TIMEOUT_SECONDS`. Behind that, the service bounds how many callers wait (reconciliation shares the slots). `DATABASE_WORKER_POOL_MAX_SIZE` must be at least 2, so synchronization always leaves a worker connection free. (`routes.py`, `service.py`; mutation-checked)
2. **The reconcile job could stop covering customers.** A customer Stripe rejects (typically one deleted in Stripe whose subscriptions have all ended) was never recorded as checked, so it stayed at the head of every batch; enough of them starved every other customer. With a batch size of 1, one unresolvable flag took the whole batch. Rejected customers now move to the back of the rotation, flagged customers take at most half the batch (none at size 1), and a run stops starting checks after 60 s. Divergences and rejections are logged by subscription and customer ID. (mutation-checked)
3. **A deleted Stripe customer kept Pro forever.** Its subscriptions could no longer be listed, so the stored `active` row never changed. A stored subscription that is not yet final and that the listing does not return is now read from Stripe by ID, and Stripe's state is applied. A subscription Stripe does not return changes nothing, so keys for the wrong account never revoke access. (mutation-checked)
4. **Deadlock between reconciliation and a redelivered flagged event.** The webhook locked its ledger row, then the customer; reconciliation did the reverse. Both now take the customer lock first. (reproduced; mutation-checked)
5. **Replacing a Pro price removed Pro from everyone still paying the old one** (Stripe prices cannot be edited, so a price change is a new price ID). Two opt-in settings, `STRIPE_PRICE_PRO_MONTHLY_RETAINED` and `STRIPE_PRICE_PRO_YEARLY_RETAINED`, list replaced prices that still grant Pro but are never sold. They are empty by default, so default behaviour is unchanged. A live subscription on a price outside the catalog is now logged (`billing_unknown_price`). The plan is read from the subscription item that has a known price, not always the first item. **Needs owner approval** (spec §17 rule 5, a billing-related decision): see below.

### LLM

1. **`budget_exceeded` was never produced** (LLM-02). An exhausted global budget now returns `429` with code `BUDGET_EXCEEDED`, distinct from the caller's own `RATE_LIMITED`. The web app presents it without a retry prompt.
2. **A check refused by the concurrency bound still used the caller's and the global quota.** The bound is now taken first.
3. **A check could hold a thread for about 3 × `LLM_TIMEOUT_SECONDS`** (retries each got the full timeout, and the body was read). Attempts now share the timeout: each gets what is left of it for connecting, sending, and each read, and a retry happens only if its wait fits. Only the status line and headers are read, never the body. `LLM_MAX_CONCURRENT_CHECKS` is limited to 16. This is not a hard wall-clock limit: a server that trickles its headers can still stretch one attempt.

### Input validation

1. **A NUL character in any text input returned `500`** (PostgreSQL text cannot hold it), in every module, including generated ones. Every input model now refuses it with `422`. The check walks the input iteratively: the first version recursed, and deeply nested JSON (about 500 levels) turned it into a `500`, which the second pass caught. (reproduced; mutation-checked)

### Web app

1. **PostHog received full URLs, including query strings** (OBS-03): `$session_entry_url`, `$session_entry_referrer`, and `$set_once` `$initial_*` values, so a Clerk invitation ticket or a Stripe session ID rode on every event; the feature-flag request carried them too. Every URL-like value is now stripped of its query and fragment before sending, and the flag request is disabled (the template uses no flags). A canary test runs the real `posthog-js`. (mutation-checked)
2. **Paths the auth proxy skips returned `500`** (`/favicon.ico`, `/x.png`, `/api/v1x`): the root layout rendered Clerk's server `<Show>`, which throws without the proxy, and each one could be sent to Sentry. Signed-in UI is now rendered by client components, an app icon exists, and pages are static again. (e2e-checked)
3. **After switching accounts without a reload, the previous user's data stayed on screen.** Resource keys, the API client, and the workspace are now keyed by user. (mutation-checked)
4. **Analytics identity carried over**: an identified PostHog user was not reset when a page loaded signed out, or when a different user signed in. Analytics now starts only once Clerk has loaded, and the stored identity is reset or replaced before PostHog's first pageview; the second pass found the landing pageview still went out under the stored identity. Data views also wait for Clerk, so a page load requests each resource once. (real SDK; mutation-checked)
5. **Sentry scrubbing was partial** (OBS-02): console breadcrumbs, event messages, and extras were not redacted. Both the browser and server configurations now use one shared scrubber. It never throws (a throwing value would make Sentry drop the event) and is bounded in depth, breadth, and total work, with cycles cut. (mutation-checked)
6. **Smaller fixes:**
   - the lint guards could be bypassed (`const { env } = process`, `Reflect.get`, dynamic imports of server configuration, including template-literal imports);
   - `API_ORIGIN` accepted paths, queries, and credentials;
   - the CSP allowed the wrong PostHog origins for the US and EU hosts and for reverse proxies;
   - account-free e2e runs could pick up telemetry settings from a developer's `.env`;
   - Next's rewrite proxy cut provider calls off at 30 s;
   - a hung or failing Clerk token fetch was outside the request deadline;
   - the billing page stayed "pending" after Back from Stripe;
   - checkout confirmation had no wall-clock budget.

### Repository tooling and CI

1. **`pnpm test:init-template` failed at `dbf8a9b`**, a required CI step, because a web test fixture contained the template's database port. Fixed. It also failed in every initialized copy (an adopter's CI); it now verifies, in such a copy, that re-running with the same identity changes nothing and a different one is refused.
2. **Security: a module named `m` generated an always-true RLS policy** (`m.org_id = m.org_id`: the policy's alias shadowed the table), so any member could read and insert another organization's rows, and the generated tests passed. The alias can no longer collide, and generated modules now include DB-07 wrong-context tests. The generator check builds a one-letter module. (mutation-checked)
3. **Generated module directories could be ignored by git** (`pnpm new:module logs` matched `logs/`), so a commit wired in a module it did not contain. Root patterns in `.gitignore` are anchored, and the generator refuses outputs that git would ignore.
4. **The license gate accepted any `WITH` exception** (`Apache-2.0 WITH Commons-Clause` passed). Exceptions are now allowlisted, and the gate fails closed on anything else.
5. **The bundle-secret scan missed prerendered pages** (`.next/server/app/**` HTML and RSC payloads served to browsers).
6. **The secret scan exempted every API test file.** The exemption is removed; synthetic test values carry an explicit marker.
7. **The restore drill only proved a restore into the same server.** It now restores into a fresh container, with roles created first and object owners kept: a restore with `--no-owner` silently changed the owner of a `SECURITY DEFINER` function. It compares owners, privileges, policies, and role attributes. A signal now stops it at the next step, removes the container, and exits 128+n.
8. **Smaller fixes:**
   - `pnpm dev` left servers running after SIGTERM or when one server exited (it is now documented as macOS and Linux only; it never worked on Windows);
   - `new:module` crashed on names like `s`, guessed wrong singulars (`movies` → `Movy`), could leave a half-written module, and could generate code that failed lint;
   - `init-template` could not resume a part-way run and accepted ports such as `08000` or `3000`; it now names the Compose project;
   - `test:new-module` broke once an adopter generated a module with a fixture's name, or with a global gitignore such as `logs/`; the script tests now copy tracked files only (`scripts/lib/tracked-copy.mjs`), keep tracked symlinks inside the copy, and refuse submodules;
   - the generator's rollback was never exercised and its up-front check missed unwritable directories; both are now tested;
   - the API test suite skipped every database test in CI when PostgreSQL was unreachable;
   - `pnpm format` skipped `perf/`;
   - the CI secret-scan job lacked the permissions the action needs on pull requests.

### Performance harness and DEC-001 proof

1. **PERF-06 could not detect double processing**, although the docs said it verified exactly-once delivery: the ledger counts look the same when every duplicate re-synchronizes. The perf server now counts Stripe re-reads per processed event and fails the run on a mismatch, and k6 requires every delivery to be `processed`. A regression test drives the harness with concurrent duplicates. (mutation-checked)
2. **Recorded saturation figures were whole-run averages**, including ramp-up time, and the stress stages ramped rather than holding each level. The scenarios now hold each level and report offered against achieved rate per stage. Dropped iterations fail baseline, load, and soak runs. Corrections are appended to the earlier records; no recorded number was changed.
3. **The proof's build step asserted nothing about the bundle.** It now fails on test files, non-x86-64 native libraries (read from ELF and Mach-O headers), a bundle of 500 MB or more, crons that differ from the API's job registry, or a runtime other than Python 3.12. A full local proof run passed with these checks.
4. **Deployed runs could not pass Vercel Deployment Protection.** k6 and the proof now send `x-vercel-protection-bypass` from `VERCEL_AUTOMATION_BYPASS_SECRET`, passed by name and never logged.
5. **Smaller fixes:**
   - the latency proxy was silently skipped for a URL without a port;
   - rate parameters were not validated;
   - the users-file guard compared path strings;
   - `BASE_URL` credentials could reach logs;
   - the proof's request minimum ignored `PROOF_USERS`;
   - `run.sh` inherited Vercel credentials;
   - several documentation errors (a non-existent `CLERK_ISSUER`, a `pnpm --` form that fails, and Docker Desktop being required).
6. **Vercel Cron runs only on production deployments**, so the authorized preview cannot verify it. The DEC-001 records now say so.

A local PERF-06 rerun (mocked; default pools: worker pool 3, two synchronization slots; 100 events × 3 concurrent copies, 20 virtual users) processed all 300 deliveries, with 100 Stripe re-reads for 100 events. PERF-03, PERF-04, and PERF-06 still have to be re-recorded as evidence.

## Not fixed: owner decisions and limits

- **Retained prices (Billing 5) need owner approval.** The settings are opt-in and empty by default. To decline, remove them; the `billing_unknown_price` warning still makes the problem visible.
- **Permanent Stripe rejections are acknowledged and flagged, whatever the cause.** With keys for the wrong account or mode, every webhook is flagged rather than retried. Nothing is revoked, and the reconcile job resolves the flags once the keys are fixed. Events are not yet refused by `livemode`.
- **`@next/env`** is no longer imported, but removing it changes the lockfile; left in place.
- **PostHog campaign parameters** (`utm_*`, click IDs) are still sent as properties. They are not credentials; `mask_personal_data_properties` would mask click IDs if you want that.
- **Not verified here:**
  - Linux instructions for the perf tooling;
  - real Deployment Protection, Stripe, Clerk, PostHog, or Sentry;
  - the gitleaks version CI downloads (a local `ghcr.io/gitleaks/gitleaks:v8.28.0` image found no leaks in tracked files or the 38-commit history);
  - Windows for `pnpm dev`.
- Still open from before: DEC-001, DEC-006, DEC-013, the lint-guard wiring (owner edits `apps/web/eslint.config.mjs`; the guards now also catch the bypasses above), and the license exceptions review.
