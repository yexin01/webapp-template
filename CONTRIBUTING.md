# Contributing

Thanks for helping improve the template. Before changing behavior, read the engineering specification in [`dev-docs/engineering-implementation-plan.md`](dev-docs/engineering-implementation-plan.md). It is the source of truth for requirements, open decisions, and quality gates.

## Setup

Follow the [README quick start](README.md#quick-start). The full automated test suite runs without any external accounts:

```bash
pnpm bootstrap
pnpm db:up
pnpm test
pnpm test:e2e
```

## Ground rules

- **Small, focused changes.** Each pull request should be reviewable on its own and include tests.
- **Server-side authorization first.** Every new endpoint must:
  - depend on authentication;
  - call the domain authorization policy;
  - be covered by negative tests: cross-user, cross-tenant, and role.

  The route-manifest test fails if an endpoint is unprotected.
- **Respect module and layer boundaries.** The API is a modular monolith (ADR-0003): feature modules in `app/modules/` on a shared kernel in `app/kernel/`. Inside a module, `module.py` wires `routes.py` and `adapters.py` to `service.py`, which depends on `ports.py` and `domain.py`; the domain is framework-free. A module reaches another module only through its `api.py`, and the kernel never imports a module. Add a module with `pnpm new:module`. [docs/architecture.md](docs/architecture.md#module-anatomy) has the full rules; CI enforces them with `lint-imports` and the architecture tests. The web app holds no business logic, secrets, or direct database or Stripe access.
- **Contracts.** If you change API schemas, run `pnpm contracts` and commit the regenerated files.
- **Migrations** are additive and immutable once merged. Destructive changes need an expand → contract plan and explicit maintainer approval.
- **Never weaken tests or checks** to make CI pass. Never commit secrets, real personal data, or production identifiers. Tests use synthetic data only.
- **Be honest about verification.** Label evidence as mocked, sandbox, or live.
- **New infrastructure, providers, or architectural changes** need an entry in the open decisions register and maintainer approval before implementation.

## Checks

Your pull request must pass:

- lint and format checks;
- strict type checking;
- import boundaries;
- unit, integration, and end-to-end tests;
- contract drift check;
- bundle secret scan;
- dependency audit;
- license check;
- secret scanning.

## Commit messages

Use concise, imperative summaries (for example, `fix(api): reject expired webhook signatures`). Reference requirement IDs or decision IDs where relevant.

## License

By contributing, you agree that your contributions are licensed under the [Apache License 2.0](LICENSE).
