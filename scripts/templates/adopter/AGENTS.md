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
- Setup and integrations: [`docs/integrations.md`](docs/integrations.md). Operations: [`docs/operations.md`](docs/operations.md). Personal data: [`docs/data-inventory.md`](docs/data-inventory.md).
- Inherited reference: [`dev-docs/engineering-implementation-plan.md`](dev-docs/engineering-implementation-plan.md) is the specification this code was built against. Requirement IDs in code and tests (for example `ENV-02`) refer to it. Its open-decisions register, phase plan, and status describe the template's own history: they do not block work here.
- This project's verification evidence: [`dev-docs/evidence/`](dev-docs/evidence/README.md).

## Verify before claiming anything works

```bash
pnpm db:up                       # local PostgreSQL for API tests
pnpm lint && pnpm typecheck
pnpm test && pnpm test:e2e       # API + web; e2e browser/sandbox suites need E2E_SANDBOX=1
pnpm contracts:check && pnpm test:init-template && pnpm check:placeholders && pnpm test:new-module
pnpm test:scripts && pnpm check:links
pnpm build:check                 # account-free build and bundle secret scan; `pnpm build` needs your Clerk keys
pnpm security:audit && pnpm security:licenses
pnpm check:setup                 # configuration and integration checks, with an evidence block
```

Record results in `dev-docs/evidence/` and label each integration as mocked, sandbox, or live. With sandbox keys in `.env`, the `/integrations` page runs a live check per service.
