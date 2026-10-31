# AGENTS.md

The authoritative specification for this repository is
[`dev-docs/engineering-implementation-plan.md`](dev-docs/engineering-implementation-plan.md).
Read it before making changes. Do not restate its requirements in other files; link to it.

## Critical rules (full text in §17 of the specification)

1. **Never** push, publish, release, deploy, or modify remote repository or service state without explicit authorization from the owner. Local commits are allowed.
2. **Never** expose, commit, print, log, or transmit secrets, credentials, or personal data.
3. **Never** resolve an open decision (§3) on the owner's behalf or implement scope it blocks. Ask. A decision the owner delegated and an agent adopted stays `OPEN` ("adopted by delegation") until the owner confirms it: [ADR-0004](dev-docs/adr/0004-cycle-1-adopted-decisions.md).
4. **Never** fabricate test results, measurements, security findings, or deployment evidence. Label integration evidence as `mocked`, `sandbox`, or `live`; a run on one machine against local services is `local` (§20). Write `NOT RUN` for a check that exists and was not run.
5. **Never** weaken tests or disable required checks to get a passing result. Never bypass a hook (`--no-verify`).
6. Preserve unrelated work. Ask before destructive operations.

## Where things are

- Open decisions register: §3. Decisions adopted by delegation: [`dev-docs/adr/0004-cycle-1-adopted-decisions.md`](dev-docs/adr/0004-cycle-1-adopted-decisions.md)
- Architecture and boundaries: §4, [`docs/architecture.md`](docs/architecture.md), ADR-0003
- Adding a feature module: `pnpm new:module <name>` (never hand-wire a module differently). The steps after it, including the `can` flags the UI reads, are in `docs/architecture.md` under "Adding a feature"
- Implementation phases and current status: §18, §23
- Vercel deployment proof (DEC-001, local only): [`proofs/dec-001/`](proofs/dec-001/README.md)
- Quality gates and evidence statuses: §19, §20
- Requirements traceability matrix: §21
- Evidence records: [`dev-docs/evidence/`](dev-docs/evidence/2026-10-30-cycle-1-fix-round.md) (latest: the cycle 1 fix round)

## Verify before claiming anything works

```bash
pnpm db:up            # local PostgreSQL; the API tests fail without a database
pnpm verify           # everything CI requires for the api, web, and secrets jobs, in CI's order, ending in a PASS/FAIL table
pnpm verify:quick     # while iterating: lint, format, types, and the API and web tests
pnpm hooks:install    # once per clone: scans staged changes for secrets before each commit (Docker is enough without gitleaks)
```

- `pnpm verify --help` lists its options and `pnpm verify --list` its steps. A step that cannot run (gitleaks not installed, `--offline`, `--no-db`) is reported as SKIPPED with the reason, never as PASS.
- CI's other jobs have their own commands: `pnpm test:vercel-build` (deployment configuration, needs network) and `pnpm test:supabase` (Supabase's Postgres image, needs Docker). `pnpm check:setup` checks the configuration and the integration probes without signing in.
- Test tiers: unit, integration, and contract tests run in `pnpm test`; every API response in a test session is checked against the OpenAPI document. `pnpm test:e2e` is the account-free browser tier (public pages, mocked: clerk-js is blocked). The sandbox tier (`pnpm verify:sandbox`, or `E2E_SANDBOX=1 pnpm test:e2e`) needs provider keys in `.env`.
- Commands that contact a provider or create objects in an account need the owner's authorization (rule 1): `pnpm setup:stripe`, `pnpm verify:sandbox`, `pnpm test:stripe-sandbox`, `pnpm check:setup --send-test-events`, and anything with `--allow-remote`. Plain `pnpm check:setup` contacts only the providers whose mode is `sandbox`.

Record results in `dev-docs/evidence/` (one file per run) and label each integration as mocked, local, sandbox, or live. `pnpm verify:sandbox` and `pnpm check:setup` print a record ready to paste. With sandbox keys in `.env`, the `/integrations` page runs a live check per service.
