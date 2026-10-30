# Notes for the integrator: package `stripe`

Branch `arch/stripe`, cut from `arch/base`. Code is in `apps/api/app/modules/billing/**`, `apps/web/src/features/billing/**`,
`apps/web/src/lib/{api/client,env,env.server}.ts`, and `supabase/migrations/20261030000020_*`. Documentation files are yours to edit; this file says what
they need. Evidence is `mocked` or `local` throughout; nothing here was run against Stripe (see "Evidence").

## What changed

1. **Webhook payload style** (`stripe-thin-event-payloads-rejected-as-forged`). The verifier checks the signature first (`stripe.WebhookSignature.verify_header`),
   then parses. A bad signature is still `400 WEBHOOK_SIGNATURE_INVALID` and now logs a WARNING with the reason only. A correctly signed body that is
   not a snapshot event (a Thin `v2.core.event`, or not JSON) is `400 INVALID_REQUEST` whose detail says to choose the **Snapshot** payload format, plus
   an ERROR log (`stripe_webhook_unsupported_payload`, object and event type only). No new error class (verifier correction, ARCH-11). Neither path logs the body (BILL-08).
2. **Portal and Stripe misconfiguration visibility** (`stripe-portal-and-endpoint-unverified-silent-misconfig`).
   - New port method `describe_portal()` (read only; the portal is never created or changed). The Stripe check on `/integrations` now also fails (`portal_mismatch`) when the
     default configuration is missing or inactive, payment-method update or invoice history is off, cancellation is off or not `at_period_end`, switching is off or lacks
     `price`, or the union of switchable prices is not exactly the two Pro prices (each extra is named as a replaced price or a price outside the catalog; each missing one is named).
     Price and portal problems are reported together. The policy is `portal_problems()` in `domain.py`, shared with `setup-stripe`.
   - The webhook endpoint is **not checked**: listing endpoints needs a restricted-key permission the setup docs do not require. The PASSED summary says so.
     A key that cannot read portal configurations gives "Customer Portal: not checked", not a failure.
   - A rejected API key (`AuthenticationError`, `PermissionError`), and a rejected `create_customer` / `create_checkout_session` / `create_portal_session` (every input is built
     by the server) are now `503 NOT_CONFIGURED` plus one ERROR log `stripe_configuration_error` (error type, code, param, http status, request id; never Stripe's message).
     A rejection naming the `customer` param (a deleted customer) stays an ordinary 400. Reads keep `InvalidRequestError` (sync and reconciliation rely on it).
     `billing_unknown_price` is an ERROR while the subscription is `active` or `past_due`.
   - Not done, as the briefing said: the v2 event-destination listing, a "last verified webhook" line, an API-managed portal configuration (would change ADR-0001).
3. **`pnpm setup:stripe`** (`python -m app.cli setup-stripe`, code in `billing/adapters/stripe_setup.py`). See "Commands".
4. **Re-read on request** (`stripe-success-and-portal-return-never-resync`). `POST /api/v1/billing/sync` (personal context, no body, never reads a session ID) re-reads the signed-in
   user's own customer under the customer lock with the same synchronization as a webhook (BILL-02). The success page polls first and, after 3 s, asks once; the portal return URL is now
   `/billing?from=portal` (server-built, SEC-14) and the billing page asks once when it sees it; a refused checkout asks before showing the plan. Any failure falls through to the old behavior.
   The design was sound; the log line the reviewer wanted at ERROR is INFO (`billing_user_sync`, `changed_plan`), as the verifier said.
   See "Decisions taken" for the protection of webhooks.
5. **DEC-013 option B** (`stripe-dec013-option-b-design`), as adopted: see "DEC-013".
6. **Custom domains** (`stripe-custom-domain-redirects-refused`): one optional variable `STRIPE_CUSTOM_DOMAIN`, read by the API and the web app. The API checks the URL of every Checkout and
   portal session it creates (https, no credentials or backslash, host `stripe.com`, a subdomain of it, or exactly the custom domain); anything else is `503 NOT_CONFIGURED` plus an ERROR log of the
   host only. The web app derives `NEXT_PUBLIC_STRIPE_CUSTOM_DOMAIN` in `next.config.ts` and accepts the same hosts.
7. **Sandbox suite** (`stripe-no-automated-sandbox-lifecycle-tests`): `apps/api/tests/sandbox/`, marker `sandbox`, `pnpm test:stripe-sandbox`. **NOT VERIFIED**: it cannot be run without an
   owner's sandbox key, and was not. It collects cleanly and is skipped in the normal run (5 skipped).
8. `config-provider-provisioning`: Stripe half is item 3. The Clerk half is out of scope (docs integrator).

## DEC-013 (option B) as built

- `checkout_block(subs, now)` in `domain.py`: `already_pro` (entitling, BILL-07 unchanged), `payment_required` (`unpaid`, `paused`, `past_due` on an unknown price), `subscription_exists`
  (`trialing`, `active` on an unknown price), `payment_pending` (`incomplete`, defensive). Ended or final subscriptions block nothing. `BillingStatus.checkout_blocked` carries it (BILL-20);
  the page offers Upgrade only when it is null, and shows the reason plus **Manage billing** otherwise (not for `payment_pending`, which has nothing to manage).
- `start_checkout`: refuses from the stored state with **zero Stripe calls** (409 `CONFLICT`, so the page's reload-on-CONFLICT works unchanged). For a user who already has a customer it then
  (a) expires the stored open session at Stripe, (b) reads Stripe's own subscriptions and refuses again if one is not over (closes the slow-webhook path), (c) creates the session, (d) stores it.
  A first checkout (new customer) makes none of (a), (b).
- **Table** `app.billing_checkout_sessions` (migration `20261030000020`): `user_id` PK (one row per user, `ON DELETE CASCADE`), `stripe_session_id`, `idempotency_key`, `expires_at`, `created_at`.
  Forced RLS; `app_api` has SELECT/INSERT/UPDATE/DELETE on its own row only; `app_worker` has **no** grant and no policy. It stores the session ID, not the URL (the URL is a bearer link).
- **Failure handling**: if Stripe cannot expire the earlier session (outage), no second session is created and the request fails `503 DEPENDENCY_UNAVAILABLE` ("could not be closed ... try again"),
  the row stays so the retry closes it. "Already complete / already expired" (`InvalidRequestError`) counts as closed, and step (b) then sees any subscription it created. A stored session whose
  `expires_at` has passed is not expired again. If the new session cannot be stored it is expired at once. If another request of the same user stored one in between (two tabs within ~1 s), the later
  save wins and the replaced session is expired.
- **Idempotency**: a retry with the same `Idempotency-Key` returns its session if still open (one `retrieve`), instead of closing it. The Stripe idempotency key of every later session
  is `checkout-<user>-<plan>-<client key>-<16 hex of sha256(session just closed)>`. Without that, Stripe replays the *closed* session for a repeated key and the user would get a dead URL.
  The first checkout's key is unchanged. `test_checkout_and_portal_send_the_plan_price_customer_and_configured_urls` was updated for the second request's suffix.
- Residual: the DB-level race above is closed after the fact, not prevented; a user could in theory complete the loser's session in the milliseconds before it is expired.
  `billing_multiple_entitling_subscriptions` detection and the operator procedure stay.

## Commands and environment

| Item | Detail |
|---|---|
| `pnpm setup:stripe --currency usd --monthly 900 --yearly 9000` | `python -m app.cli setup-stripe`. Amounts in minor units; all three flags required, no defaults. |
| `pnpm test:stripe-sandbox` | `STRIPE_SANDBOX_TESTS=1 pytest tests/sandbox -m sandbox`. Needs `STRIPE_SECRET_KEY=sk_test_...` (unrestricted), `STRIPE_PRICE_PRO_MONTHLY/YEARLY` (env or `.env`) and the test database. |
| `STRIPE_CUSTOM_DOMAIN` | optional, API and web, bare lowercase hostname (`payments.example.com`). `.env.example` updated. Web: added to `SERVER_VARIABLES`; `NEXT_PUBLIC_STRIPE_CUSTOM_DOMAIN` is derived, not set by hand. |
| `BILLING_SYNCS_PER_USER_PER_HOUR` | default 30, 1 to 1000. `.env.example` updated. |
| pytest marker `sandbox` | registered in `apps/api/pyproject.toml`. |
| Migration | `supabase/migrations/20261030000020_billing_checkout_sessions.sql` (the only one used from my range). |
| Contracts | regenerated in the last commit: `POST /api/v1/billing/sync`, `BillingStatus.checkout_blocked`, `CheckoutBlock`. |

`setup-stripe` behaviour: it reads `STRIPE_SECRET_KEY` and `APP_ENV` only (not the full settings), refuses any key but `sk_test_` and any `APP_ENV` but `local`/`test` before contacting Stripe,
finds the prices by lookup key `pro_monthly` / `pro_yearly`, creates the product (custom ID `pro`, found again by that ID) and only the missing prices (idempotency keys), stops with nothing created if
an existing price differs in amount, currency, interval or state (message points to `STRIPE_PRICE_PRO_*_RETAINED`), prints the `.env` lines (`BILLING_MODE=sandbox`, the two price IDs, placeholders
for the key and the webhook secret), never writes `.env`, never prints the key or Stripe's error text, then reads the portal and prints the dashboard steps still needed.
**An agent must not run it against the owner's account without authorization** (spec 17 rule 1): it creates objects in the sandbox.

## Manual steps for the owner / adopter

1. Create the Stripe sandbox, put an `sk_test_` key in `.env` as `STRIPE_SECRET_KEY`, set `APP_ENV=local`, run `pnpm setup:stripe --currency <iso> --monthly <minor> --yearly <minor>`, and paste the printed lines into `.env`.
2. Dashboard only: save the Customer Portal (test mode) with payment-method update, invoice history, cancellation **at period end**, and switching between exactly the two Pro prices
   (Settings > Billing > Customer portal). `setup-stripe` and the `/integrations` check say what is missing.
3. Dashboard only: Billing > Revenue recovery > Retries: when all retries fail, cancel or mark unpaid (DEC-010).
4. Webhooks: local `stripe listen --forward-to localhost:8000/api/v1/webhooks/stripe` (secret from `stripe listen --print-secret`); deployed: create the event destination with payload format **Snapshot**.
   Keep the account's default API version (any works: the handler reads only the event ID, type, creation time and the object's customer). Pinning a non-default version uses one of Stripe's
   three uniquely versioned snapshot destinations for no gain.
5. To verify against Stripe (DEC-006): run `pnpm test:stripe-sandbox` with the key above, record the result under `dev-docs/evidence/` labelled `sandbox`. Also still to do by hand: a real 3-D Secure
   test card in Checkout (abandoned session then a new checkout), and a decline after all retries (needs the Dashboard setting in step 3).
6. If a Thin event destination was ever created: create a Snapshot one, put its secret in `STRIPE_WEBHOOK_SECRET`, delete the Thin one (the format cannot be changed), then run
   `pnpm billing:reconcile` and `pnpm billing:reconcile --apply`.
7. Custom domain (live mode only, optional): set `STRIPE_CUSTOM_DOMAIN` in the API and the web environment and deploy **before** the domain goes live in Stripe (Stripe switches automatically once DNS verifies).
8. Optional defense in depth, Dashboard only: Settings > Checkout and Payment Links > Subscriptions > "Limit customers to one subscription", redirecting to `{APP_BASE_URL}/billing`.

## Documentation changes needed

- `docs/integrations.md`, Stripe section:
  - Step 2: use `pnpm setup:stripe` (flags, what it prints, that it only touches a sandbox and never writes `.env`).
  - Step 3: the restricted key `rk_test_` cannot run `setup:stripe` or the sandbox suite. For the app, the portal check reads portal configurations, which belongs to the Dashboard permission
    **Customer portal** (confirm the exact name in the Dashboard; the check reports "not checked" if the key lacks it). Reading webhook endpoints needs another permission and is not used.
  - Step 4: add what `/integrations` now verifies; step 5 (Deployed): "Payload format: **Snapshot**, not Thin, even though Stripe labels Thin as recommended. This API answers thin notifications with 400
    INVALID_REQUEST. API version: keep the account default." plus the recovery note in manual step 6.
  - New step for `STRIPE_CUSTOM_DOMAIN` (manual step 7 and the table row at the redirect table: portal return is now `<APP_BASE_URL>/billing?from=portal`).
  - Line ~102 ("Pro access is granted only after a signature-verified webhook..."): Pro is granted by a webhook, by reconciliation, or by a re-read the signed-in user's own request triggers; the redirect alone never grants it.
  - "Verifying an integration": replace the manual Stripe bullets with `pnpm test:stripe-sandbox` plus the two Dashboard-only settings (the Dashboard cannot attach a test clock to a Checkout customer).
- `docs/operations.md`: a bad key, a missing or wrong portal configuration, an unexpected redirect host, a Thin payload, and a paid subscription on an unknown price now appear as Sentry errors
  (`stripe_configuration_error`, `stripe_session_host_unexpected`, `stripe_webhook_unsupported_payload`, `billing_unknown_price`). Webhook 400s are logged (WARNING).
- `docs/data-inventory.md`: new table `billing_checkout_sessions` (internal user ID, Stripe Checkout Session ID, request key, expiry; no URL; one row per user, replaced or deleted by the next checkout,
  removed with the user). Rows of completed checkouts linger until the user's next checkout.
- `dev-docs/engineering-implementation-plan.md`:
  - BILL-06: "asks the server once to re-read from Stripe, then polls; redirect parameters never grant entitlement"; §9.1 layer 2: "(webhooks, reconciliation, and a re-read the signed-in user requests for their own customer)".
    This changes billing behaviour (§17 rule 5): record under §23 as awaiting owner approval, like the retained-price settings.
  - BILL-07 rewritten per DEC-013; set DEC-013 to RESOLVED; **write ADR-0004** (option B); correct the DEC-013 rationale: since API 2025-03-31 a failed Checkout payment no longer leaves an `incomplete` subscription,
    so the `incomplete` dead end of the rationale does not occur and `incomplete` is only a defensive block.
  - BILL-12: the event setup must use the Snapshot payload format (this work's finding); §21 rows for BILL-06/07/12 and the "BILL-* sandbox" row pointing at `tests/sandbox` (status stays NOT VERIFIED until a recorded run);
    §23 next step 1 mentions `pnpm setup:stripe` and `pnpm test:stripe-sandbox`.
- `README.md` / `AGENTS.md`: the two new scripts; troubleshooting row "Upgrade succeeded but plan still Free" can mention the automatic re-read and the Snapshot payload.
- `docs/architecture.md`: the module now has a checkout-session record (user-owned, API role only) and the user re-read path; both are in the billing module's existing layers.

## Local development: `pnpm dev` should start the webhook listener (for the package that owns `scripts/dev.mjs`)

I did not touch `dev.mjs`. The logic to add, as the briefing verified it:

- Applies only when `BILLING_MODE=sandbox` (process environment first, then `.env`, as pydantic-settings does) and `stripe` is on PATH. Parse `.env` with a shared `scripts/lib/env.mjs`
  (`scripts/check-bundle-secrets.mjs` cannot be imported: it scans a build at import time and exits 2 without one).
- Give the child `STRIPE_API_KEY` only when `STRIPE_SECRET_KEY` starts with `sk_test_` (this keeps the listener on the API's sandbox; the CLI reads that variable). Never pass a live key, never a command-line key.
- Secret check: `stripe listen --print-secret` (timeout ~10 s), compare with `STRIPE_WEBHOOK_SECRET`, warn on a mismatch **without printing either value**.
- Listener, as a third process group: `stripe listen --forward-to http://127.0.0.1:8000/api/v1/webhooks/stripe` (add `--events <comma list>` if wanted: the 14 types of `SYNC_EVENT_TYPES` in `service.py`;
  the handler records others as ignored). The CLI forwards snapshot events, which is what the API reads. Filter its output, replacing `whsec_[A-Za-z0-9]+` with `whsec_...`.
  If it exits, warn and keep the servers running; stop it with the others.
- CLI missing: print the install link and the exact command once. Tests: a fake `stripe` on PATH covering missing CLI, matching secret, mismatching secret, listener exit, and the secret never printed.

## Decisions taken (owner may reverse)

- **Protecting webhooks from user re-reads** (security review of this package): a user's re-read (1) counts against the per-instance bound that checkout and the portal share
  (`BILLING_MAX_CONCURRENT_STRIPE_CALLS`), (2) never waits and never takes the last synchronization slot (`_Slots.admit_user`: users hold at most `slots - 1`, so webhooks and reconciliation keep one), and
  (3) is limited per user per hour. With the minimum `DATABASE_WORKER_POOL_MAX_SIZE=2` there is one slot and user re-reads are always refused (503); the default pool of 3 allows one at a time per instance.
  The web falls through to polling on any failure. **No hourly bucket shared by all users**: a first version had one, charged before it was known a Stripe call would happen, and any crowd of free accounts could
  fill it and lock everyone out of the re-read. Alternative: a generous global bucket charged only after admission; rejected because accounts are free to create and the concurrency bound already caps the rate.
  Regression tests (mutation-checked): users never take the last slot; a re-read counts against the Stripe-call bound; no shared bucket exists and refused or no-account syncs charge only their caller.
- Thin payload: `400 INVALID_REQUEST` with the Snapshot message, not a new error code (verifier correction). The portal finding proposed `WEBHOOK_PAYLOAD_UNSUPPORTED`; the dedicated finding's correction overrides.
- Portal extras: a *retained* (replaced) price still offered by the portal is a failure like an unknown one (named differently), per the briefing. Alternative: tolerate retained prices while subscribers remain on them.
- `setup-stripe` creates immediately (no dry run, no `--apply`), accepts only `sk_test_` (the briefing also allowed a write-capable `rk_test_`; the task said refuse non-`sk_test_`). Product has the custom ID `pro` so an interrupted
  run finds it again. Amounts flags are `--monthly` / `--yearly`.
- `STRIPE_CUSTOM_DOMAIN` is one variable shared by both apps (not two names). Web exposes it through a derived `NEXT_PUBLIC_*` value, like `NEXT_PUBLIC_APP_ENV`.
- Stored session rows are written and read by the API role only; completed checkouts leave a row until the next checkout (no worker cleanup, to keep the table out of the worker's grants). Alternative: delete on sync.
- `payment_pending` (incomplete) refuses with no portal button: there is nothing to manage and no "start over".
- The `checkout.session.expired` webhook that closing a session triggers causes one extra re-read of that customer; it is in `SYNC_EVENT_TYPES` already and harmless.

## Limitations and follow-ups

- Nothing was run against Stripe: the sandbox suite, the `setup-stripe` command, `describe_portal` against a real portal configuration, expiring real sessions, and the restricted-key permission needed for portal reads are all **NOT VERIFIED**.
  Field names (`features.subscription_update.products[].prices`, `subscription_cancel.mode`) are taken from the installed SDK's types and exercised only against loopback stubs.
- The sandbox suite does not cover retry exhaustion (customer B in the briefing: advancing in 7-day steps for ~62 days to see `unpaid`/`canceled`, and failing with the Dashboard instruction when still `past_due`). Add it once the owner has run the rest.
- Follow-up not done: a pytest that `docs/integrations.md` lists exactly `SYNC_EVENT_TYPES`, and `python -m app.cli stripe-events` to feed `stripe listen --events`.
- Follow-up not done: the webhook endpoint check and a "last verified webhook" line on `/integrations` (needs a restricted-key permission or an index on `billing_events.received_at`).
- The e2e suite was not run (`E2E_SANDBOX=1` needs a sandbox; the billing page keeps its Upgrade buttons for a Free user, which is what the existing e2e checks).
- `test_stripe_adapter.py`: the old case that a 401 from `list_customer_subscriptions` is `DependencyUnavailableError` is deliberately gone: a rejected key is now `NotConfiguredError` (503 `NOT_CONFIGURED`) with an ERROR log.
  Also, checkout test URLs moved from `*.stripe.test` to `*.stripe.com`, which the new host check requires.

## Evidence

All `local` or `mocked` (loopback stubs, the in-memory provider, a local PostgreSQL 17 test server). Nothing is `sandbox` or `live`.

| Command (from the worktree) | Result |
|---|---|
| `uv run ruff check app tests index.py ../../perf`; `ruff format --check` | pass; 146 files formatted |
| `uv run lint-imports`; `uv run mypy` | 8 contracts kept; no issues in 85 files |
| `uv run pytest -q -p no:cacheprovider` (full API suite, once) | 1086 passed, 5 skipped (the sandbox suite) in 224 s |
| `pnpm --filter web lint` / `typecheck` / `format:check` | pass |
| `pnpm --filter web test` | 28 files, 429 passed, 1 skipped (pre-existing) |
| `pnpm contracts:check`, `pnpm check:placeholders`, `pnpm test:init-template` | pass |
| `pnpm build` (placeholder Clerk key), `pnpm check:bundle-secrets` | build succeeds; no secret-shaped strings in the bundle |
| `pytest tests/sandbox -q -rs`; with `STRIPE_SANDBOX_TESTS=1` but no key | 5 skipped, with the reason; collects cleanly. The enabled path was **not** run (it calls Stripe). |

Mutation checks (the fix reverted in the worktree, the new test confirmed failing, the fix restored): signed thin payload and forged-signature logging; configuration-error mapping, portal policy, probe wiring and
`billing_unknown_price` level; user-sync `try_admit` vs wait, last-slot reservation, Stripe-call bound, shared bucket; checkout: expire-before-create, live re-read, retry replay, local refusal, failure to close,
replaced-session cleanup, close-on-failed-save; `setup-stripe`: key check, difference check, always-create, key printing. The web tests were run green only (not mutated).

Not run: e2e (needs `E2E_SANDBOX=1`); any call to Stripe; the sandbox suite.
