# Notes for the integrator: package `web2`

Branch `arch/web2`, on top of `apix`, `db`, `stripe`, `web`, `obs`, `config`, `onboard` and `deploy`. Code is in `apps/web/**`, one API
module (`apps/api/app/modules/diagnostics`), the shared fixture `packages/contracts/config-cases.json`, regenerated contracts, one
comment in `.env.example` and the preview environment of `scripts/test-vercel-build.mjs`. Documentation files are yours to edit; this
file says what they need. Evidence is `mocked` or `local`; nothing here ran against Clerk, Vercel or any other service.

## What changed

### 1. Page protection no longer rests on the proxy alone (client-side page gate)

- `apps/web/src/ui/page-gate.tsx` (`PageGate`) wraps `{children}` in the root layout (`src/app/layout.tsx`, inside `<main>`). It reads the
  same single allowlist as the proxy (`PUBLIC_PAGES` / `isPublicPage()` in `src/lib/page-access.ts`; there is no second list).
  - A public path: `PageGate` returns the children before it calls any Clerk hook (the test counts the reads: zero). Nothing else changes.
  - Any other path (a new page included, and the 404 page): nothing of the page renders until Clerk has loaded and reports a signed-in user.
    Until then a "Loading…" status shows; Clerk that failed to load (`useClerkUnavailable`) shows the existing "Sign-in unavailable" error
    with a reload action; a signed-out visitor sees "Redirecting to sign in…" and `router.replace`s to
    `/sign-in?redirect_url=<path and query>`.
  - Clerk has not loaded while a page is prerendered, so the static HTML of a protected page holds only the placeholder. **Built output
    checked (`local`)**: `/`, `/_not-found`, `/billing`, `/billing/success`, `/dashboard`, `/integrations`, `/pro` are still `○ Static`;
    `dashboard.html` contains the placeholder and none of the workspace UI. (The flight data of a static page still carries its
    user-independent component tree, as before; nothing in it is user data, and the page code is in the public bundles anyway.)
  - **SEC-14**: `signInUrlFor(pathname, search)` (`page-access.ts`) only ever returns a path on this origin as the return path: one
    leading slash, no `//`, no backslash, no control character, at most 1024 characters (a longer query string is dropped, then the path,
    then `/`). A `//evil.example.com/x` address becomes `/`.
  - **Once a page has been shown, it stays shown** (state keyed by path). A session that ends meanwhile surfaces as the API 401 whose
    "Sign in" action keeps form state (FE-04, the design of the `web` package), Clerk's own sign-out navigation is not raced into a
    stray sign-in redirect, and a transient `isLoaded: false` (organization switch) does not unmount the page. The next protected page
    needs its own grant, so a visitor whose session has ended does not walk on to it.
- The API authorizes every operation regardless (FE-05). The gate and the proxy are presentation; neither replaces a backend check.
- Tests (`mocked`): `src/ui/page-gate.test.tsx` (16, component tests with mocked Clerk and router: protected page renders no children while
  loading and when signed out, redirects once, keeps the query, refuses another origin, shows the page to a signed-in user, handles a
  failed Clerk, the "shown" rules, every public page in every Clerk state untouched); `src/app/layout.test.tsx` (13, the fitness test:
  it renders the **real root layout** around a page for every route `src/app` answers, for a signed-out visitor, and requires the page to
  render only where `EXPECTED_PUBLIC_ROUTES` says it is public and to redirect everywhere else, so a new page is gated by being created);
  `src/lib/page-access.test.ts` (+19: exactly one layout may render `<html>` (a route group cannot start a second document the gate does
  not wrap), the layout wraps its children in `<PageGate>`, the gate imports `isPublicPage`, and the `signInUrlFor` table).
  The route walker and `EXPECTED_PUBLIC_ROUTES` moved from `page-access.test.ts` to `src/lib/app-routes.testing.ts` so both tests read
  the same list (**the second, deliberate edit for a public page is now in that file**; the failure message says so).
- Limits: route handlers (`route.ts`) have no UI to gate and stay on the proxy (the walker still lists them for the default-deny
  test; none exist). A visitor with JavaScript off gets the placeholder only (no `<noscript>`).

### 2. `authorizedParties` in the proxy, with the API's rules

The deferred patch of `docs-notes/web.md`, adapted to `config`:

- `src/lib/env.server.ts`: `APP_BASE_URL` and `CLERK_AUTHORIZED_PARTIES` joined `SERVER_VARIABLES`; `ServerEnv.clerkAuthorizedParties` is the
  list the API's identity module verifies against, computed by the same rules: the listed origins in the form a browser sends them (an
  entry that is no origin is refused by name), else the origin of `APP_BASE_URL`; on a preview deployment of a preview application
  (`APP_ENV=preview` **and** `VERCEL_ENV=preview`) `https://$VERCEL_URL` and `https://$VERCEL_BRANCH_URL` are added after the list (bare
  hostnames only, never repeated, never in production); `APP_BASE_URL` must be set in a deployment, be an origin, and use https in production;
  the origin of `APP_BASE_URL` must be in an explicit list; under `APP_ENV=test` nothing is defaulted or checked. Messages name variables, never
  values. `VERCEL_URL` / `VERCEL_BRANCH_URL` are read from the process only, never from the root `.env` (a file must not imitate the platform).
- `browserOrigin()` is a port of the API's `http_origin`, not `httpOrigin` (WHATWG's parser accepts `https:a.example`, `https:\\a.example`,
  `https://a%2eexample`, `https://a$b.example`, a trailing backslash and `https:///a.example`; the API refuses all of them). IPv6 literals
  are kept as written, like the API.
- `next.config.ts` passes the list as `NEXT_PUBLIC_CLERK_AUTHORIZED_PARTIES` (comma-joined origins, not secrets); `src/proxy.ts` hands it to
  `clerkMiddleware({ authorizedParties })` (undefined when empty). ENV-07 holds: `proxy.ts` reads no `CLERK_*` variable except that
  `NEXT_PUBLIC_` one and imports nothing from `env.server`. **Built output checked (`local`)**: the value is inlined in the proxy bundle
  (`.next/server/chunks/...` loaded by `middleware.js`) and in nothing the browser receives; `pnpm check:bundle-secrets` is clean.
- Shared fixture `packages/contracts/config-cases.json` (run by `apps/api/tests/unit/test_config_cases.py` and `src/lib/env-cases.test.ts`;
  **no API validator changed**): 20 new cases (defaults, normalisation, every kind of bad entry, `APP_BASE_URL` rules, the preview
  derivation and its gates, hostname filter, test environment), a new optional key `authorizedParties` (the effective list both apps must
  end up with, compared whenever both accept), and an `origins` table of 72 addresses with the origin each must be read as (the API's own
  answers; both runners compare). 116 checks pass in each app.
- Test fixtures that model a preview or production deployment in `env.server.test.ts` now name `APP_BASE_URL` (`MODES` became `NAMED`).
  `scripts/test-vercel-build.mjs` now builds the web service with `APP_BASE_URL`, `ANALYTICS_MODE` and `ERROR_REPORTING_MODE`
  (`https://preview.invalid`, `disabled`): the web validator already refused a preview build without both modes since `config`, so
  `pnpm test:vercel-build` would have failed at its web build step on the integrated base (the deploy notes' "Order" warning). I checked
  that the validator accepts exactly that environment; **I did not run `pnpm test:vercel-build`** (network, a few minutes).
- Tests (`mocked`): `src/proxy.test.ts` (+3) runs the real proxy with the **real Clerk verification** (a throwaway RSA key pair, a stubbed
  key set, tokens signed in the test): tokens issued to either listed page open a protected page, tokens issued to anything else
  (`evil`, a prefix trick, `http` instead of `https`, another port, other case, empty) are sent to sign-in, and an empty list checks
  nothing; `src/next-config.test.ts` (2) checks the wiring of `next.config.ts`; `env.server.test.ts` (+20) the rules and messages.

### 3. Operator tools without console noise (`GET /api/v1/diagnostics/access`)

- API: `DiagnosticsService.may_use(ctx)` is the one predicate (diagnostics enabled, personal context, no session someone else started,
  and the operator policy: listed, or any signed-in user when `APP_ENV=local` with no operators listed). `integrations()` now asks it too;
  `run()` keeps its audit-on-refusal flow on the same two primitives. The route (`diagnostics/routes.py`) declares `requires(Access.OWN_ACCOUNT)`,
  answers any signed-in caller with `200 {"available": bool}`, writes no audit entry and no rate-limit counter, and carries nothing else. The
  integrations endpoints are unchanged. Contracts regenerated in their own commit.
- Web: `api.diagnosticsAccess()`; `DiagnosticsAccessProvider` (behind `OperatorOnly`: header link, home button, dashboard LLM card) asks it once
  per page load for a signed-in user and grants only on `available: true`. A 401, an unreachable API or any failure means no access. A
  customer's answer is a 200, so the 404 line in the browser console is gone. No component file changed (`features/workspace` is untouched).
- Tests (`local`, database): `tests/integration/test_diagnostics_access.py` (9 test functions, 16 cases: 401 anonymous; declared access;
  operator / second operator / non-operator; disabled; local with no operators listed and with operators; test environment with none;
  an impersonated session; **agreement with the integrations route for eight configurations**; zero audit rows and zero counters after 12
  calls, with the operator's whole check budget intact afterwards; the body is exactly `{"available": bool}` and names no one) and
  `tests/unit/test_diagnostics_access.py` (18: the policy per environment through `build_diagnostics`, including production off unless
  `DIAGNOSTICS_ENABLED=true`, and never open with an empty list outside local).

## Findings

| Item | Result |
|---|---|
| Page protection must not depend on one platform behavior | fixed (1); whether the proxy runs inside a Vercel service stays **NOT VERIFIED** until a preview deployment, now harmless to page presentation |
| `clerk-web-wiring-drift` (ii) `authorizedParties` (deferred in `web`) | fixed (2) |
| Operator probe printed a 404 in every customer's console | fixed (3) |
| Observation: `@clerk/nextjs` 7.9.15 reads no `CLERK_JWT_KEY` (its server constants have no such entry and `@clerk/backend` defaults `jwtKey` to `""`) | the web validates `CLERK_JWT_KEY` as a PEM, but nothing in the web uses it: the proxy verifies with keys fetched from Clerk. Not changed (ENV-07 keeps `CLERK_*` out of `proxy.ts`; an option would have to come through `next.config.ts`). Follow-up for the owner of `config`/docs: `.env.example` says "PEM public key for networkless token verification" |

## New commands and environment variables

No new command or script. `APP_BASE_URL` and `CLERK_AUTHORIZED_PARTIES` are now also read by the **web build** (they were API-only). The derived
`NEXT_PUBLIC_CLERK_AUTHORIZED_PARTIES` is set by `next.config.ts`; it is not a setting (a value set by hand is overridden).

## Manual steps for the owner or adopter

1. Vercel, Production and Preview: `APP_BASE_URL` was already required by the API; the **web build now refuses a preview or production build
   without it** (`APP_BASE_URL is required when APP_ENV=...`). `CLERK_AUTHORIZED_PARTIES` stays optional.
2. Both are read **when the web is built** (Next.js inlines them into the proxy). After changing either, deploy again (a new build); a restart is
   not enough. Locally, restart `pnpm dev`.
3. Open the app at the origin `APP_BASE_URL` names (or list the others in `CLERK_AUTHORIZED_PARTIES`). A page opened at an unlisted origin (a
   custom domain added later, `127.0.0.1` instead of `localhost`) now signs the visitor out at the proxy as the API already did, and **the
   visible symptom is a loop between `/sign-in` and the page** (Clerk sees a session, the proxy does not). The API logs `clerk_azp_refused`
   with the origin to list. Preview deployments on `*.vercel.app` need nothing (derived); a custom domain on a branch must be listed.
4. First preview deployment (needs the owner's authorization, **NOT VERIFIED until then**): (a) record which layer acted on a signed-out
   `curl -sI https://<preview>/dashboard`: a `307` with `location: /sign-in?redirect_url=...` means the proxy ran inside the service; a `200`
   means only the client gate protects pages (it redirects in the browser); either is acceptable, the API authorizes every call; (b) sign in
   on the preview URL and open `/dashboard`: staying there means `VERCEL_URL` reached the web build and the derived origins match; a loop
   means they do not (list the URL in `CLERK_AUTHORIZED_PARTIES`).

## For other packages (integrator: these files are not mine)

- **`test` package (`apps/web/playwright.config.ts`, `src/playwright-config.test.ts`, `e2e/**`)**: in sandbox mode the page is served at
  `http://127.0.0.1:3100` and a session token's `azp` is that origin. The API process already gets `APP_BASE_URL`/`CLERK_AUTHORIZED_PARTIES`
  set to it; **the web process (build and start) needs the same, or the proxy signs every sandbox session out**:

  ```diff
       env: {
         API_ORIGIN: apiOrigin,
  +      // The proxy checks the session token's azp claim; next.config.ts inlines these at build time.
  +      ...(sandbox ? { APP_BASE_URL: webOrigin, CLERK_AUTHORIZED_PARTIES: webOrigin } : {}),
  ```

  and in `playwright-config.test.ts` ("authorizes the origin the sandbox e2e page is served from") add
  `expect(web?.env?.APP_BASE_URL).toBe(origin); expect(web?.env?.CLERK_AUTHORIZED_PARTIES).toBe(origin);`. Account-free runs (`APP_ENV=test`)
  need nothing: no parties, no check. Also: `e2e/http.spec.ts` can add `GET /api/v1/diagnostics/access` signed out -> `401` next to the integrations
  check. The client gate cannot be separated from the proxy in a browser test (the proxy redirects first), so it is covered by the component
  and layout tests above only; I ran no e2e.
- **`caps` package**: nothing to merge. `OperatorOnly` and `useDiagnosticsAccess` keep their names and behavior; `features/workspace` is untouched.
- Merge conflicts to expect: `apps/web/src/lib/env.server.ts` and its test (if `config` work continues), `packages/contracts/openapi.json` and
  `apps/web/src/lib/api/schema.d.ts` (regenerate with `pnpm contracts`, do not hand-merge), `packages/contracts/config-cases.json`.

## Documentation changes needed (integrator)

- `docs/architecture.md`: Frontend section and step 6 of "Adding a feature": a page requires sign-in in **two layers** that read one list
  (`src/lib/page-access.ts`): the proxy on the server and `PageGate` in the root layout (`src/ui/page-gate.tsx`), presentation only (FE-05);
  making a page public means `PUBLIC_PAGES` **and** `EXPECTED_PUBLIC_ROUTES` (now in `src/lib/app-routes.testing.ts`; `docs-notes/web.md` says
  `page-access.test.ts`); `layout.test.tsx` and `page-access.test.ts` are the fitness tests.
- `docs/integrations.md`: Clerk step 4 and callback table: the web proxy applies `CLERK_AUTHORIZED_PARTIES` too (same defaults and preview
  derivation, read at build time); the diagnosis for an unlisted origin (loop; `clerk_azp_refused`). "Access": the web asks
  `GET /api/v1/diagnostics/access`, so a customer's browser console stays clean; add the endpoint to the diagnostics route list.
- `docs/operations.md` and `README.md`: step "Before a first deployment": the web build also needs `APP_BASE_URL`; change either variable ->
  redeploy; troubleshooting row for the sign-in loop.
- `dev-docs/engineering-implementation-plan.md` §21: FE-05 ("proxy default deny (`mocked`), client page gate (`mocked`: `page-gate.test.tsx`,
  `layout.test.tsx`), whether the proxy runs inside a Vercel service NOT VERIFIED (DEC-001 preview); the API authorizes every call"); ENV-03
  (web mirrors the party rules, shared cases: 116 checks); AUTH row for `azp` (proxy and API); ADM-01 (probe adds no audit entry); TEN-02
  (the route declares `OWN_ACCOUNT`). ADR-0004: page protection in two layers because "middleware is not supported inside a service" is
  documented and the lab report says otherwise (NOT VERIFIED).
- `docs-notes/web.md` "Deferred (ii)" is resolved here. `dev-docs/evidence/2026-10-15-review-fixes.md`: the probe now has its own endpoint.

## Decisions taken (owner delegated; reversible)

- **The gate sits in the root layout around `children`**, not in a route group with a `SignedInGate` per group: a new page is gated by being
  created, with no wiring to forget, and the fitness test proves it. Alternative: `src/app/(app)/` plus a gate in its layout (then the
  second-root-layout test would have to cover groups).
- **A shown page stays shown** (latch per path) instead of redirecting whenever Clerk says signed out. Alternative: redirect on any
  signed-out state: stricter, but it discards form state on session expiry (FE-04) and races Clerk's sign-out navigation.
- **A visible placeholder** ("Loading…") while Clerk loads, not an empty `<main>` (live region for screen readers). Alternative: render nothing.
- **Strict mirror of `APP_BASE_URL`** in the web validator (required in a deployment, https in production), so a bad deployment fails at the web
  build, not at the first API request. Alternative: only default from it when set. Reversal: drop the first two branches in `originSettings`.
- **`browserOrigin` ported from the API** rather than the WHATWG parser the web already used for `API_ORIGIN`, pinned by a table both apps run.
- **One predicate in the API** (`may_use`), used by the listing and by the new route, instead of a copy of the policy in the route.
  The answer is a `200` with a body for everyone (a 404 or 204 would keep the console noise).
- **Preview environment of `scripts/test-vercel-build.mjs` edited** although it is `deploy`'s file: the change follows from this package's
  new requirement and from `config`'s earlier one, and the deploy notes asked for it.

## Limitations and NOT VERIFIED

- The behavior of Clerk's real session state around sign-out, organization switches and `isLoaded` is read from the SDK, not observed: all gate
  tests mock Clerk (`mocked`). The sandbox (DEC-006) must show that signing out from a protected page lands on the home page and that
  switching organizations does not reload the page.
- Whether the Next.js proxy runs inside a Vercel service, and whether `VERCEL_URL`/`VERCEL_BRANCH_URL` reach the web service's build: NOT VERIFIED
  until a preview deployment. If the proxy does not run there, its `azp` check is not active either; the API still enforces `azp` on every call.
- A signed-in visitor now sees "Loading…" instead of the page frame until Clerk loads (the pages already waited for Clerk before fetching).
- The sign-in loop for an unlisted origin (Manual steps 3) is a consequence of enforcing `azp` at the proxy, which the task asked for.
- `pnpm test:vercel-build`, `pnpm test:new-module`, `pnpm test:init-template` and every e2e suite were not run.

## Evidence (`mocked`/`local`; nothing is `sandbox` or `live`)

Machine under heavy load from unrelated processes, one heavy command at a time.

| Command | Result |
|---|---|
| `pnpm --filter web lint` / `typecheck` / `format:check` | clean (`--max-warnings=0`) |
| `pnpm --filter web test` (once, after all changes) | 41 files, **789 passed**, 1 skipped (a skip already on the base) |
| `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=<placeholder> APP_BASE_URL=https://app.example.com CLERK_AUTHORIZED_PARTIES=... pnpm build` (once) | compiled; the seven page routes above are `○ Static`; parties inlined in the proxy bundle only; `pnpm check:bundle-secrets` clean |
| `pnpm contracts` then `pnpm contracts:check` | regenerated in its own commit (`DiagnosticsAccess`, `/api/v1/diagnostics/access`); up to date |
| `apps/api`: `ruff check`, `ruff format --check`, `lint-imports` (8 contracts kept), `mypy` (86 files) | clean |
| `TEST_DATABASE_URL=...54360/app uv run pytest -q -p no:cacheprovider` (full suite, once) | **1651 passed, 5 skipped** (see "Full API suite") |
| `uv run pytest tests/unit/test_config_cases.py` and `vitest src/lib/env-cases.test.ts` | 116 passed in each app (44 cases + 72 origins) |
| `uv run pytest tests/unit/test_diagnostics_access.py tests/integration/test_diagnostics_access.py` | 34 passed |
| `pnpm check:placeholders` | ok |

Fidelity check of `browserOrigin` (one-off, not committed): 72 addresses through the API's `http_origin` and the port; the first version differed on two
IPv6 forms (the URL parser normalised them), fixed to keep them as written; now identical. The 72 are the committed table.

Mutation runs (each reverted; the named tests failed):

- Gate: no `<PageGate>` in the layout (7 failed across `layout.test.tsx` and `page-access.test.ts`); a gate that lets every page through (14);
  no redirect (11); a gate that treats every path as protected (11, all on public pages); no per-path key (1); no "shown" latch (2); an unchecked
  return path (12); a second root layout in a route group (the structural test); a new page that is not listed (gated by default, passes); the
  same page listed in `PUBLIC_PAGES` only (the exact-set test and the layout test fail).
- Parties: proxy ignores the option (1); `next.config.ts` does not pass the value (2); branch URL not derived (4); derived outside a preview
  deployment (3); no default party; no hostname filter; `APP_BASE_URL` optional in a deployment; http allowed in production; no host validation;
  no port range; default port kept; entries not normalised; entries checked under `APP_ENV=test`; `APP_BASE_URL` not a web variable (each
  failed one or more tests). Survivors: dropping the `@`, backslash and `%` pre-checks of `browserOrigin` changed nothing, because the
  structural regex already refuses those addresses; the redundant pre-check was removed.
- Probe: always-true operator policy; the local opening lost; diagnostics on in production; the opening in other environments; an impersonator
  allowed; the enabled flag ignored; the operator check forgotten; wiring `enabled=True`; a constant answer; an audited refusal; a spent
  rate-limit counter; the wrong declared access; a response that names an operator count; the listing on a different policy from the answer;
  web provider granting on any success, asking the old report, probing while signed out, not tied to the user, wrong client path (each failed a test).

### Full API suite

`local`: `TEST_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54360/app uv run pytest -q -p no:cacheprovider` once, after the last code change:
**1651 passed, 5 skipped** (the opt-in Stripe sandbox suite), 336 s. The two `test_platform.py` route-manifest failures that
`docs-notes/config.md` reported on its own branch (`POST /api/v1/billing/sync` without a declaration) no longer occur on the integrated base.
