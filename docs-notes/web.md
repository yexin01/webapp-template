# Notes for the integrator: package `web`

Branch `arch/web`, on top of `apix`, `db` and `stripe`. Code is in `apps/web/**` and one line each in `scripts/new-module.mjs` and
`scripts/templates/module/web/panel.test.tsx.tmpl`. No API, contract, migration, `.env.example`, `next.config.ts`, `env.server.ts` or
ESLint change. Documentation files are yours to edit; this file says what they need. Evidence is `mocked` or `local`; nothing here ran
against Clerk (see "Evidence").

## What changed

1. **Default-deny page access** (`web-arch-route-protection-deprecated-allowlist`, `onboarding-clerk-route-matcher-deprecated`).
   - `src/lib/page-access.ts` is the one allowlist: `PUBLIC_PAGES = ["/", "/sign-in/*", "/sign-up/*"]` (`/x/*` is `/x` and everything
     below it; any other entry is exactly that path). `isPublicPage()` fails closed: a path it does not match is not public.
   - `src/proxy.ts` calls `auth.protect()` for every page that is not public, so a **new page requires sign-in** until it is listed on
     purpose. Clerk's deprecated `createRouteMatcher` is gone (no more deprecation warning in `pnpm dev`). `auth.protect()` keeps the
     307 to `/sign-in?redirect_url=...`, so pages stay static and the account-free e2e still proves the redirect.
   - Matcher: `/__clerk/(.*)` is added (Clerk's default; the clerk-js files its Frontend API proxy serves end in `.js`, which the first
     pattern skips); `txt` and `xml` join the skipped extensions so `robots.txt` and `sitemap.xml` (metadata route files) stay public
     without an allowlist entry. `api/v1` stays excluded.
   - Tests: `src/lib/page-access.test.ts` walks `src/app` (pages and route handlers; route groups, dynamic and optional catch-all
     segments handled; parallel and intercepting routes fail loudly) and fails when a page is public that is not in
     `EXPECTED_PUBLIC_ROUTES`, when a route is half-open, and when an allowlist entry matches no page. `src/proxy.test.ts` runs the
     real proxy (a document request, no cookies) for public and protected paths and runs Next's own matcher
     (`unstable_doesMiddlewareMatch`) over `config`.
   - e2e (`mocked`, account-free, ran): added "an unlisted page requires sign-in" and the three public pages open; the existing 307
     checks are unchanged. Sandbox files (NOT RUN, need Clerk keys): `pages.spec.ts` "unknown routes show the 404 page" became "unknown
     routes ask a signed-out visitor to sign in", and the 404 page check (with axe) moved to `authenticated.spec.ts`.
   - Generator: `pnpm new:module` writes no page. Its last step now says a new page requires sign-in and where the public list is; the
     generated panel test mocks `useClerk` (needed since item 5).
2. **Clerk wiring** (`clerk-web-wiring-drift`): see "Findings" below for what was done and skipped.
   - `ClerkProvider` moved inside `<body>`, around the skip link, header, `<main>` and the diagnostics provider (Clerk's quickstart).
     The built pages are still static.
   - **AUTH-06 silent refresh** (`src/lib/api/client.ts`): a 401 for a request that carried a token is retried once with
     `getToken({ skipCache: true })` when Clerk returns a different token, under the same deadline. The request is cloned before the
     first send (a body can be read once); `startCheckout` keeps its `Idempotency-Key` on the retry. The API answers 401 before any
     handler runs, so the retry repeats no effect. Otherwise the 401 stands and the "Sign in" action (relative `redirect_url`, keeps form
     state) is unchanged, as the verifier asked. `TokenGetter` takes `{ skipCache?: boolean }`; `useApi` passes it through.
3. **CSP** (`clerk-csp-behind-current-requirements`, `secarch-csp-static-missing-clerk`): `https://*.protect.clerk.com` in
   `script-src` and `frame-src`, `https://*.protect.clerk.com:*` in `connect-src` (Clerk Protect is served on ports other than 443; the
   `:*` is required). `csp.test.ts` pins every Clerk source per directive (table-driven, exact tokens) and fails on drift.
4. **Operator tools only for operators** (`web-arch-operator-tools-in-customer-shell`): the header's Integrations link, the home page's
   "Check integrations" button and the dashboard's LLM connectivity card render only when the API says the user may use diagnostics.
   `DiagnosticsAccessProvider` (`src/lib/diagnostics-access.tsx`, mounted in the root layout) asks the existing
   `GET /api/v1/diagnostics/integrations` once per page load for a signed-in user: success means access, 404 (not an operator,
   diagnostics disabled, an actor session) or any other outcome, and the time before the answer, mean none. The answer is keyed to the
   user. `OperatorOnly` (`src/ui/operator-only.tsx`) wraps the three places. No API change.
5. **Clerk failure detection** (`web-arch-clerk-failure-timer`): the SDK exposes a stable, documented signal (`Clerk.status`, "error"
   when clerk-js cannot be loaded or `Clerk.load()` rejects; read with `useClerk()`). `useClerkLoadTimedOut` became
   `useClerkUnavailable` (`status === "error"` or the backstop). The 30 s timer stays as `CLERK_LOAD_BACKSTOP_MS` for a `load()` that
   never settles, and no longer borrows the API request deadline. `"degraded"` means loaded (only the Frontend API is down), so nothing
   is shown for it. Every test that mocks `@clerk/nextjs` for a component using these hooks now provides `useClerk`.

## Findings

| Finding | Result |
|---|---|
| `web-arch-route-protection-deprecated-allowlist` + `onboarding-clerk-route-matcher-deprecated` | fixed (1) |
| `clerk-csp-behind-current-requirements` + `secarch-csp-static-missing-clerk` | fixed (3); telemetry subdomains and `frame-src 'self'` not added, no violation reporting (verifier) |
| `clerk-web-wiring-drift` (i) `/__clerk` matcher | fixed |
| (ii) `authorizedParties` | **deferred**: needs `env.server.ts` and `next.config.ts`, which belong to another package. Patch below |
| (iii) `ClerkProvider` inside `<body>` | fixed (no functional change, per verifier) |
| (iv) post-sign-up landing | not done (verifier: not Clerk guidance; `ClerkProvider` fallback props do not reach the sign-up buttons, which always send a `redirect_url`). If wanted: `forceRedirectUrl="/dashboard"` on the `SignUpButton`s |
| (v) AUTH-06 silent refresh | fixed (2) |
| `web-arch-operator-tools-in-customer-shell` | fixed (4), with a server answer instead of the verifier's `APP_ENV` gate |
| `web-arch-ui-rederives-authorization` | **deferred**, see below |
| `web-arch-clerk-failure-timer` | fixed (5), verifier-corrected |

## New commands and environment variables

None. No `.env.example` change. No `package.json` script.

## Manual steps for the owner or adopter

- None required.
- Optional, in the Clerk dashboard: turning on Clerk Protect mid-flow challenges is the owner's product choice; the CSP now allows it.
- A page that must be reachable signed out (pricing, terms, a public docs page) is added to `PUBLIC_PAGES` in
  `apps/web/src/lib/page-access.ts` **and** to `EXPECTED_PUBLIC_ROUTES` in `page-access.test.ts` (the second edit is the deliberate step).
  Metadata route files other than `robots.txt` and `sitemap.xml` (for example `opengraph-image`) also need an entry or a matcher change.

## Documentation changes needed (integrator)

- `docs/architecture.md`
  - Step 6 of "Adding a feature" (line 257): "Mount the panel in a page. Pages require sign-in by default; make one public only by listing it
    in `apps/web/src/lib/page-access.ts`" and keep "add an e2e check for the main flow".
  - Frontend section (near line 239): the access gate is `src/proxy.ts` plus `src/lib/page-access.ts` (default deny; presentation only,
    the API authorizes every call, FE-05); `page-access.test.ts` is the fitness test.
- `docs/integrations.md` "Access" (line 37): the header link, the home button and the dashboard LLM card appear only for a user the API
  lets use diagnostics (the page asks the API; a customer never sees them). In production, diagnostics also need `DIAGNOSTICS_ENABLED=true`.
- `README.md` line 150 (troubleshooting): in production also set `DIAGNOSTICS_ENABLED=true`.
- `dev-docs/engineering-implementation-plan.md` §21:
  - FE-05: "proxy default deny (`page-access.test.ts`, `proxy.test.ts`: mocked); account-free e2e 307 and public pages: mocked; the real
    redirect after Clerk loads: sandbox (DEC-006), not run".
  - FE-07: the 404 page is shown to signed-in users; a signed-out visitor to an unknown path is sent to sign-in (the proxy cannot tell a
    typo from a page).
  - AUTH-06: silent refresh implemented and tested with mocks (`client.test.ts`); the real Clerk flow stays NOT VERIFIED (sandbox).
    The sign-in step stays a link the user clicks, so form state is kept (FE-04); the owner may confirm.
  - SEC-02: Clerk's required hosts are pinned by `csp.test.ts`; the policy keeps `'unsafe-inline'` (see Limitations).
  - ARCH-03: still PASS by review; record the open gap below.
- `dev-docs/evidence/2026-10-15-review-fixes.md:115` ("Integrations nav link needs an API capability flag"): resolved without a flag, from
  the existing endpoint.
- Record in ADR-0004 or the decisions register: default-deny page access with a single allowlist.

## Deferred: `clerk-web-wiring-drift` (ii), `authorizedParties`

Not done because it needs `src/lib/env.server.ts` and `next.config.ts` (another package). The API already enforces `azp`
(`identity/adapters/clerk.py`); the middleware gate only controls page presentation, so the risk is low. Ready patch (verifier-corrected,
because ENV-07 forbids `process.env.CLERK_*` and `env.server` in `src/proxy.ts`):

- `env.server.ts`: add `"CLERK_AUTHORIZED_PARTIES"` to `SERVER_VARIABLES`; parse it like the API (comma-separated http(s) origins,
  trailing slash stripped, no path; required unless `APP_ENV=test` when `serving`; messages name the variable, never the value);
  add `clerkAuthorizedParties: readonly string[]` to `ServerEnv`; tests for a missing value, a malformed value and a value with a path.
- `next.config.ts`: `env: { ..., NEXT_PUBLIC_CLERK_AUTHORIZED_PARTIES: env.clerkAuthorizedParties.join(",") }` (origins, not secret).
- `src/proxy.ts`: `const parties = (process.env.NEXT_PUBLIC_CLERK_AUTHORIZED_PARTIES ?? "").split(",").filter(Boolean);` and
  `authorizedParties: parties.length ? parties : undefined` in the options. `.env.example` already sets `CLERK_AUTHORIZED_PARTIES`.

## Deferred: `web-arch-ui-rederives-authorization` (ARCH-03)

Confirmed: `features/workspace/workspace.tsx` has `canDelete = orgId === null || membership?.role === "org:admin"`, a copy of
`PROJECT_DELETE` (`projects/domain.py`) and of the Clerk-to-app role mapping, read from Clerk's client-side role while the API reads its
own membership mirror (up to 300 s apart). The generated panel takes `canDelete` as a required prop, so every page that mounts it
copies the rule, and `new-module.mjs`'s "Mount `<XPanel />`" step omits the props. Not fixed because the fix is not small:

- it adds `can: {create, delete}` to `ProjectPage` and to the generator's `__Entity__Page` (service `capabilities(ctx)` using
  `is_allowed`, routes, `routes.py.tmpl`, `service.py.tmpl`, `test_module.py.tmpl`), regenerated contracts, API tests for personal,
  organization admin and organization member asserting `can` equals what POST and DELETE return, plus a mutation run
  (`PROJECT_DELETE` to `ALL_ROLES` must flip the member's `can.delete` with no web change);
- the web then drops `canDelete`, shows the create form and Delete from `first.data.can`, and `panel.tsx.tmpl`/`panel.test.tsx.tmpl` follow;
  a small `useActiveTenant()` hook (`{isLoaded, orgId}`) lets `<XPanel />` take no props, as `new-module.mjs` already says;
- it needs `pnpm test:new-module` and the full API suite, and touches files the `apix` package just changed (`projects/routes.py`,
  the API templates).

Plan as in the briefing (module-local `can` on the list response, no kernel registry, no new route). Until then a member who is shown
or denied Delete is corrected by the API (403 "Not allowed", or a hidden button).

## Decisions taken (owner delegated; reversible)

- **Default deny in the proxy, not a route group with a client gate.** The proxy keeps the server 307, static pages and the
  account-free e2e (the verifier's minimal fix). Alternative: `src/app/(app)/` plus a client `SignedInGate` (Clerk's "client-only
  routes" pattern). Cost accepted: an unknown URL sends a signed-out visitor to sign-in instead of the 404 page.
- **Operator tools decided by the existing diagnostics endpoint**, as the task required, not by the verifier's `APP_ENV !== "production"`
  gate (a browser guess that also hides the link from a production operator who sets `DIAGNOSTICS_ENABLED=true`). Cost: one extra
  `GET /api/v1/diagnostics/integrations` per page load for every signed-in user, answering 404 for customers (no audit entry, no rate-limit
  counter, no secret; a failed request line in the browser console). Alternative: a boolean on `GET /api/v1/me` (needs the identity module
  to learn the operator policy through composition, plus contracts) which has the same one call without the 404.
  The LLM card stays on the dashboard for operators, instead of the verifier's removal of `features/llm`.
- **Keep the 30 s backstop** instead of replacing it: the SDK reports `"error"` itself only for a blocked or refused script (after its 15 s
  script timeout) or a rejected `load()`.
- **`txt` and `xml` skipped by the matcher** so default deny does not break `robots.txt` and `sitemap.xml`.

## Limitations

- Nonce CSP is deferred (this round's decision): the policy keeps `'unsafe-inline'` in `script-src` and `style-src`, because Next.js
  inlines bootstrap scripts without a nonce setup, and Clerk's style runtime needs inline styles. The cost: an injected inline script is
  not blocked by CSP (XSS defense rests on React escaping, `react/no-danger`, the lint guards, and `frame-ancestors`). A nonce policy needs a
  per-request header from the proxy and makes every page dynamic. There is no CSP violation reporting (`report-to`) either.
- `frame-src 'self'` and `*.clerk-telemetry.com` are not allowed (verifier: no effect under `X-Frame-Options: DENY`; Clerk uses the apex
  `clerk-telemetry.com`, already allowed, and only from development instances).
- The page gate is presentation (FE-05). Pages stay static, so nothing is checked server-side beyond the proxy redirect; the API is the gate.
- Unknown URLs: a signed-out visitor is sent to sign-in, and a crawler gets 307 for a dead link.
- The diagnostics probe shows a 404 in the browser console for customers (see Decisions).
- Not done: the optional sandbox e2e that records `securitypolicyviolation` events on `/sign-in` and `/sign-up`.

## Evidence (labelled `mocked` or `local`; none is `sandbox` or `live`)

Commands run in the worktree (the machine was not under heavy load during these runs):

- `local`: `pnpm --filter web lint` (0 findings, `--max-warnings=0`), `typecheck`, `format:check`: clean.
- `local`: `pnpm --filter web test`: 33 files, 514 passed, 1 skipped (a skip already present on the base; none added), run twice.
- `local`: `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=<placeholder> pnpm build`: compiled; `/`, `/billing`, `/billing/success`, `/dashboard`,
  `/integrations`, `/pro` still prerendered static; `pnpm check:bundle-secrets`: clean.
- `mocked`: `e2e-locked.sh` (production build, local API, placeholder Clerk key): 40 passed, 20 skipped (the sandbox files `pages.spec.ts` and
  `authenticated.spec.ts`, which need `E2E_SANDBOX=1` and Clerk keys; **NOT RUN**, including the two I edited).
- `local`: `pnpm test:new-module` (with `TEST_DATABASE_URL` on the shared test PostgreSQL): passed (three generated modules pass lint, types,
  contracts, architecture, API with RLS, and their generated web tests, which use the changed panel test template).
- Not run: `pnpm test:init-template` (init is untouched), the API suite and `contracts:check` (no API or contract change).

Mutation runs (each reverted; the named tests failed):

- proxy protects only a fixed list: `/notes` redirect tests failed (2); `isPublicPage` always true: 9+ failed; `/pro` listed public: the
  exact-set test and 2 more failed; `/__clerk/(.*)` removed from the matcher: 2 failed; prefix match without the `/` boundary: 3 failed.
- CSP: no `:*`, no Protect in `frame-src`, none in `script-src`: one table case failed each time.
- AUTH-06: no clone (body consumed): 2 failed; no retry: 3; retry without a first token: 1; refresh outside the deadline: 1 (the test timed out at 5 s).
- Clerk status branch removed: 2 failed (use-resource, sign-in-unavailable).
- `OperatorOnly` always rendering: 3 failed; provider granting on any answer: 5; not tied to the user: 1; probing while signed out: 1.
