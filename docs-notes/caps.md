# Notes for the integrator: package `caps`

Branch `arch/caps`, on top of `apix`, `db`, `stripe`, `web`, `obs`, `config`, `onboard` and `deploy` (merge base `70e1fea`). It
closes `web-arch-ui-rederives-authorization` (ARCH-03), which `web` confirmed and deferred with a plan (`docs-notes/web.md`,
"Deferred"). Documentation files are yours to edit; this file says what they need. Evidence is `local` or `mocked`; nothing here
ran against Clerk, Stripe or any hosted service.

## What changed

**API (`apps/api/app/modules/projects`).**

- `domain.py`: `Capabilities(create, delete)`, a frozen dataclass: what the caller may do with projects in one context.
- `service.py`: `ProjectService.capabilities(ctx)` returns `is_allowed(ctx, PROJECT_CREATE)` and `is_allowed(ctx, PROJECT_DELETE)`. They
  are the same `Permission` objects, read through the same module-level names, that `create` and `delete` apply, on the tenant
  context the request resolved. No query, no new rule, no new route, no kernel registry.
- `routes.py`: `ProjectCapabilities` (an OpenAPI schema) and a required `ProjectPage.can`, filled in `page()` for both list
  routes (`GET /projects`, `GET /orgs/{org_id}/projects`) and so on every page. The route manifest is unchanged: each list route
  still declares `requires(PROJECT_READ)` and evaluates it; the two extra evaluations are recorded and not required.
- Contracts regenerated in their own commit (`ProjectPage.can`, `ProjectCapabilities`).

**What `can` means.** It is the decision for the context the request resolved, with the role from the membership mirror (at most
`MEMBERSHIP_MAX_STALENESS_SECONDS` old, TEN-07). `DELETE` still re-reads the role under a row lock, so for up to that bound after a
downgrade `can.delete` can still say yes; the API then answers 403 and audits the denial, as before. `can` shows what to offer; the
routes still decide.

**Web (`apps/web/src`).**

- `lib/use-active-tenant.ts`: `useActiveTenant()` returns `{isLoaded, orgId}` from `useOrganization()` and nothing else about the
  organization. `Workspace`, `ProFeature` and the panel use it, so the tenant is derived in one place.
- `features/projects/projects-panel.tsx`: `<ProjectsPanel />` takes no props. It waits for the tenant (and shows "Sign-in
  unavailable" with a reload when Clerk never loads, §16.4), keys its state per signed-in user and tenant (this moved from
  `Workspace`), and shows the create form, the "create your first" hint and Delete only from the first page's `can`. Until the list
  has answered, and when it fails to load, nothing is offered.
- `features/workspace/workspace.tsx`: `membership`, `canDelete` (a copy of `PROJECT_DELETE` and of the Clerk-to-app role mapping,
  read from Clerk's live role) and the key are gone. `useOrganization()` stays for the heading's name only.
- `src/no-client-roles.test.ts`: fails if any source reads a Clerk role name (`"org:..."`), `membership.role`, `orgRole`,
  `orgPermissions`, `has({ role | permission })`, `<Protect>` or a role-gated `<Show>`. Its patterns are themselves tested.

**Generator (`scripts/`).** `pnpm new:module` produces the same shape.

- API templates: `Capabilities` (domain), `service.capabilities`, `<Entity>Capabilities` and `<Entity>Page.can` (routes).
- Web templates: `panel.tsx.tmpl` follows `ProjectsPanel` (no props, tenant from the hook, decisions from `can`); the generated
  panel test mocks `useOrganization` and covers the same cases as the reference panel.
- `test_module.py.tmpl`: for a personal workspace, an organization admin and an organization member, `can` equals what POST and
  DELETE return, and follows a change to the permissions (members may delete and only admins may create) with nothing else edited.
- `new-module.mjs`: the "Mount `<XPanel />`" step now says the panel takes no props (it is true: it used to fail `tsc` because
  `canDelete` was a required prop); a step 6 tells an adopter how to add a flag for a new action; `<Entity>Capabilities` joins the
  schema names the generator refuses to clash with (FastAPI would rename both clashing schemas).

## Findings

| Finding | Result |
|---|---|
| `web-arch-ui-rederives-authorization` (ARCH-03) | **fixed**: `ProjectPage.can`, panels without role props, `useActiveTenant()`, generator in step |
| verifier addition: the generator step `Mount <XPanel />` omitted required props | fixed (the panel takes none) |
| verifier addition: the UI copies the Clerk-to-app role mapping | fixed (no role is read; a test enforces it) |
| verifier addition: Clerk's live role vs the API's mirror (up to 300 s) | fixed (the UI no longer reads Clerk's role) |
| reviewer's `GET /context` (kernel-hosted, for screens with no list) | not built: the verifier said "later, only if"; see Follow-ups |

## Integration

Likely conflicts: `packages/contracts/openapi.json` and `apps/web/src/lib/api/schema.d.ts` (another package adding a route: run
`pnpm contracts` and keep both outputs), and `apps/web/src/features/workspace/workspace.tsx` (`web2` may touch the diagnostics
wrapper there; keep `useActiveTenant()` and the argument-free `<ProjectsPanel />`). No `cli.py`, `config.py`, CI, `.env.example`,
`package.json` or migration change.

## New commands and environment variables

None. No `.env.example`, `package.json` script or migration change.

## Manual steps for the owner or adopter

None. An adopter who changes the permissions edits `domain.py` (and, for a new action, the flag; see the convention below) and
nothing in the web app.

## The capability-flag convention (for `docs/architecture.md`, "Adding a feature")

> Whether an action is shown is the API's decision, never the browser's. A module that lists a resource returns, with every page,
> `can`: one boolean per action the UI offers. The use case computes it in `capabilities(ctx)` with `is_allowed` on the same
> `Permission` objects its routes enforce, and the contract types it as `<Entity>Capabilities` (the generated module has `create`
> and `delete`). To add an action, such as `archive`: declare its `Permission` (say `NOTE_ARCHIVE`) in `domain.py` and `authorize` it in
> the use case; add the flag to `Capabilities` (domain), `service.capabilities` and `<Entity>Capabilities` (routes); run
> `pnpm contracts`; render the control from `can?.archive` in the panel (`can` is the first page's); and add the action to the
> module's `test_the_list_says_what_the_caller_may_do` table, which compares `can` with what the route returns. The panel reads no Clerk role (`membership`, `orgRole`,
> `has({ role })`); `src/no-client-roles.test.ts` fails the build if it does. Use the tenant from `useActiveTenant()` and nothing
> else Clerk knows. A screen with no list response (navigation, a settings page) must not copy the rule: that is when a
> kernel-level context endpoint is added, as an amendment to ADR-0003.

## Documentation changes needed (integrator)

- `docs/architecture.md`
  - "Frontend", the "server is the authority" bullet: add that a screen takes what the caller may do from the list's `can`, that
    `useActiveTenant()` supplies the tenant and never a role, and that `src/no-client-roles.test.ts` is the fitness test.
  - "Adding a feature": step 4 gets the convention above (a permission needs a flag); step 6 reads "Mount `<XPanel />` (it takes no
    props) in a page".
  - "Module anatomy": `domain.py` also holds `Capabilities`; "Fitness functions": add "The UI shows server decisions and reads no
    role" | `src/no-client-roles.test.ts`, and "A list's `can` equals its routes" | `tests/integration/test_project_capabilities.py`
    and the generated module's test.
- `dev-docs/engineering-implementation-plan.md` §21, ARCH-03 row: replace "Review" with the evidence below (PASS, tested).
- `dev-docs/evidence/`: record the mutation results below as the ARCH-03 evidence the reviewer asked for.
- `docs-notes/web.md` "Deferred: `web-arch-ui-rederives-authorization`" is now done; the table row in that file can say so.

## Decisions taken (owner delegated; reversible)

- **`can` lives on each module's list response**, as the verifier proposed (not a kernel registry, not `GET /me`, not a new route).
  Alternative: one kernel `GET /context`. Cost of the choice: a screen with no list has no flag; add the endpoint then.
- **`<Entity>Capabilities`, not a shared `Capabilities` schema**, so generated modules cannot collide in the OpenAPI document
  (FastAPI renames both clashing schemas, which breaks `Schemas["X"]`). The domain dataclass is `Capabilities` in every module; it is
  not in the contract.
- **Nothing is offered before the API has answered, or when the list fails.** Alternative: show the form until the API says no
  (default allow while unknown), which is the pre-fix behavior. The cost is that the form appears with the list (a moment later).
  The first page's `can` is used for later pages (they are the same context).
- **The old test "a project created while the first page loads is listed once" could not stay**: with no form before the first
  page, that race cannot happen. The dedupe stays (a later page can still repeat a project created here) and its test now says so.
- **`useActiveTenant()` returns `{isLoaded, orgId}` only**, as specified; `Workspace` keeps `useOrganization()` for the heading.
- **The panel keeps the Clerk-unavailable path** (`useClerkUnavailable`, reload), because the old generated panel showed "Sign-in
  unavailable" through `useResource` and a panel that only waits for the tenant would spin forever (§16.4).
- **A source scan** (`no-client-roles.test.ts`) in the web tests, not an ESLint rule: agents may not edit the ESLint config. If the
  owner prefers a lint rule, the scan's regexes are the patterns to port.

## Limitations and follow-ups

- A new action needs its flag added by hand (the convention above); there is no registry that derives flags from permissions. The
  generated test table is what notices a flag that disagrees with its route.
- `can` is as fresh as the request's context (the membership mirror, at most 300 s by default); the route re-checks.
- Not built: a context endpoint for screens without a list (navigation, settings). Nothing needs it yet.
- The sandbox e2e `authenticated.spec.ts` ("create and delete a personal project", "validation errors are announced") was not
  edited (`test` package) and was not run (it needs Clerk keys). It still fits: Playwright waits for the form, which now appears
  with the list.
- `BrowserChecks` still reads the organization's name from `useOrganization()` (display only; it never derived a tenant).

## Evidence (labelled `local` or `mocked`; nothing is `sandbox` or `live`)

Commands run in the worktree (the machine was under heavy load, load average above 300; every command ran alone):

- `local`: `uv run pytest -q -p no:cacheprovider tests/integration/test_project_capabilities.py` (shared test PostgreSQL 17; Clerk and
  Stripe are test doubles): 7 passed.
- `local`: related existing tests (`tests/architecture`, `test_authorization.py`, `test_api_security.py`, `test_project_write_rechecks.py`,
  `test_query_counts.py`, `test_platform.py`, which holds the route manifest and the "declared permission must be evaluated" rule): 138 passed.
- `local`: `uv run ruff check`, `ruff format --check` (173 files), `mypy` (86 files), `lint-imports` (8 contracts kept): clean.
- `local`: full API suite, `uv run pytest -q -p no:cacheprovider`: **1532 passed, 5 skipped** (the Stripe sandbox suite, which needs
  `STRIPE_SANDBOX_TESTS=1`), 4 min 37 s. Run once, after the last API change.
- `local`: `pnpm contracts` then `pnpm contracts:check`: up to date.
- `mocked`: `pnpm --filter web lint` (0 findings, `--max-warnings=0`), `typecheck`, `format:check`: clean.
- `mocked`: `pnpm --filter web test`: 40 files, **650 passed, 1 skipped** (a skip already on the base). New or rewritten: `projects-panel.test.tsx`
  (36 tests), `workspace.test.tsx` (6), `use-active-tenant.test.tsx` (4), `no-client-roles.test.ts` (9).
- `local`: `pnpm test:new-module` (shared test PostgreSQL): **passed**, 2 min, on the final templates. Typical, one-letter and longest
  modules pass lint, types, contracts, architecture, API with RLS and web tests. In a scratch copy, one generated module ran 24 API tests
  (6 new) and 25 web tests.
- `local`: `pnpm build:check` (placeholder Clerk key): compiled; `/dashboard` still prerendered static; the bundle scan found nothing.
- `local`: `pnpm check:placeholders`: clean.
- Not run: `pnpm test:init-template` (init is untouched), the e2e suites (the `test` package owns them; the sandbox flows need Clerk keys).

**Mutation runs** (each reverted; the named tests failed):

- The spec's mutation, `PROJECT_DELETE` granted to `ALL_ROLES` in `projects/domain.py`, with no web change:
  `test_the_list_says_what_the_caller_may_do[organization member]` failed with `{'delete': True} != {'delete': False}` (the member's
  `can.delete` flipped) and `test_every_page_carries_the_decision` failed; the personal, admin and changed-permissions cases passed, which
  is the point: `can` follows the permission. The same flip with the route is `test_the_list_follows_a_change_to_the_permissions`
  (members may delete and not create; `can` and the routes agree).
- Service `capabilities`: `delete` re-derived from the role (a copy of the rule): the changed-permissions test failed for the member.
  `create=True` hard-coded: the same test failed. `create` and `delete` swapped: 3 failed.
- Generated module (scratch copy of the generator output): re-derived `delete`: 1 failed; swapped permissions: 2 failed.
- Panel (`ProjectsPanel`): Delete always shown: 4 failed (3 panel, 1 workspace); form always shown: 5; the empty-state hint always shown: 1;
  tenant gate removed: 1; remount key removed: 2 (panel and workspace); Clerk-unavailable branch removed: 1.
- Generated panel: Delete always shown: 3; form always shown: 3; tenant gate removed: 1; key removed: 2; Clerk-unavailable branch removed: 1.
- A scratch source file with `membership?.role === "org:admin"` made `no-client-roles.test.ts` fail (it named the file and the two reads).

**ARCH-03 row, suggested text** (`dev-docs/engineering-implementation-plan.md` §21): "PASS (tested): `can` equals the routes for personal,
organization admin and organization member and follows a permission change (`test_project_capabilities.py`, and the generated module's
test); the UI reads no Clerk role (`apps/web/src/no-client-roles.test.ts`); panels render `can` (`projects-panel.test.tsx`). Mutation:
`PROJECT_DELETE` to `ALL_ROLES` flips the member's `can.delete` with no web change. Mocked and local; no sandbox evidence is needed."
