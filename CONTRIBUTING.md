# Contributing

<!-- template-only:start -->
Thanks for helping improve the template. Before changing behavior, read the engineering specification in [`dev-docs/engineering-implementation-plan.md`](dev-docs/engineering-implementation-plan.md). It is the source of truth for requirements, open decisions, and quality gates.
<!-- template-only:end -->

Requirement and decision IDs in code and tests (for example `ENV-02`) refer to the [engineering specification](dev-docs/engineering-implementation-plan.md); a project made from the template keeps it as inherited reference.

## Setup

Follow the [README quick start](README.md#quick-start). The full automated test suite runs without any external accounts:

```bash
pnpm bootstrap
pnpm hooks:install   # optional, once per clone: scans staged changes for secrets before each commit
pnpm db:up           # local PostgreSQL; the API tests fail without it
pnpm verify          # the checks of CI's api, web and secrets jobs; `pnpm verify:quick` while iterating
```

`pnpm verify` ends in a table of what ran. A step that could not run (gitleaks not installed, `--offline`, `--no-db`) shows as SKIPPED with the reason. `pnpm verify:sandbox` runs the provider checks and needs your own sandbox keys.

## Ground rules

- **Small, focused changes.** Each pull request should be reviewable on its own and include tests.
- **Server-side authorization first.** Every new endpoint must:
  - depend on authentication;
  - declare what authorizes it (`dependencies=[requires(...)]`) and call the domain authorization policy in the use case;
  - declare its errors on its router (`responses=errors(...)`);
  - be covered by negative tests: cross-user, cross-tenant, and role.

  The route-manifest test fails if an endpoint is unprotected or never evaluates the permission it declares, and the API tests fail when a response differs from the OpenAPI document.
- **The server decides what the UI offers.** A list endpoint returns `can`, one boolean per action the UI shows. The web app never reads a Clerk role (`apps/web/src/no-client-roles.test.ts` fails if it does). A new page requires sign-in until it is listed in `apps/web/src/lib/page-access.ts`.
- **Respect module and layer boundaries.** The API is a modular monolith (ADR-0003): feature modules in `app/modules/` on a shared kernel in `app/kernel/`. Inside a module, `module.py` wires `routes.py` and `adapters.py` to `service.py`, which depends on `ports.py` and `domain.py`; the domain is framework-free. A module reaches another module only through its `api.py`, and the kernel never imports a module. Add a module with `pnpm new:module`. [docs/architecture.md](docs/architecture.md#module-anatomy) has the full rules; CI enforces them with `lint-imports` and the architecture tests. The web app holds no business logic, secrets, or direct database or Stripe access.
- **Contracts.** If you change API schemas, run `pnpm contracts` and commit the regenerated files.
- **Migrations** are additive and immutable once merged, and linted by `pnpm api:lint` (squawk). Destructive changes need an expand → contract plan and explicit maintainer approval. Keep every new database object in schema `app`.
- **Never weaken tests or checks** to make CI pass, and never bypass a hook. Never commit secrets, real personal data, or production identifiers. Tests use synthetic data only.
- **Be honest about verification.** Label evidence as mocked, local, sandbox, or live, and write NOT RUN for a check that exists and was not run.
- **New infrastructure, providers, or architectural changes** need maintainer approval before implementation<!-- template-only:start -->, and an entry in the open decisions register<!-- template-only:end -->. Record the outcome as an ADR.

## Checks

Your pull request must pass the CI jobs. `pnpm verify` runs the first three on your machine; the other two have their own commands. The CodeQL workflow (`.github/workflows/codeql.yml`) runs on GitHub only.

- **API:** lint and format, import boundaries, migration lint, strict type checking, the tests with a database, and the dependency audit.
- **Web:** lint and format, strict type checking, unit tests, contract drift, script tests, template initialization, placeholders, documentation links, the module generator check, the account-free build with its bundle secret scan, the dependency audit, the license check, and the account-free browser tests.
- **Secret scan:** gitleaks over the history.
- **Deployment configuration:** `vercel build` of the root `vercel.json` with no deployment (`pnpm test:vercel-build`).
- **Supabase image:** the migrations and database tests on Supabase's Postgres image (`pnpm test:supabase`, needs Docker).

## Commit messages

Use concise, imperative summaries (for example, `fix(api): reject expired webhook signatures`). Reference requirement IDs or decision IDs where relevant.

## License

By contributing, you agree that your contributions are licensed under the [Apache License 2.0](LICENSE).
