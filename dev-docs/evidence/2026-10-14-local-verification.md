# Evidence — Local Verification, 2026-10-14

| Field | Value |
|---|---|
| Commit | `9834791` (code); this record was added in the following commit |
| Environment | macOS 26 (Darwin 25.6, arm64); Node 25.8.2; pnpm 9.15.0; Python 3.12.13 via uv 0.11.1; PostgreSQL 17.6 (Docker, local) |
| Integration mode | **mocked** for Clerk (identity), Stripe API, LLM provider, PostHog, Sentry. Real cryptographic verification was exercised for Clerk-format JWTs (generated RSA keys), Stripe webhook signatures (test-only secret), and Svix signatures (test-only secret). |
| Live or sandbox services contacted | **None.** No `.env` existed, and no external account was used. |
| Remote actions | **None.** No push, publication, deployment, or repository-setting change. |

## Results

| Check | Command | Result | Requirements |
|---|---|---|---|
| API lint and format | `uv run ruff check …; ruff format --check …` | PASS | TEST-*, SEC-06 (partial) |
| API import boundaries | `uv run lint-imports` | PASS — 4 contracts kept | ARCH-01, ARCH-02, FE-02 (API side) |
| API strict type check | `uv run mypy` (strict) | PASS — 42 files | — |
| API tests | `pnpm test:api` | PASS — 167 tests | See below |
| Web lint (incl. FE-02 import bans) | `pnpm --filter web lint` | PASS | FE-02, ENV-07 (source rule) |
| Web strict type check | `pnpm --filter web typecheck` | PASS | — |
| Web unit tests | `pnpm test:web` | PASS — 37 tests | FE-03, FE-06, FE-07, FE-10, OBS-03, SEC-14 |
| Contract drift | `pnpm contracts:check` | PASS | DEC-007 contracts |
| Production build | `pnpm build` (placeholder publishable key) | PASS | Gate B build |
| Bundle secret scan | `pnpm check:bundle-secrets` | PASS — no secret-shaped strings. With no `.env` there were no server-only *values* to compare; a planted value was detected in a negative test | ENV-07 |
| E2E, account-free | `pnpm test:e2e` | PASS — 16 (8 checks × desktop and mobile); 16 skipped (sandbox-only) | FE-05, SEC-02, API-02, API-04 |
| Dependency audit | `pnpm security:audit` | PASS — no known vulnerabilities (pnpm audit `--prod`, pip-audit on locked versions) as of this date | SEC-04, SEC-07 |
| License check | `pnpm security:licenses` | PASS — 132 npm and 40 PyPI runtime packages; `tzdata` needs manual review (Windows-only marker; upstream license Apache-2.0) | SEC-10 |
| Secret scan | gitleaks v8.28.0 (Docker), `dir` mode with `.gitleaks.toml` | PASS — 0 findings in project files after one reviewed false positive was annotated | SEC-05, REPO-10 |
| Migrations | `pnpm db:migrate` twice, `db:seed`, `db:reset`; non-local URL refused | PASS | MIG-01, MIG-05, ENV-05 |
| Template init | `init-template` on an isolated copy; dry run, real run, re-run, invalid input | PASS — idempotent; `.env` created from the example only; no original identifiers left in renamed targets | REPO-07 |
| Clean-clone onboarding | `git clone` → `pnpm bootstrap` → tests, lint, typecheck, contract check | PASS — 36 s | REPO-11 |

### API test coverage by requirement (167 tests)

- **Entitlement matrix and summaries:** BILL-01, §9.5. Includes `past_due` grace, end-of-period cancellation, unknown price, and organization contexts.
- **Authorization policy and context invariants:** TEN-02, ADM-01.
- **Configuration safety:**
  - key/mode mismatches;
  - live keys outside production;
  - production rejecting mock and sandbox;
  - no secret echo;
  - empty values treated as unset.

  Requirements: ENV-02 to ENV-05.
- **Clerk token verification:**
  - expired, wrong issuer, wrong or missing `azp`, missing `sub`, not yet valid;
  - wrong key, `alg=none`, HS256 confusion, malformed input;
  - JWKS outage fails closed.

  Requirements: AUTH-01 to AUTH-04, NT-11.
- **RLS directly in SQL:**
  - no context means no rows;
  - personal and organization isolation;
  - cross-tenant insert and delete denied;
  - the API role cannot modify memberships or subscriptions, or read audit and ledgers;
  - no `BYPASSRLS`;
  - RLS forced on every table.

  Requirements: DB-06, DB-07, NT-08.
- **Negative API tests:** NT-01 to NT-12, including:
  - the staleness bound;
  - webhook-driven revocation;
  - fail closed when the directory is down;
  - the in-transaction role re-check;
  - concurrent downgrade serialization;
  - audited denials.
- **Billing:**
  - checkout idempotency and plan allowlist;
  - conflict when already Pro;
  - rate limit;
  - outage maps to `503`;
  - not configured;
  - portal;
  - redirect alone does not grant Pro;
  - state transitions, cancellation and reactivation;
  - forged, replayed, and tampered webhooks;
  - duplicate deliveries processed once;
  - out-of-order convergence;
  - unknown customer goes to reconciliation;
  - provider failure returns `503` and the retry succeeds;
  - 8 concurrent duplicate deliveries;
  - reconciliation dry run and apply.

  Requirements: BILL-*.
- **Platform:**
  - route manifest;
  - unauthenticated probe of every route;
  - problem-details contract and security headers;
  - request-ID sanitization;
  - body limit;
  - sanitized `500`s;
  - bounded cursor pagination;
  - readiness with the database down;
  - analytics outage;
  - LLM outage isolation;
  - LLM per-user and global limits;
  - log and analytics leakage canaries;
  - Clerk webhook resync, out-of-order, organization deletion, forgery, and duplicates;
  - migration checksum tamper detection;
  - non-local target refusal.

  Requirements: TEN-02, API-*, SEC-09, OBS-06, OBS-08, LLM-*, MIG-*.

### Mutation checks (tests can fail)

- Removing the in-transaction role re-check makes the NT-06 test fail.
- Removing the application-level tenant filter does **not** break isolation tests, because RLS alone still enforces isolation. This is expected defense-in-depth behavior.

## Not verified by this record

- Any behavior against real Clerk, Stripe, Supabase (hosted), PostHog, Sentry, or LLM services (DEC-006).
- Browser page rendering, accessibility (axe), and signed-in flows. These need a Clerk development instance; they are sandbox suites `e2e/pages.spec.ts` and `e2e/authenticated.spec.ts`.
- GitHub Actions workflows, including CodeQL. They have never run, because no remote action was authorized.
- Vercel deployment and Python runtime behavior (DEC-001).
- Performance (DEC-003), backup and restore (DEC-005), deletion and retention (DEC-004).
- Query-count (N+1) tests for ARCH-08.

## Limitations

- The clean-clone test reused this machine's pnpm store, uv cache, and running PostgreSQL container. It is not a fresh machine.
- Audit results reflect advisory databases on 2026-10-14 only.
