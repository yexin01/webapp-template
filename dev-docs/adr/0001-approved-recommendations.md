# ADR-0001 — Approved Architecture Recommendations (DEC-007 to DEC-012)

- **Status:** Accepted
- **Date:** 2026-10-14
- **Decided by:** Repository owner
- **Resolves:** DEC-007, DEC-008, DEC-009, DEC-010, DEC-011; DEC-012 (license only)
- **Amended by:** [ADR-0004](0004-cycle-1-adopted-decisions.md): DEC-008, `DATABASE_URL` logs in as the `app_login` role and migrations use `MIGRATION_DATABASE_URL` (§3); DEC-007, under the Vercel Services topology the platform routes `/api/v1/*` to the API, and the Next.js rewrite serves local development (§1)

## Context

The specification's open decisions register listed recommendations for DEC-007 to DEC-011, plus a license question (DEC-012). The owner approved all the recommendations and chose Apache-2.0. The owner also stated that the `.env` will contain test/sandbox keys only, and that deployment stays local-only for now. With local-only deployment, DEC-001 remains OPEN.

## Decisions

### DEC-007 — Frontend, layout, toolchain, contracts

- **Frontend:** Next.js (App Router), TypeScript strict. The web app holds no business logic, server secrets, or direct database or Stripe-secret access (FE-02). It reaches the Python API through same-origin `/api/v1/*` rewrites.
- **Layout:**
  - `apps/web` — frontend.
  - `apps/api` — Python backend.
  - `packages/contracts` — generated API types.
  - `supabase/migrations` — SQL migrations.
  - `perf/`, `scripts/`, `docs/`, `dev-docs/`.
- **Contracts:** the FastAPI OpenAPI document is the source of truth. TypeScript types are generated from it, and CI fails on drift.
- **Toolchain:** pnpm; uv with Python 3.12; Ruff; mypy (strict); import-linter; pytest; ESLint; Vitest; Playwright.

### DEC-008 — Data access and RLS: option A

- The browser never connects to the database.
- The Python API connects with `DATABASE_URL`. Every transaction runs `SET LOCAL ROLE` to one of two `NOLOGIN` roles: `app_api` for user requests, or `app_worker` for webhooks, synchronization, and maintenance. Neither role owns tables or has `BYPASSRLS`.
- User and tenant context is set transaction-locally with `set_config(..., true)`.
- RLS is enabled and forced on every table.
- Tables live in the `app` schema, which is not exposed to Supabase's client roles.

### DEC-009 — Membership source of truth: option A

- Clerk Organizations owns organizations, memberships, roles, and invitations.
- The `app.organization_memberships` table mirrors them. It is refreshed in three ways:
  1. Signature-verified Clerk webhooks, which trigger a re-fetch rather than being applied blindly.
  2. On demand, when a request targets an organization that is missing from the mirror.
  3. When the mirrored row is older than `MEMBERSHIP_MAX_STALENESS_SECONDS`.
- If Clerk is unreachable and the row is stale, the request fails closed.
- **Pending owner confirmation:** the staleness bound value. The proposed default is 300 s.

### DEC-010 — Billing policy

- The Stripe Customer Portal handles payment methods, invoices, cancellation, and monthly↔yearly switching. The portal configuration lives in the Stripe dashboard and is documented in `docs/integrations.md`.
- `past_due` keeps Pro until Stripe moves the subscription to `unpaid` or `canceled`.
- End-of-period cancellation is the default (portal configuration).
- No trials. `trialing` is not entitled and is logged as an anomaly.
- Single currency, configured by the prices. Tax calculation is disabled by default and is an adopter decision.

### DEC-011 — Rate limiting: option A

- Per-user and global limits use PostgreSQL fixed-window counters (`app.rate_limit_counters`).
- Coarse per-IP limits use Vercel platform rules, configured at deployment time (documented, not in code).
- No Redis.

### DEC-012 — License

- **Apache-2.0.**
- The vulnerability-reporting channel and the Code of Conduct enforcement contact remain OPEN.

## Consequences

- Scaffolding, tenancy, and billing implementation may proceed.
- DEC-001 (Vercel proof), DEC-002 (organization entitlements), DEC-003, DEC-004, DEC-005, and DEC-006 remain open. Their interim rules apply.
- The LLM provider is not selected. The code ships Anthropic and OpenAI adapters plus a deterministic mock, chosen with `LLM_PROVIDER`.
