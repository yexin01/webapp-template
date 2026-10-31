# AGENTS.md

Instructions for AI coding agents and for contributors. Add this project's own conventions and decisions below.

<!-- adopter-agents -->

## Critical rules

1. **Never** push, publish, release, deploy, or change remote repository or service state (cloud projects, provider dashboards, DNS) without explicit authorization from the owner. Local commits are allowed.
2. **Never** expose, commit, print, log, or transmit secrets, credentials, or personal data. Tests and examples use obviously fake placeholders.
3. **Never** fabricate test results, measurements, security findings, or deployment evidence. Label integration evidence as `mocked`, `sandbox`, or `live`.
4. **Never** weaken tests or disable required checks to get a passing result.
5. Preserve unrelated work. Ask before destructive operations: deleting files or data you did not create, resetting a database, rewriting history.
6. Ask before security-sensitive, billing-related, or architectural decisions, and record the outcome as a short ADR in `dev-docs/adr/`.

## Where things are

- Architecture and module boundaries: [`docs/architecture.md`](docs/architecture.md) and the ADRs in [`dev-docs/adr/`](dev-docs/adr/) (ADR-0003: modular monolith). Add a feature module with `pnpm new:module <name>`; never hand-wire a module differently.
- Integrations: [`docs/integrations.md`](docs/integrations.md). Operations: [`docs/operations.md`](docs/operations.md). Personal data: [`docs/data-inventory.md`](docs/data-inventory.md).
- Inherited reference: [`dev-docs/engineering-implementation-plan.md`](dev-docs/engineering-implementation-plan.md) is the specification this code was built against. Requirement IDs in code and tests (for example `ENV-02`) refer to it. Its open-decisions register, phase plan, and status describe the template's own history: they do not block work here.
- This project's verification evidence: [`dev-docs/evidence/`](dev-docs/evidence/README.md).

## Verify before claiming anything works

```bash
pnpm db:up            # local PostgreSQL; the API tests fail without a database
pnpm verify           # the checks of CI's api, web, and secrets jobs, in CI's order, ending in a PASS/FAIL table (`pnpm test:vercel-build` and `pnpm test:supabase` cover the other two jobs)
pnpm verify:quick     # while iterating: lint, format, types, and the API and web tests
pnpm hooks:install    # once per clone: scans staged changes for secrets before each commit (Docker is enough without gitleaks)
```

- First-time setup, from clone to a running app, is in [`docs/setup.md`](docs/setup.md).
- `pnpm verify --help` lists its options and `pnpm verify --list` its steps. A step that cannot run (gitleaks not installed, `--offline`, `--no-db`) is reported as SKIPPED with the reason, never as PASS.
- `pnpm check:setup` checks the configuration and the integration probes without signing in; it contacts only the providers whose mode is `sandbox`. Commands that contact a provider or create objects in an account need the owner's authorization (rule 1): `pnpm setup:stripe`, `pnpm verify:sandbox`, `pnpm test:stripe-sandbox`, `pnpm check:setup --send-test-events`, and anything with `--allow-remote`.

Record results in `dev-docs/evidence/` and label each integration as mocked, sandbox, or live. With sandbox keys in `.env`, the `/integrations` page runs a live check per service.
