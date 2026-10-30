# Package `apix`: API extension safety

Branch `arch/apix`. Everything below is API-side. `apps/web`, `README`, `docs/`, and `dev-docs/` are untouched; the
documentation changes are listed for the integrator.

## What changed

### Errors and 5xx reporting (`api-arch-error-taxonomy-closed`, `obs-api-domain-5xx-invisible-to-sentry`)

- `DomainError` declares a `status` class variable. A subclass must declare a 4xx or a 503, or the class cannot be
  defined (`TypeError`: "declare status (4xx, or 503)"). The base class keeps 500, so a bare `raise DomainError()` is a 500.
  The name is `status`, not `status_code`: Sentry's Starlette hook reports any exception with an int `status_code`, which
  would send every expected 503 around the throttle.
- `kernel/http/problem.py` maps by `exc.status`; the closed `_STATUS` map is gone. A module adds an outcome by declaring
  an error class, with no kernel edit.
- The request transaction commits for a completed request and for a `DomainError` that is not a 500, and rolls back for
  anything else, so the documented contract holds for module-defined errors: a 4xx or a 503 the service chose keeps its
  writes (a denial's audit entry), a bare `DomainError` or any other exception rolls back and reports.
- A 5xx `DomainError` is reported to Sentry from the error handler (`observability.report_server_error`), through the
  existing `before_send` scrubber (so it carries the `request_id` tag and is redacted), tagged `error_code`, fingerprinted
  by code and cause type, at most once a minute per (code, public message, cause type) per instance (`ErrorThrottle`,
  at most 256 keys). It never raises and never waits (the SDK sends in the background). `NOT_CONFIGURED` is `warning`
  level; everything else `error`. 4xx stay unreported. Unexpected exceptions still reach Sentry through the ERROR log, as before.
- The warning log line `dependency_or_configuration_error` now also carries `cause_type`.

### Impersonation (`clerk-impersonation-act-claim-ignored`)

- `ClerkTokenVerifier` reads the `act` claim. Its `sub` becomes `VerifiedIdentity.actor_external_id`, carried into
  `TenantContext.actor_external_id`. An `act` that is not an object with a non-empty string `sub` (at most 255 characters)
  is an invalid token (401, generic).
- `authenticate` refuses an actor session with **403 FORBIDDEN** unless `CLERK_ALLOW_IMPERSONATION=true`. Logged as
  `actor_session_refused` (no identifiers).
- When allowed, `PgTx.audit` writes `app.audit_log.impersonator_clerk_id` (migration `20261030000030`; nullable; RLS
  policies unchanged) and the request logs `actor_session` with `actor_external_id`. `AuditEntry` and its call sites are unchanged.
- Diagnostics answer 404 to an actor session, so an impersonator does not inherit the operator allowlist.

### Composition (`api-arch-integration-extension-path`, `api-arch-composition-fails-silently`)

- `build_container` raises `TypeError` naming any adapter override no builder looked up (`kernel.wiring.Overrides`, a
  `Mapping` that remembers the keys asked for; `pick()` and the module builders are unchanged).
- `load_settings` raises `TypeError` for a keyword that names no setting (`billing_mod=`, or an upper-case `BILLING_MODE=`,
  which the model silently ignored). Names starting with `_` (`_env_file`) pass.
- Integration probes are collected from every module's `Contribution` through `Container.module_contributions()`, the list
  `pnpm new:module` already maintains (markers unchanged). `Container.diagnostics` is built last, from those. A module that
  adds a probe no longer needs a bootstrap edit. `DiagnosticsService` raises `ValueError` for two probes with one key.
- `tests/architecture/test_settings.py`: no two settings classes read one environment variable (field name or alias), and
  every module `*_problems` entry point is called in `app/settings.py`.

### Membership refresh (`api-arch-membership-refresh-n-plus-one`)

`PgIdentityRepository.replace_user_memberships` writes organizations and memberships in one statement each (`unnest`
arrays, `ORDER BY` for a fixed lock order): 8 round trips for any number of memberships (was 2N+6, 106 at N=50). A read
that lists an organization twice is reduced first (last listing wins), because `ON CONFLICT DO UPDATE` refuses a row twice.
The query-count test now measures the refresh itself. The lock-ordering test was reworked (see Limitations).

### Layer imports (`api-arch-import-allowlist`)

`tests/architecture/test_layer_imports.py`: kernel core and domains may import the standard library (and
`typing_extensions`) only; ports, services, probes also `pydantic`; routes also `fastapi`, `starlette`, `pydantic`, `anyio`.
From `app.kernel`: the core only (routes also `app.kernel.http` and `request_context`); never `app.settings`,
`app.bootstrap`, or `app.cli`. The kernel-core list is read from the import-linter contract in `pyproject.toml`
(`pyproject.toml` itself is unchanged). Add an allowance in that test file, with a reason, for a utility that is not a vendor SDK.

### Authorization declarations (`secarch-ten02-authz-convention`)

- `requires(...)` (`app.kernel.http.auth`) in a route's `dependencies` declares a `Permission` that a module registers in
  its domain, or an `Access` kind (`OWN_ACCOUNT`, `ANY_MEMBER`, `OPERATOR`, in `app.kernel.authorization`).
- The route manifest (`tests/integration/test_platform.py`) fails a route with no declaration, with two, with a permission no
  module registers, or with an access kind that does not fit its path.
- A declared `Permission` must be evaluated (`authorize`/`is_allowed`) while the route runs; a request that completes
  without it answers 500 and rolls back (fail closed). Refusals are unchanged: the use case still decides inside its
  transaction and audits a denial (NT-07).
- Projects, billing, LLM, diagnostics, `/me`, and the `pnpm new:module` routes template declare their routes. The OpenAPI
  contract is unchanged (verified: `app.cli openapi` output is identical to `packages/contracts/openapi.json`).
- Refuted: the claim that the manifest collected zero routes under FastAPI 0.143. It collects 22 routes (the handling of
  included routers is already in `_api_routes`); no manifest fix was needed.

## New commands and environment variables

- `CLERK_ALLOW_IMPERSONATION` (default `false`), in `.env.example` after `CLERK_WEBHOOK_SIGNING_SECRET`.
- No new commands, no new `package.json` scripts.
- Migration `supabase/migrations/20261030000030_audit_impersonator.sql`.

## Manual steps for the owner or adopter

- None required. To accept Clerk impersonation (dashboard "Impersonate user", Backend API actor tokens, agent tasks): set
  `CLERK_ALLOW_IMPERSONATION=true` where the API runs (Vercel project environment variables), redeploy, and run
  `python -m app.cli migrate` (the new column). Clerk shows its own impersonation banner in the web app; the template adds nothing there.
- Optional, in Sentry: an alert rule on tag `error_code:DEPENDENCY_UNAVAILABLE`. Sentry's default high-priority alert
  already notifies on new error-level issues.

## Documentation changes needed (integrator)

- `docs/architecture.md`
  - Request-scoped transactions bullet on commit: say a module error commits when it declares a 4xx or a 503, and a bare
    `DomainError` (500) rolls back.
  - Add "Adding an error": subclass a kernel category (or `DomainError`) with `code` and `status` (4xx or 503). It keeps the
    request's writes. Anything else is a plain exception: 500, rollback, reported to Sentry.
  - Line 90: "bootstrap collects every module's contributed probes". Add "Adding an integration" after "Adding a feature"
    (checklist below).
  - Query budget: the membership refresh transaction is also a fixed number of round trips.
  - Fitness functions row: named SDKs by import-linter, the allowlist by `test_layer_imports.py`. Mention
    `test_settings.py` and `test_errors.py`.
  - TEN-02: routes declare `requires(...)`; the manifest checks it.
- `docs/operations.md`, Provider outages: each 503 from an unavailable dependency is reported to Sentry, tagged
  `error_code`, at most once a minute per cause per instance; `NOT_CONFIGURED` is warning level (the public Clerk webhook
  answers it while `CLERK_WEBHOOK_SIGNING_SECRET` is unset). The log line carries `cause_type`. Add: an impersonated
  action is visible in `app.audit_log.impersonator_clerk_id` and in the `actor_session` log line.
- `docs/data-inventory.md`: `audit_log` also holds the Clerk user ID of an actor (`impersonator_clerk_id`) when
  impersonation is allowed.
- `docs/integrations.md`: `CLERK_ALLOW_IMPERSONATION`, in the Clerk section; the 403 an actor session gets by default.
- `dev-docs/engineering-implementation-plan.md`
  - §21: ARCH-08 evidence row (membership refresh), ARCH-01/ARCH-04/ARCH-13 rows (layer import allowlist), TEN-02 row
    (declarations and the fail-closed check), ADM-01/ADM-02 row (actor recorded; refused by default).
  - §13.2: T-21 "A provider-side actor (Clerk dashboard impersonation, Backend API actor token, agent task) acts as a user
    without attribution", boundary 3, controls ADM-01, ADM-02. Holders of `CLERK_SECRET_KEY` stay trusted (sign-in tokens
    carry no `act`).
  - ADR-0004: the decisions below.
  - `dev-docs/evidence/`: the mutation runs listed under Evidence.
- `AGENTS.md`: nothing.

### Extension checklist for `docs/architecture.md` ("Adding an integration")

Only mechanisms that exist today:

1. Settings: `app/modules/<name>/settings.py` with a settings class and a `<name>_problems(s, core)` function
   (`mode_problems("<NAME>_MODE", ...)`); inherit the class and call the function in `app/settings.py`
   (`Settings` bases and `_validate`). Reserve request threads in `reserved_request_threads` if calls hold threads until
   the provider answers. `tests/architecture/test_settings.py` fails a colliding environment variable or an uncalled `*_problems`.
2. Port, real adapter, and fake: the port in `ports.py`, the SDK only in `adapters/` (`test_layer_imports.py` fails an SDK
   anywhere else), chosen with `pick(overrides, "<key>", factory)` in `module.py`. An override key no builder reads raises.
3. Probe: `probe.py` and a `describe()`, returned in the module's `Contribution(probes=[...])`. Bootstrap collects it;
   a key two probes share raises at startup. Add the key to `IntegrationKey` (`kernel/diagnostics.py`), then `pnpm contracts`.
4. Errors: an adapter raises `DependencyUnavailableError` (503, reported to Sentry) for an outage or a rejected credential,
   and a 4xx error for a request the provider refused. A new outcome is an error class next to the domain with `code` and `status`.
5. Routes: `dependencies=[requires(PERMISSION)]` on each route, a `Permission` in `domain.py`, and `authorize()` in the use case.
6. Analytics events: `ANALYTICS_ALLOWLIST` (`observability.py`) and `docs/data-inventory.md`.
7. Inbound webhooks: follow identity's pattern: raw body, verify the signature before any database work, a per-provider ledger
   table keyed by the provider's event ID using `EventStatus`, and a migration.
8. `.env.example` and `docs/integrations.md` (setup, and the callback-URL table).

## Decisions taken (owner delegated; reversible)

- **Actor sessions get 403, not 401** (briefing's verifier correction). The token is valid, so refusing it is an authorization
  decision; the web maps 401 to "sign in again", and the Clerk session stays active, so staff would loop between the app and
  sign-in. A malformed `act` stays a generic 401 (AUTH-03). Alternative: 401 from the verifier.
- **A 503 keeps the request's writes** (status quo; the contract in `docs/architecture.md` says a 503 the service chose
  commits). Only a 500 rolls back. Alternative: roll back on 503 too; that is the owner's open item "Commit on
  DependencyUnavailableError" (`dev-docs/evidence/2026-10-15-second-review-identity-db.md`) and was not changed.
- **`NOT_CONFIGURED` is reported at warning level**, not skipped, so an unconfigured webhook secret is visible without paging.
- **Route-level fail-closed check** (500 when a declared permission was never evaluated) goes beyond the finding's test-only
  fix, so a forgotten `authorize()` is found by the first test of the route rather than only by review. Alternative: the
  manifest checks declarations only.
- **`x-permission` in the OpenAPI document was not added** (the reviewer's idea): the declaration is a dependency, which the
  document does not show. Adding it means a second declaration per route.

## Limitations and follow-ups

- Web: `apps/web/src/lib/api/errors.ts` shows a generic message for an error code it does not know. The server now lets a
  module add codes with 4xx or 503, so the default branch should show the server message for 4xx
  (`{title: "Could not complete this", action: "none"}`) and offer retry for 503, with `client.test.ts` cases. Not done here (web is out of scope).
- The throttle is per instance and in memory (OBS-07); a fleet of N instances can report one cause N times a minute.
- The Sentry event for a Stripe rejection carries the chain `DependencyUnavailableError` from the Stripe `AuthenticationError`;
  the kernel does not tell a rejected credential from an outage (ARCH-04 keeps vendor types in adapters). A normalized
  `DependencyCredentialsRejectedError` subclass would, if wanted.
- Impersonation is audited only where the use case writes an audit entry (organization project deletes, diagnostics, and
  entries future modules write). Other requests of an actor session are recorded by the `actor_session` log line and its request ID.
- The lock-ordering test of the membership refresh installs a trigger (`SECURITY DEFINER`, in the test database only) that
  holds a transaction's second new organization until another session waits on a lock, with a 5 s cap. It does not use fixed
  sleeps. It was changed because the refresh no longer has a statement per organization to hook.
- `tests/conftest.py` is shared: `TokenTableVerifier.issue(user, actor=None)` and `Harness.login(user, actor=None)` gained an
  optional actor.
- Merge notes: `apps/api/app/bootstrap.py` (Container fields, `contributions`, end of `build_container`), `settings.py`
  (`load_settings`), and the route decorators of billing and projects are the likely conflicts.

## Evidence (labelled `mocked` or `local`; none is `sandbox` or `live`)

All against the shared test PostgreSQL 17 (`TEST_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54360/app`),
from `apps/api`, with the `uv run --offline` environment.

- `local`: `uv run ruff check app tests index.py ../../perf`, `ruff format --check`, `lint-imports` (8 kept, 0 broken),
  `mypy` (84 files): all clean.
- `local`: full API suite, `uv run pytest -q -p no:cacheprovider`: 974 passed (run before the last commit: 4 failed in
  `test_jobs_configuration.py`, caused by `Container.diagnostics` being an `init=False` field that `dataclasses.replace`
  dropped; fixed by making it a cached property, then the full suite passed).
- `local`: `pnpm test:new-module` passed (generates three modules with the new route declarations and runs the API gates,
  architecture tests, RLS tests, contract regeneration, and the web checks on them). `pnpm contracts:check` passed
  (the OpenAPI document is unchanged).
- `mocked` (capturing Sentry transport, loopback Stripe stub): a Stripe 401 for a revoked key on `create_customer` returns
  503 `DEPENDENCY_UNAVAILABLE` and yields exactly one event: level `error`, tag `error_code`, tag `request_id` equal to
  the response header, the `stripe` `AuthenticationError` in the exception chain, no canary key, token, or query secret in
  the event. Before the change: 503 and no event.
- Mutation runs (each change reverted after the run; the named tests failed):
  - commit rule `isinstance(error, DomainError)` only: the bare-`DomainError` request-scope test failed (write committed on a 500);
  - handler without the report call: 5 of the reporting tests failed;
  - actor refusal off, actor not passed to the audit insert, `act` ignored in the verifier, operator check without the actor:
    5, 2, 12+, and 1 tests failed respectively;
  - probes from the hand-written list of three modules: `test_a_probe_contributed_by_any_module_is_listed` failed;
    override check off, duplicate-probe check off, settings keyword check off, a duplicated settings field: each failed its test;
  - membership refresh on the old per-organization loop: the query-count test saw `[8, 106, 106, 8]` round trips
    (now `[8, 8, 8, 8]`); without the `ORDER BY` the reworked lock-ordering test saw `DeadlockDetected`; without the
    de-duplication the repeated-listing test saw `CardinalityViolation`;
  - an installed but unlisted package (`cryptography`, `pydantic_settings`, `uvicorn`) injected into a service, the kernel
    core, a probe, a router: `test_layer_imports` failed each time while `lint-imports` stayed green; `app.settings`
    imported by a service, and `pydantic` by a domain, failed too;
  - a route without `requires(...)`, an `ANY_MEMBER` declaration on a path without an organization, `authorize()`
    removed from `ProjectService.list`, and the fail-closed check disabled: each failed the manifest, API security, or
    authorization tests.
