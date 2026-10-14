# SaaS Template — Engineering Implementation Plan

| | |
|---|---|
| **Document role** | Authoritative engineering implementation specification for this repository |
| **Revision** | 2 — 2026-10-14 |
| **Audience** | AI development agents, software engineers, reviewers |
| **Owner** | Repository owner — approves decisions, scope changes, and every remote action |
| **Priority** | Security, correctness, maintainability, reproducibility |

> **Non-negotiable:** Local commits are permitted. Never push, publish, release, deploy, or otherwise modify remote state without explicit authorization from the owner. Never expose, commit, log, or transmit secrets, credentials, or personal data unnecessarily. Never present unverified behavior as verified.

---

## 0. Using This Specification

### 0.1 Authority and precedence

1. Explicit, current instructions from the owner.
2. This specification, including the open decisions register (§3).
3. Accepted Architecture Decision Records (ADRs) in `dev-docs/adr/`.
4. All other documentation, which must link to this document instead of restating its requirements.

If two sources conflict, stop and ask. Do not pick one silently. Any change that alters a requirement, decision, threat, or operational behavior must update this document in the same change.

### 0.2 Normative language

- **MUST / MUST NOT:** mandatory.
- **SHOULD:** the default; deviating requires a recorded reason.
- **MAY:** optional.
- A **Recommendation** in the register is a proposal awaiting approval. It is not a requirement.

### 0.3 Requirement identifiers

Requirements carry stable identifiers (for example `AUTH-03`). Identifiers are never reused. A removed requirement is marked `RETIRED` in the traceability matrix (§21) together with its approval reference. Tests and evidence records reference these identifiers.

### 0.4 Supporting documents

| Document | Location | Status |
|---|---|---|
| Agent entry point | `AGENTS.md` | Exists. Links here; restates only the critical operating rules. |
| Open decisions register | §3 of this document | Maintained here |
| Threat model | §13.2 of this document | Initial version. If it outgrows this document it moves to `dev-docs/threat-model.md`, and §13.2 becomes a link. |
| Performance test plan | §15.2 of this document | Maintained here |
| Requirements traceability matrix | §21 of this document | Maintained here |
| Architecture Decision Records | `dev-docs/adr/NNNN-short-title.md` | ADR-0001 (approved recommendations for DEC-007 to DEC-012); ADR-0002 (DEC-002 option (b), DEC-009 value, DEC-012 contacts, DEC-003 to DEC-005 adopter-owned); ADR-0003 (modular monolith, request-scoped transactions, outbox, jobs, generator) |
| Evidence records | `dev-docs/evidence/` | `2026-10-14-local-verification.md`, `2026-10-14-diagnostics-perf-local.md`, `2026-10-14-adr-0002-perf07-local.md`, and `2026-10-14-adr-0003-modular-architecture.md` (mocked integrations, local only) |
| Adopter-facing documentation | `README.md`, `docs/architecture.md`, `docs/integrations.md`, `docs/operations.md`, `docs/data-inventory.md`, `perf/README.md`, `perf/run-plan-template.md` | Exists |
| Community files | `LICENSE`, `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, `.github/` | Exist. Contacts are adopter placeholders that `init-template` fills in and `check:placeholders` enforces (DEC-012, ADR-0002) |

---

## 1. Mission and Definition of Done

Build a production-oriented, open-source SaaS starter template. A new team must be able to clone, configure, test, deploy, and extend it without inheriting credentials, infrastructure dependencies, or hidden assumptions from the original project.

The template must prioritize:

- Clear modular boundaries and maintainable code.
- Secure-by-default authentication, authorization, and tenant isolation.
- Reliable individual subscriptions and server-authoritative entitlements.
- Reproducible local development, CI, and deployment.
- Automated functional, security, integration, resilience, and performance testing.
- Explicit operational procedures and verifiable acceptance criteria.

**Definition of done:** every mandatory quality gate (§19) is `PASS`. Each requirement in the traceability matrix (§21) is `PASS` or `RETIRED`, or is `BLOCKED` with the owner's explicit, recorded acceptance. Compilation, a successful happy-path demo, or passing mocked tests alone is insufficient.

---

## 2. Approved Architecture Decisions

These decisions are confirmed. A confirmed technology choice is not evidence that its compatibility, configuration, or production behavior has been verified.

| Area | Approved direction | Verification status |
|---|---|---|
| Frontend | TypeScript with strict type checking | NOT VERIFIED — framework selection in DEC-007 |
| Backend | Python deployed on Vercel | NOT VERIFIED — mandatory proof in DEC-001 |
| Database | Supabase/PostgreSQL | NOT VERIFIED — access model in DEC-008, integration in DEC-006 |
| Authentication | Clerk | NOT VERIFIED — membership model in DEC-009, integration in DEC-006 |
| Billing | Stripe; the individual user owns the subscription | NOT VERIFIED — policy in DEC-010, org scope in DEC-002 |
| Plans | Free and Pro; Pro billed monthly or yearly | NOT VERIFIED |
| Analytics | PostHog | NOT VERIFIED — DEC-006 |
| Error monitoring | Sentry | NOT VERIFIED — DEC-006 |
| LLM integration | Minimal provider-agnostic abstraction with a connectivity check | NOT VERIFIED — provider selection in DEC-006 |
| Tenancy | Individual users and organizations, with strict authorization and tenant isolation | NOT VERIFIED |
| Deployment | Vercel, subject to verified compatibility of the selected architecture | NOT VERIFIED — DEC-001 |
| Performance | Reproducible baseline, then controlled stress testing; quantitative targets approved after measurement | NOT VERIFIED — DEC-003 |
| Git policy | Local commits allowed; no push, publication, release, or remote modification without explicit authorization | In force |
| Data protection | Never expose, commit, log, or transmit secrets, credentials, or personal data unnecessarily | In force |
| Architecture style | Modular monolith: shared kernel plus feature modules with identical layers; request-scoped transactions; transactional outbox; scheduled jobs; module generator (ADR-0003) | Implemented and tested locally. Vercel Cron and after-response dispatch NOT VERIFIED (DEC-001) |

---

## 3. Open Decisions Register

### 3.1 Register rules

- Record every unresolved question here before starting work that depends on it. This applies to any question that affects architecture, security posture, billing behavior, data handling, cost, or external obligations.
- Each entry states:
  - status, owner, and what it blocks;
  - rationale and the questions to answer;
  - impact;
  - required evidence and resolution criteria;
  - an interim rule;
  - optionally, a recommendation.
- Agents **MUST NOT**:
  - resolve entries on the owner's behalf;
  - turn a recommendation into a requirement;
  - implement scope that an entry blocks;
  - invent requirements to fill a gap.
- Agents **MAY** gather evidence, run authorized proofs, and propose options.
- To resolve an entry:
  1. Record the owner's approval (date and the chosen option).
  2. Create an ADR for architectural choices.
  3. Set the status to `RESOLVED` with a link to the ADR or approval.
  4. In the same change, update the affected sections and the traceability matrix.
- Decision statuses are `OPEN`, `RESOLVED`, and `SUPERSEDED`.

### 3.2 Register summary

| ID | Decision | Status | Blocks | Next action |
|---|---|---|---|---|
| DEC-001 | Python backend on Vercel | OPEN — mandatory technical validation | All backend implementation beyond the proof | Owner authorizes the proof deployment (§18, P1) |
| DEC-002 | Individual billing vs. organization entitlements | RESOLVED 2026-10-14 — option (b) ([ADR-0002](adr/0002-organization-entitlements-contacts-adopter-decisions.md)) | — | — |
| DEC-003 | Performance targets | RESOLVED 2026-10-14 — adopter-owned (ADR-0002) | — (the template claims no targets) | Each adopter sets targets from its own preview baseline (`perf/run-plan-template.md`) |
| DEC-004 | Data retention, export, and deletion | RESOLVED 2026-10-14 — adopter-owned (ADR-0002) | Deletion and export features remain out of the template; no deletion-completeness claim | Each adopter completes the checklist in `docs/data-inventory.md` |
| DEC-005 | Backup and recovery | RESOLVED 2026-10-14 — adopter-owned (ADR-0002) | No recovery-readiness claim | Each adopter follows the checklist in `docs/operations.md` (a local restore drill exists) |
| DEC-006 | External integration verification | OPEN until verified | Any "sandbox-tested" or "live-tested" claim; Gate E | Owner provides sandbox keys in `.env`; then run the `/integrations` checks and sandbox e2e |
| DEC-007 | Frontend framework, repository layout, toolchain, and API contracts | RESOLVED 2026-10-14 — recommendation approved ([ADR-0001](adr/0001-approved-recommendations.md)) | — | — |
| DEC-008 | Data access path and RLS enforcement model | RESOLVED 2026-10-14 — option A approved (ADR-0001) | — | Validate on Vercel in DEC-001 |
| DEC-009 | Organization membership source of truth and staleness bound | RESOLVED 2026-10-14 — option A approved (ADR-0001); bound 300 s confirmed (ADR-0002) | — | — |
| DEC-010 | Billing state and plan-change policy | RESOLVED 2026-10-14 — recommendation approved (ADR-0001) | — | — |
| DEC-011 | Rate limiting and shared counters | RESOLVED 2026-10-14 — option A approved (ADR-0001) | — | Configure platform rate-limit rules at deployment time |
| DEC-012 | License and community contacts | RESOLVED 2026-10-14 — Apache-2.0 (ADR-0001); contacts are adopter placeholders (ADR-0002) | — | — |

### 3.3 Decision entries

#### DEC-001 — Python Backend on Vercel

- **Status:** OPEN — mandatory technical validation.
- **Owner:** Repository owner.
- **Blocks:**
  - Backend implementation beyond the proof.
  - Final Python framework selection.
  - Validation of DEC-008.
  - Vercel project topology (DEC-007).
- **Rationale:** Python on Vercel is approved. Serverless Python has constraints that can invalidate the backend design if they are discovered late: raw-body access, connection management, cold starts, bundle size, payload limits, and the concurrency model.
- **Impact if unresolved or wrong:** rework of the backend structure, webhook handling, database connection strategy, or deployment target.
- **Proof scope:**
  - A minimal, disposable proof in `proofs/dec-001/`, isolated from template code.
  - It runs locally first. It is deployed to a Vercel **preview** environment only in an owner-authorized Vercel account, using sandbox or test credentials only.
  - Production deployment is out of scope unless separately authorized.
  - Candidate framework: FastAPI (ASGI) with Pydantic v2. This is a recommendation that the proof must validate.
- **Required evidence.** Record each item as `PASS`, `FAIL`, or `NOT VERIFIED`, with method, environment, date, and commit:
  1. **Runtime and framework:** the supported Python version and the candidate framework serve requests locally and in preview.
  2. **Dependency packaging:**
     - The intended dependency set installs from a lockfile. It includes the framework, PostgreSQL driver, Stripe SDK, a JWT/JWKS verification library, the Sentry SDK, the PostHog SDK, and an HTTP client.
     - The packaged deployment fits the limits. Bundle size is recorded.
  3. **Limits:** documented and observed values for maximum duration, memory, bundle size, and request/response payload size. Observed behavior when each is exceeded: status code, logs, and client experience.
  4. **Cold starts:** latency of the first request after idle and after deploy. Report the distribution and the sample size.
  5. **Concurrency:**
     - Behavior of module-level state and connection pools when one instance serves concurrent requests.
     - No cross-request data leakage.
  6. **Database:**
     - Connection through the Supabase connection pooler.
     - Connection count under concurrency.
     - Driver compatibility with the pooler mode, including prepared statements.
     - Behavior when the database is unreachable.
     - Transaction-local tenant context as required by DEC-008, option A.
  7. **Errors and timeouts:**
     - An unhandled exception returns a sanitized 5xx without stack traces.
     - A function timeout produces an observed status and client behavior.
     - An upstream timeout is handled within bounds.
  8. **Stripe webhooks:**
     - The route is reachable.
     - The handler receives the body byte-for-byte as Stripe signed it.
     - Signature verification passes for genuine sandbox or Stripe CLI events. It fails for an altered body, a wrong secret, and a stale timestamp.
     - A non-2xx response causes Stripe to retry.
     - Duplicate deliveries are observed and handled.
  9. **Environment parity:**
     - Environment-variable scoping across local, preview, and production configuration.
     - Server secrets are absent from frontend output.
     - Differences between environments are recorded.
  10. **Topology:** the frontend and the Python API coexist in the candidate layout from DEC-007 (one project, multiple services, or separate projects). Routing, same-origin/CORS, and preview URL behavior are recorded.
- **Provider-documented starting points** (NOT VERIFIED; confirm against current Vercel documentation during the proof):
  - Python 3.12–3.14 is listed as supported on Fluid Compute.
  - The default maximum function duration is 300 s.
  - Request and response bodies are limited to 4.5 MB.
  - Fluid Compute can serve concurrent requests from one instance, so module-level state must be concurrency-safe.
- **Known risks to examine:**
  - Transaction-mode pooling and driver prepared-statement caching.
  - Connection exhaustion under bursts.
  - Large SDK footprints.
  - Middleware or platform alteration of the raw body.
  - Cold-start latency on webhook delivery.
- **Resolution criteria:**
  - The proof report exists at `dev-docs/evidence/dec-001-vercel-python.md`.
  - Every item is `PASS`, or each `FAIL` has an owner-approved mitigation, or the owner decides on escalated alternatives.
  - An ADR records the framework and topology.
- **Interim rule:**
  - No backend code exists outside the proof.
  - If a required capability fails, stop the affected work and present alternatives.
  - Do not change the deployment provider or add infrastructure without approval.

#### DEC-002 — Individual Billing and Organization Entitlements

- **Status:** RESOLVED 2026-10-14 — option (b): an individual's Pro applies to their own actions in every organization they belong to and is never shared. See [ADR-0002](adr/0002-organization-entitlements-contacts-adopter-decisions.md) and §9.9. The text below is kept as the decision record.
- **Owner:** Repository owner.
- **Blocks:** `BILL-ORG-*` requirements and any feature that is paid within an organization context.
- **Rationale:** the subscription owner is the individual user. Organization membership and isolation are also required, but the relationship between an individual's Pro subscription and organization access is undefined. A wrong guess either leaks paid features to non-payers or locks payers out, and it shapes the schema.
- **Questions:**
  1. Do Pro entitlements apply only to the subscriber's personal context, or can the subscriber use them within organization contexts?
  2. Which organization operations, if any, require paid access?
  3. What happens when a subscriber joins or leaves an organization?
  4. How do membership removal, role changes, cancellation, and account deletion affect access?
  5. Can organization administrators manage members without granting those members the subscriber's paid entitlements?
- **Options for discussion** (no recommendation; this is a business decision):
  - (a) Pro applies only in the subscriber's personal context.
  - (b) The subscriber's Pro applies to the subscriber's own actions in any organization they belong to, and is never shared with other members.
  - (c) Organization-level plans. This would introduce an organization as a billing owner, which conflicts with the approved individual-ownership model and therefore requires an explicit change to §2.
- **Impact:**
  - The entitlement model, and whether organization entitlement records exist.
  - The schema, UI, tests, and Stripe metadata.
- **Required evidence:**
  - Written answers from the owner.
  - An entitlement truth table covering: subscriber vs. non-subscriber × personal vs. organization context × role × membership state × subscription state.
- **Resolution criteria:** the approved truth table is recorded in §9.9 and in an ADR, tests are derived from it, and the traceability matrix is updated.
- **Interim rule:**
  - Personal context: Free or Pro according to the individual subscription.
  - Organization context: authorization is evaluated independently of billing, and entitlement evaluates to "not entitled".
  - No paid feature is exposed in organization contexts.
  - Organization administration works regardless of billing.

#### DEC-003 — Performance Targets

- **Status:** RESOLVED 2026-10-14 — accepted as **adopter-owned** (ADR-0002). The template ships the harness, local reference results, and `perf/run-plan-template.md`, and claims no targets. The text below is kept as the decision record, and each adopter follows its sequence.
- **Owner:** Repository owner.
- **Blocks:** quantitative performance acceptance (Gate D).
- **Rationale:** targets set before measurement are arbitrary. Targets must reflect observed behavior, expected usage, and platform limits.
- **Sequence:**
  1. Establish a reproducible baseline.
  2. Run controlled load, stress, spike, and soak tests.
  3. Identify bottlenecks and saturation points.
  4. Propose quantitative targets based on observed performance and expected usage.
  5. Obtain owner approval of the targets before declaring performance acceptance complete.
- **Required evidence:** reports per §15.2. Each report includes:
  - p50, p95, and p99 latency where applicable;
  - throughput, error rates, and concurrency;
  - resource usage and test duration;
  - environment, dataset, request mix, and limitations.
- **Resolution criteria:** an owner-approved targets table is recorded in §15.4. For each endpoint class it states the metric, percentile, threshold, environment, and workload.
- **Interim rule:**
  - Reports may establish baselines and document observed limits.
  - No report may claim that an undefined target has been met.

#### DEC-004 — Data Retention, Export, and Deletion

- **Status:** RESOLVED 2026-10-14 — accepted as **adopter-owned** (ADR-0002). The template ships the data inventory and an adopter checklist (`docs/data-inventory.md`). It has no deletion or export feature and makes no deletion-completeness claim. The interim rule below stays in force for template code.
- **Owner:** Repository owner.
- **Blocks:** account deletion and data export features, and any claim that deletion is complete.
- **Rationale:** personal data lives in the application database, Clerk, Stripe, PostHog, Sentry, logs, and backups. Each has different deletion capabilities and retention obligations.
- **Questions:**
  1. What does account deletion remove or anonymize in each system?
  2. What happens to an active subscription on deletion?
  3. What happens to organizations where the deleted user is the only owner?
  4. What is the scope and format of data export?
  5. What are the retention periods for audit logs, billing records, webhook ledger entries, logs, and telemetry?
  6. Which records must be retained (legal, tax, fraud), and under what access restrictions?
  7. What is the expected time to complete deletion?
  8. What are the analytics consent requirements, and in which jurisdictions do they apply?
- **Impact:** schema (soft vs. hard delete, anonymization), provider API usage, telemetry identifiers, and documentation.
- **Required evidence:**
  - A data inventory listing each personal-data element, its location, purpose, and retention. Maintained from P3 onward.
  - Verified provider deletion capabilities: documentation plus a sandbox test where possible.
- **Resolution criteria:**
  - The approved policy is recorded in an ADR.
  - The inventory is complete.
  - Deletion and export tests pass against the application database and sandbox providers.
- **Interim rule:**
  - Do not implement irreversible deletion.
  - Never delete payment records.
  - Never claim complete deletion.

#### DEC-005 — Backup and Recovery

- **Status:** RESOLVED 2026-10-14 — accepted as **adopter-owned** (ADR-0002). The template ships the local restore drill and an adopter checklist with a hosted restore-test procedure (`docs/operations.md`). It makes no recovery-readiness claim.
- **Owner:** Repository owner.
- **Blocks:** recovery-readiness claims and Gate E.
- **Questions:**
  - Recovery point objective and recovery time objective.
  - Backup frequency and retention.
  - Acceptable data-loss window.
  - Who may initiate a restore.
  - Which Supabase plan capabilities apply (point-in-time recovery availability depends on the plan).
- **Required evidence:**
  - Documented backup capabilities for the selected service plans.
  - A timed restore into an isolated, non-production project containing synthetic data, with the restored data integrity checked.
- **Resolution criteria:** approved objectives, a documented procedure in `docs/`, and a passing restore test recorded in `dev-docs/evidence/`.
- **Interim rule:** make no recovery-readiness claims.

#### DEC-006 — External Integration Verification

- **Status:** OPEN until verified.
- **Owner:** Repository owner.
- **Blocks:** any classification of an integration as sandbox-tested or live-tested, and Gate E.
- **Integration modes** (also defined in §5.2):
  - `mock`: deterministic in-process fake; no network.
  - `sandbox`: the provider's test mode or sandbox account.
  - `live`: production credentials.
- **Mocked tests never prove connectivity.**

| Provider | Credentials (names only, never values) | Callback / configuration surfaces | Verification procedure |
|---|---|---|---|
| Clerk | Publishable key; secret key; JWT verification key or JWKS URL; webhook signing secret (if DEC-009 uses webhooks) | Sign-in/up redirect URLs; allowed origins / authorized parties; webhook endpoint | Sandbox: development instance with synthetic users; verify token validation, session expiry, organization membership changes |
| Stripe | Secret key (test mode/sandbox); publishable key; per-endpoint, per-environment webhook signing secret; Pro monthly and yearly price IDs | Checkout success/cancel URLs; Customer Portal return URL (if DEC-010 adopts it); webhook endpoint | Sandbox: Stripe CLI and test clocks for checkout, renewal, failure, cancellation, reactivation, retry |
| Supabase | Pooled connection string for the application role; separate migration-role connection string; project URL | Network restrictions; roles; RLS | Sandbox: non-production project; migrations, RLS tests, connection behavior under load |
| PostHog | Project ingest key (public); host | Allowed domains; data retention settings | Sandbox: separate non-production project; event allowlist and redaction checks |
| Sentry | Frontend and backend DSNs; CI-only auth token for source maps | Environments; scrubbing rules; sample rates | Sandbox: separate project or environment; redaction checks |
| LLM provider | API key; model identifier | Spend limits at the provider | Provider not yet selected. Sandbox: a minimal-token connectivity check under an approved spending cap |

- **Required evidence:** for each provider, a dated evidence record stating the mode, environment, procedure, and result. Live verification happens only with explicit authorization.
- **Resolution criteria:** each provider is classified as mocked, sandbox-tested, or live-tested, with evidence. The LLM provider is selected and recorded.
- **Interim rule:** every integration is reported as `mocked`.

#### DEC-007 — Frontend Framework, Repository Layout, Toolchain, and API Contracts

- **Status:** RESOLVED 2026-10-14 — the owner approved the recommendation; see [ADR-0001](adr/0001-approved-recommendations.md). The text below is kept as the decision record.
- **Owner:** Repository owner.
- **Blocks:** scaffolding (P2).
- **Rationale:** strict TypeScript is approved, but the framework is not. That choice determines routing, rendering, Clerk integration, Vercel topology, and where server-side code may run.
- **Options:**
  - **Frontend:**
    - (a) **Next.js (App Router).** First-class Vercel and Clerk support, plus static or server rendering for public pages. Risk: it adds a second server runtime where business logic or secrets could leak; FE-02 mitigates this.
    - (b) **Vite + React SPA.** The strictest boundary (static assets only) and the simplest mental model. Weaker for server-rendered public pages, and auth redirects are client-side.
  - **Layout:**
    - `apps/web` — frontend.
    - `apps/api` — Python backend.
    - `packages/contracts` — generated API types.
    - `supabase/` — migrations and seed.
    - `perf/` — performance scripts.
    - `scripts/` — template init and guarded maintenance commands.
    - `docs/` — adopter-facing documentation.
    - `dev-docs/` — this specification, ADRs, and evidence.
  - **Contracts:** the Python API's OpenAPI document, generated from Pydantic models, is the single source of truth. TypeScript types and client code are generated from it, and CI fails on drift.
  - **Toolchain:**
    - Node.js 24 LTS with pnpm; Python (version per DEC-001) with uv.
    - Python: Ruff for lint, format, and security rules; mypy or Pyright in strict mode.
    - TypeScript: `strict` plus `noUncheckedIndexedAccess`; ESLint with boundary rules; Prettier.
    - Tests: Vitest with Testing Library; pytest; Playwright with axe-core; k6 for performance.
    - Scanning: gitleaks for secrets; pip-audit plus `pnpm audit` or OSV-Scanner for dependencies; Semgrep or CodeQL for static analysis; a license checker.
    - Local PostgreSQL: Supabase CLI, which requires Docker.
- **Recommendation:**
  - Frontend option (a), with FE-02 enforced by lint rules.
  - The layout, contract approach, and toolchain above.
  - Vercel topology as validated by DEC-001.
- **Impact:** every later phase.
- **Required evidence:** owner choice, plus DEC-001 confirmation that the topology works.
- **Resolution criteria:** an ADR is recorded, and §4 and §6 are updated with the final names.
- **Interim rule:** no scaffolding.

#### DEC-008 — Data Access Path and RLS Enforcement Model

- **Status:** RESOLVED 2026-10-14 — the owner approved the recommendation; see [ADR-0001](adr/0001-approved-recommendations.md). The text below is kept as the decision record.
- **Owner:** Repository owner.
- **Blocks:** schema, RLS policies, and repository implementation (P3).
- **Options:**
  - **(A) Backend-only data access.**
    - The browser never connects to Supabase.
    - The Python API connects as a dedicated least-privilege role. The role is not the table owner and has no `BYPASSRLS`.
    - For each transaction, the API sets transaction-local user and tenant context. RLS policies enforce tenant isolation as defense-in-depth beneath application authorization.
    - Migrations run under a separate privileged role that exists only in the migration step.
  - **(B) Direct client access.**
    - The browser queries Supabase directly, using Clerk-issued tokens through Supabase's third-party authentication support. RLS is keyed on token claims.
    - Less backend code, but two enforcement paths and a larger exposed surface.
- **Recommendation:** (A). It keeps a single authoritative enforcement path in the approved Python backend.
- **Impact:** schema, policies, connection strategy, tests, and performance.
- **Required evidence:**
  - Owner choice.
  - DEC-001 item 6 shows that transaction-local context works through the pooler.
  - An RLS test plan per DB-07.
- **Resolution criteria:** an ADR is recorded, and §8 is updated.
- **Interim rule:** no schema or policies beyond the proof.

#### DEC-009 — Organization Membership Source of Truth and Staleness Bound

- **Status:** RESOLVED 2026-10-14 — the owner approved the recommendation; see [ADR-0001](adr/0001-approved-recommendations.md). Staleness bound confirmed at 300 s ([ADR-0002](adr/0002-organization-entitlements-contacts-adopter-decisions.md)). The text below is kept as the decision record.
- **Owner:** Repository owner.
- **Blocks:** tenancy implementation (P3).
- **Options:**
  - **(A) Clerk Organizations is the source of truth** for organizations, memberships, roles, and invitations. The application database keeps a mirror, synchronized by signature-verified Clerk webhooks plus periodic reconciliation.
  - **(B) The application database is the source of truth.** Clerk is used only for authentication, and invitations and email delivery become application responsibilities (possibly new infrastructure).
- **Questions:**
  1. The role set.
  2. The maximum acceptable delay between a membership removal or role change and its enforcement on the next request.
  3. Whether at least one owner per organization is enforced.
  4. Invitation behavior.
- **Recommendation:** (A). The owner must approve a staleness bound. Authorization checks the mirrored membership on every request and never relies on token claims alone.
- **Impact:** schema, sync and reconciliation, the negative tests NT-05 and NT-06, and UI components.
- **Required evidence:** owner choice and staleness bound; DEC-006 sandbox verification of the sync path.
- **Resolution criteria:** an ADR is recorded, the bound is recorded in TEN-07, and NT-05 measures against it.
- **Interim rule:** no tenancy implementation.

#### DEC-010 — Billing State and Plan-Change Policy

- **Status:** RESOLVED 2026-10-14 — the owner approved the recommendation; see [ADR-0001](adr/0001-approved-recommendations.md). The text below is kept as the decision record.
- **Owner:** Repository owner.
- **Blocks:** the billing transitions marked "DEC-010" in §9.5, and plan-change implementation (P4).
- **Questions:**
  1. Does Pro access continue while a subscription is `past_due` during Stripe's retry period?
  2. What proration behavior applies for monthly↔yearly switches, and do switches take effect immediately or at period end?
  3. Which cancellation mode (immediate or end-of-period) does the default UI offer? Both must be supported technically.
  4. Is plan, payment-method, and invoice management done through Stripe's Customer Portal or an in-app UI?
  5. Are trials supported? They are not in the approved scope.
  6. What currency applies, and is tax calculation enabled?
  7. What happens to data or usage above Free limits after a downgrade?
- **Recommendation:**
  - Use the Customer Portal for payment methods, invoices, cancellation, and interval switching.
  - Keep Pro during `past_due` until Stripe moves the subscription to `unpaid` or `canceled`.
  - Make end-of-period cancellation the default.
  - No trials.
  - A single, configurable currency.
  - Tax calculation disabled by default and documented as an adopter decision.
- **Impact:** entitlement matrix, UI copy, tests, and Stripe configuration documentation.
- **Required evidence:** owner policy, then sandbox tests with Stripe test clocks.
- **Resolution criteria:** an ADR is recorded, §9.5 and §9.6 are updated, and transition tests are added.
- **Interim rule:** implement only the matrix rows whose entitlement is unambiguous.

#### DEC-011 — Rate Limiting and Shared Counters

- **Status:** RESOLVED 2026-10-14 — the owner approved the recommendation; see [ADR-0001](adr/0001-approved-recommendations.md). The text below is kept as the decision record.
- **Owner:** Repository owner.
- **Blocks:** rate limiting (SEC-09), LLM budget enforcement (LLM-06), and P5.
- **Rationale:** serverless instances do not share memory, so per-instance counters cannot bound global usage or spend.
- **Options:**
  - **(A)** Platform-level rate-limit rules for coarse per-IP limits (availability depends on the Vercel plan; verify), plus PostgreSQL-backed counters for per-user, per-tenant, and LLM budget limits. No new infrastructure.
  - **(B)** A managed Redis-compatible store. This is new infrastructure and requires approval.
  - **(C)** Per-instance limits only. Insufficient on its own; acceptable only as an additional local concurrency bound.
- **Questions:**
  1. Limits per endpoint class.
  2. LLM budget per user and globally.
  3. Behavior when a limit is reached. The proposal is `429` with `Retry-After`.
- **Recommendation:** (A), revisited after the performance baseline.
- **Required evidence:** owner choice; platform-feature availability verified; database counter overhead measured in P7.
- **Resolution criteria:** an ADR, with limits configured and tested.
- **Interim rule:** per-instance concurrency bounds and request-size limits only. Rate limiting is reported as `BLOCKED`.

#### DEC-012 — License and Community Contacts

- **Status:** RESOLVED 2026-10-14 — license is **Apache-2.0** (ADR-0001). The vulnerability-reporting channel and Code of Conduct enforcement contact are **adopter placeholders** (`{{SECURITY_CONTACT}}`, `{{CONDUCT_CONTACT}}`). `init-template` fills them in, and `check:placeholders` fails in a renamed project that still has them (ADR-0002).
- **Owner:** Repository owner.
- **Blocks:** `LICENSE`, `SECURITY.md`, and `CODE_OF_CONDUCT.md` content; Gate A.
- **Questions:**
  1. The license (for example MIT or Apache-2.0; Apache-2.0 includes an explicit patent grant).
  2. The copyright holder name.
  3. The vulnerability-reporting channel. GitHub private vulnerability reporting is recommended; enabling it is a remote setting that requires authorization.
  4. The Code of Conduct enforcement contact.
- **Required evidence:** owner choice.
- **Resolution criteria:** the files exist with approved content, and no personal contact details are added without consent.
- **Interim rule:** no license file. Gate A remains `BLOCKED`.

---

## 4. System Architecture

### 4.1 Layers and responsibilities

Since ADR-0003, the API layers below apply **inside every feature module** (`app/modules/<name>`), and the shared kernel (`app/kernel`) provides the cross-cutting parts. The mapping:

| Layer below | Module file | Kernel part |
|---|---|---|
| API transport | `routes.py` | `kernel/http` (app factory, error contract, middleware, `AuthDeps`) |
| Application services | `service.py`, `probe.py` | `kernel/application` (rate limits, outbox dispatch, kernel probes) |
| Domain | `domain.py` | kernel core (tenancy, permissions, errors, pagination, events) |
| Ports | `ports.py` | `kernel/ports.py` |
| Infrastructure adapters | `adapters` | `kernel/infrastructure` (database, outbox store, observability, config) |
| Composition root | `module.py` | `app/bootstrap.py` and `app/settings.py` |

`docs/architecture.md` describes the structure in detail.

| Layer | Responsibility | May depend on | MUST NOT |
|---|---|---|---|
| Web presentation | Routes, layouts, components, client-side validation (UX only), session-aware presentation, typed API client | Generated contracts, UI libraries, Clerk frontend SDK, telemetry wrappers | Access the database, hold server secrets, decide entitlements or authorization, call Stripe with secret keys |
| API transport | Routing, request parsing and schema validation, authentication middleware, tenant-context resolution, error mapping, correlation IDs | Application services, contracts | Contain business rules or call vendor SDKs directly |
| Application services | Use cases: authorization checks, orchestration of domain logic and ports, transaction boundaries | Domain, ports | Import HTTP framework types or vendor SDKs |
| Domain | Entities, value objects, authorization policy, entitlement decision, subscription-state mapping, tenant context types | Standard library only | Perform I/O |
| Ports | Interfaces for repositories, identity, billing, analytics, error reporting, LLM, clock, ID generation | Domain types | — |
| Infrastructure adapters | PostgreSQL repositories, Clerk, Stripe, PostHog, Sentry, LLM providers, configuration loading | Ports, vendor SDKs | Contain business rules or leak vendor types past the adapter |
| Composition root | Wires adapters to services according to environment and integration mode | Everything | Be imported by any other layer |
| Contracts | OpenAPI document and generated TypeScript types | — | Be edited by hand |

### 4.2 Architecture requirements

- **ARCH-01** Dependencies point inward: transport → application → domain. Adapters implement ports. Import-boundary rules enforce this in CI.
- **ARCH-02** No circular dependencies, enforced in CI.
- **ARCH-03** Business rules exist once, in the domain layer. These include entitlements, authorization policy, and subscription-state mapping. The frontend renders server-provided decisions and does not re-derive them.
- **ARCH-04** Vendor SDK types and errors do not cross adapter boundaries. Adapters return domain types and normalized errors.
- **ARCH-05** No global mutable state.
  - Configuration is immutable after startup.
  - Per-request state is passed explicitly.
  - Shared per-instance resources, such as connection pools and HTTP clients, MUST be concurrency-safe.
- **ARCH-06** Modules and components SHOULD have a single responsibility. Reviewers flag oversized units (as a guideline, more than ~400 lines or mixed concerns) for splitting.
- **ARCH-07** Every outbound call has an explicit timeout. Retries are bounded and are used only for idempotent operations. Concurrency is bounded (§15.3).
- **ARCH-08** No N+1 queries. List endpoints use a bounded number of queries, verified by query-count tests on key endpoints.
- **ARCH-09** No new infrastructure, service, queue, cache, or distributed component is added without a register entry and owner approval.
- **ARCH-10** Asynchronous processing is introduced only when it is required and DEC-001 shows the platform supports it. Its delivery guarantees, idempotency, and failure recovery are documented.
- **ARCH-11** An abstraction must have a concrete reason, such as a vendor boundary or multiple real implementations (including a test fake). No speculative layers.
- **ARCH-12** Each resolved decision and each significant architectural choice gets a concise ADR covering context, decision, alternatives, and consequences.
- **ARCH-13** Modular monolith (ADR-0003):
  - Every feature module has the same layers.
  - The kernel never imports a module.
  - A module uses another module only through its `api.py` or through kernel contracts (`AuthDeps`, events, probes).
  - Modules are acyclic.
  - Import-linter contracts and architecture tests enforce these rules in CI.
- **ARCH-14** Transactions:
  - Each transaction sets role, timeout, and the full tenant context in one statement.
  - Database-only routes use a request-scoped transaction that commits before the response is sent and rolls back when a block raises.
  - No request transaction is held across an external call (`release()`).
  - Request and system work use separate connection pools.
- **ARCH-15** Domain events use a transactional outbox:
  - an event is recorded in the transaction that makes the change;
  - delivery is at least once, so handlers are idempotent;
  - failures retry with backoff and are dead-lettered after a bounded number of attempts;
  - processed events are pruned after a retention period.
- **ARCH-16** Scheduled jobs:
  - they are registered in code, idempotent, and bounded per run;
  - they run through the CLI or a secret-protected HTTP endpoint;
  - the platform schedule is kept identical to the registry.
- **ARCH-17** New feature modules are generated with `pnpm new:module`. CI proves that a generated module passes every gate.

### 4.3 Protected request lifecycle

1. **Platform:** TLS, platform limits, coarse rate limiting (DEC-011).
2. **Transport:**
   - Assign or propagate a request ID.
   - Enforce the body-size limit and the content type.
   - Parse the request.
3. **Authentication:** verify the Clerk session token (AUTH-01 to AUTH-04). Failure returns `401`.
4. **Tenant context:**
   - Resolve the target context (personal, or an organization identified in the request path).
   - Verify membership and role against the authoritative state (TEN-07).
5. **Validation:** validate input against its schema. Write operations reject unknown fields.
6. **Authorization:**
   - The service calls the domain policy `authorize(actor, action, resource)`, which denies by default.
   - Paid operations also call the entitlement decision.
7. **Data access:** data is accessed in a transaction that carries the tenant context (DEC-008). Repository methods require that context.
8. **Response:**
   - The response conforms to the contract.
   - Errors are mapped to the error contract (API-02).
   - Telemetry is emitted with redaction.

### 4.4 API conventions

- **API-01** All endpoints live under a versioned base path, `/api/v1`.
- **API-02** Errors use RFC 9457 problem details (`application/problem+json`).
  - Fields: `type`, `title`, `status`, a stable machine-readable `code`, and `request_id`.
  - Responses never include stack traces, SQL, internal identifiers of other tenants, or raw vendor messages.
- **API-03** List endpoints use cursor pagination with a server-enforced maximum page size.
- **API-04** Authentication uses `Authorization: Bearer <Clerk session token>`.
  - The API does not accept cookie authentication, which removes the CSRF surface for API calls.
  - CORS allows only configured origins.
- **API-05** Client-initiated mutations with external side effects accept an idempotency key. Checkout session creation is one example.
- **API-06** Health endpoints:
  - `GET /api/v1/health` is a liveness check with no dependency calls.
  - `GET /api/v1/ready` checks dependencies with bounded timeouts and never reveals configuration or secrets.
  - Semantics are adjusted to the runtime model established by DEC-001.

---

## 5. Environments, Configuration, and Integration Modes

### 5.1 Environments

| Environment | Purpose | Data | Credentials | Who may target it |
|---|---|---|---|---|
| `local` | Development | Synthetic seed in local PostgreSQL | The adopter's own sandbox keys, or mocks | Developer |
| `test` | Automated tests, locally and in CI | Ephemeral database, deterministic fixtures | Mocks; sandbox keys only in explicitly marked, trusted jobs | CI, developer |
| `preview` | Vercel preview deployments | Isolated non-production database, synthetic data | Preview-scoped sandbox keys | Owner-authorized only |
| `production` | Live service | Real user data | Production keys, available only to the production environment | Owner-authorized deploys only |

### 5.2 Configuration requirements

- **ENV-01** Each environment uses separate credentials. Production credentials never reach `local`, `test`, pull-request CI, or `preview`.
- **ENV-02** Each provider has an explicit integration mode: `mock`, `sandbox`, or `live`.
  - `mock` is the default for automated tests.
  - `live` is accepted only when `APP_ENV=production`; startup fails otherwise.
- **ENV-03** Each application has one centralized, typed configuration module.
  - It validates at startup which settings are required or optional per environment, and their formats.
  - It fails fast with a message that names the variable but never prints its value.
  - The frontend module separates public variables from server-only variables.
- **ENV-04** Key/mode consistency checks run at startup. For example, a live Stripe key outside production, or a test key in production live mode, refuses to start.
- **ENV-05** Production-targeting safeguards:
  - Development and test commands require `APP_ENV` to be `local` or `test`.
  - Database URLs must match an allowlist of local or designated test targets.
  - Destructive commands (reset, seed, migration rollback) also require an explicit confirmation flag and refuse to run otherwise.
- **ENV-06** `.env.example` lists every variable with its purpose, the environments where it applies, whether it is required or optional, and a safe placeholder.
  - Real `.env*` files are git-ignored.
  - Secret scanning runs in pre-commit and in CI.
- **ENV-07** A CI check scans built frontend assets for server-only variable names and secret patterns.
- **ENV-08** The application contains **no** test-only authentication path. Tests replace adapters through the composition root (`build_container(..., verifier=...)`) from test code only. If a test-only path is ever added, it:
  - is excluded from production builds;
  - refuses to activate unless `APP_ENV=test`;
  - is covered by a CI check that asserts it is absent from production artifacts.

---

## 6. Template Bootstrap and Repository Readiness

- **REPO-01** `README.md` covers:
  - purpose and architecture overview;
  - prerequisites with versions;
  - setup from a clean clone;
  - external service configuration, including callback URLs;
  - commands; testing; deployment; troubleshooting;
  - a link to this specification.
- **REPO-02** `.env.example` satisfies ENV-06 and contains safe placeholders only.
- **REPO-03** One documented set of commands is used identically locally and in CI: setup, dev, lint, format, typecheck, unit, integration, e2e, build, `db:migrate`, `db:seed`, `db:reset`, and the performance scripts.
- **REPO-04** Lockfiles are committed, and installs use frozen lockfiles. Runtime versions are pinned in version files.
- **REPO-05** Database migrations are version-controlled (§8.3). Seed data is deterministic and synthetic.
- **REPO-06** A safe development reset procedure exists. It works only on local targets and is guarded by ENV-05.
- **REPO-07** A template initialization script:
  - replaces project-specific identifiers (name, package names, display strings);
  - generates a local `.env` from `.env.example` without copying any credential;
  - is idempotent.

  CI runs it in a temporary copy and asserts that no original identifiers remain.
- **REPO-08** A clean clone runs the full automated suite in mock mode without any external account. Running the app locally with real authentication requires only the adopter's own provider accounts, and the documentation says so.
- **REPO-09** Community and repository policy files:
  - `LICENSE` (DEC-012), `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`;
  - issue and pull request templates;
  - dependency-update configuration;
  - optionally `CODEOWNERS`.

  Recommended GitHub settings (branch protection, required checks, secret scanning push protection, private vulnerability reporting) are documented. Applying them is a remote change that requires authorization.
- **REPO-10** The repository contains no credentials, no inherited identifiers that grant access (project references, organization IDs, DSNs, webhook endpoints), no production data, and no hidden manual setup steps.
- **REPO-11** Clean-clone onboarding test:
  - Run it in an isolated environment, such as a fresh container or VM, or a fresh directory with clean caches.
  - Follow only the README.
  - Record the steps, the duration, and any deviation as Gate A evidence.
- **REPO-12** `.gitignore` excludes dependencies, build output, caches, environment files, keys, local tool state, and raw test and performance artifacts.

---

## 7. Authentication, Authorization, and Tenancy

Clerk provides authentication. It does not replace application authorization.

### 7.1 Authentication

- **AUTH-01** Every protected request verifies the Clerk session token server-side:
  - the signature, using the configured public key or a cached JWKS;
  - `exp`, plus `nbf` or `iat` within a bounded clock skew;
  - `iss` matches the configured issuer;
  - `azp` is among the configured authorized origins;
  - `aud` matches, where configured.
- **AUTH-02** The accepted signing algorithm is pinned. `none` and algorithm-confusion attempts are rejected.
- **AUTH-03** A missing, invalid, or expired token returns `401` with the error contract. The response does not reveal which check failed.
- **AUTH-04** Verification keys are cached with a bounded lifetime and refreshed when an unknown key ID appears. If keys cannot be retrieved and no valid cached key exists, verification fails closed.
- **AUTH-05** The internal user is resolved from the verified subject claim. Client-supplied user identifiers never establish identity.
- **AUTH-06** The frontend handles expired sessions:
  - It attempts a silent refresh through Clerk.
  - If that fails, it redirects to sign-in.
  - The return path is relative only, which prevents open redirects.

### 7.2 Authorization and tenancy

- **TEN-01** The tenant context is either personal (the user) or an organization. It is derived from the request target and verified server-side.
- **TEN-02** Every protected backend operation invokes an explicit, deny-by-default authorization policy. A route-manifest test fails if any route lacks a declared authorization requirement.
- **TEN-03** Every tenant-owned row carries a tenant key. Repository methods for tenant data require a tenant context argument, and unscoped queries against tenant data are prohibited.
- **TEN-04** Database-level row-level security is enabled and forced on all tenant tables (DEC-008), with automated tests (DB-07).
- **TEN-05** Responses do not reveal resource existence:
  - A resource outside the actor's tenants returns `404`.
  - A resource inside the actor's tenant, accessed with an insufficient role, returns `403`.
- **TEN-06** Client-provided user IDs, roles, tenant IDs, and subscription claims are untrusted. A tenant ID from the client only selects which context to verify.
- **TEN-07** Membership removals and role changes are enforced within the owner-approved staleness bound of 300 s (DEC-009, ADR-0002).
  - Authorization reads membership and role from the authoritative store, never from token claims alone.
  - A privileged write re-checks the role inside the same transaction, so a concurrent downgrade cannot be bypassed.
- **TEN-08** Organization membership and individual subscription ownership are separate entities. Entitlement derives only from the actor's own subscription. Membership decides only whether an organization context exists (DEC-002 option (b), §9.9).

### 7.3 Administrative operations

- **ADM-01** Administrative operations (organization administration, and any operator tooling) require an explicit role check. Each operation writes an audit record containing the actor, action, target, timestamp, request ID, and outcome, with no secrets or request bodies.
- **ADM-02** The template contains no global "super-admin" bypass. Any operator or support capability requires a register entry and approval.

### 7.4 Required negative security tests

| ID | Scenario | Expected result |
|---|---|---|
| NT-01 | User A reads User B's personal resource by ID | `404`; no data returned |
| NT-02 | User A modifies or deletes User B's resource | `404`; resource unchanged |
| NT-03 | Non-member accesses an organization's resources (item, list, search, filter parameters) | `404`; no data |
| NT-04 | Member of organization X supplies organization Y's ID in path, header, query, or body | Denied; no cross-tenant data |
| NT-05 | Removed member continues with a still-valid session token | Denied within the approved staleness bound (DEC-009) |
| NT-06 | Role downgraded while a privileged request runs concurrently | No privileged write commits after the downgrade commits |
| NT-07 | Non-admin attempts an administrative action | `403`; no change; denial audited |
| NT-08 | Cross-tenant access through alternate paths: bulk endpoints, exports, webhook-triggered processing, reconciliation jobs, and direct queries under the application role without context | Denied; RLS returns zero rows without context |
| NT-09 | Client sends `plan`, `role`, `org_id`, or `subscription_status` fields to influence decisions | Ignored or rejected; decision unchanged |
| NT-10 | Entitlement manipulation: forged success redirect, replayed checkout session ID, client-asserted subscription state | No Pro access |
| NT-11 | Forged, expired, wrong-issuer, wrong-authorized-party, and `alg=none` tokens | `401` |
| NT-12 | A non-subscribing member uses a Pro-gated feature in an organization where another member is a subscriber | `403`; Pro is never shared (DEC-002 option (b)). The full truth table is in §9.9 |

---

## 8. Data Model, Database, and Migrations

### 8.1 Logical data model (proposed; final after DEC-008, DEC-009, and DEC-010)

| Entity | Purpose | Integrity requirements |
|---|---|---|
| `users` | Internal user mapped to the Clerk user | Unique Clerk user ID |
| `organizations` | Organization tenant | Unique external organization ID (if DEC-009 option A) |
| `organization_memberships` | User ↔ organization with role | Unique (organization, user); role check constraint; foreign keys |
| `billing_customers` | User ↔ Stripe customer | Unique user; unique Stripe customer ID |
| `subscriptions` | Synchronized Stripe subscription state for one user | Unique Stripe subscription ID; status check constraint; price, interval, current period end, cancel-at-period-end, last-synchronized marker |
| `billing_events` | Webhook ledger for idempotency | Primary key is the Stripe event ID; type; object ID; received/processed timestamps; processing status. No full payload stored by default (DEC-004) |
| `audit_log` | Administrative and security-relevant actions | Append-only for the application role |
| Example tenant resource | Demonstrates tenant scoping, RLS, and pagination | Tenant key; composite index on tenant key and sort key |

### 8.2 Database requirements

- **DB-01** Integrity is enforced in the database through foreign keys, unique constraints, check constraints, and `NOT NULL`, not only in application code.
- **DB-02** Tenant-scoped composite indexes support each list or query pattern. The query plans of key queries are reviewed.
- **DB-03** Multi-statement changes run in transactions. Webhook processing is atomic per event.
- **DB-04** Every query is bounded, through pagination and limits. No unbounded list queries.
- **DB-05** Connection management follows DEC-001 findings: pooled connections, small per-instance pools, connect and statement timeouts, and driver configuration that matches the pooler mode.
- **DB-06** Row-level security:
  - RLS is enabled and forced on every tenant table.
  - The application role is not the table owner and lacks `BYPASSRLS`.
  - Policies deny access when the tenant context is unset.
- **DB-07** Automated RLS tests use at least two synthetic users and two synthetic organizations. For every policy they cover a permitted case, a denied case, missing context, and wrong context.
- **DB-08** Each test run or worker uses an isolated, ephemeral test database or schema that is dropped afterwards. Test tooling refuses non-test targets (ENV-05).
- **DB-09** Fixtures are deterministic: fixed identifiers and timestamps, a seeded random generator, and reserved example domains. They contain no real personal data.

### 8.3 Migration safety

- **MIG-01** Migrations are ordered, version-controlled, and immutable once merged. Shared environments only roll forward. Down migrations are optional and local-only.
- **MIG-02** A destructive change requires:
  - explicit review;
  - an expand → migrate → contract plan where overlapping application versions are possible;
  - verified backups;
  - owner authorization before it runs against any shared environment.

  Destructive changes include drops, renames, type narrowing, and data rewrites.
- **MIG-03** CI applies all migrations to an empty database, runs schema linting, verifies that generated types and contracts are current, and runs the RLS tests.
- **MIG-04** A dedicated migration role runs migrations in a controlled deployment step. The application never runs migrations at request time.
- **MIG-05** No agent or automated process runs migrations or data deletion against non-local databases without owner authorization.

### 8.4 Backup and restore

- **DB-10** Backup and restore procedures are documented, and restoration is tested in an isolated environment with synthetic data. The template tests restoration locally (`pnpm db:restore-drill`). Recovery objectives and the hosted restore test are adopter-owned (DEC-005, ADR-0002; checklist in `docs/operations.md`).

---

## 9. Billing and Entitlements

### 9.1 Layers of truth

| Layer | Owner | Rule |
|---|---|---|
| 1. Stripe payment and subscription state | Stripe | The external source of truth for payment state |
| 2. Synchronized billing records | Application database | Updated only from verified Stripe data (webhooks, reconciliation) |
| 3. Entitlement decision | Domain function | Pure function of user, context, billing records, and time |
| 4. Plan and billing presentation | Frontend | Renders server-provided state; decides nothing |

- **BILL-01** There is exactly one entitlement implementation: `decide(user, context, billing_snapshot, now)`. It returns the plan and the feature entitlements, and is unit-tested against the matrix in §9.5.
- **BILL-02** Pro access derives only from synchronized billing records that originate in verified Stripe data. It never derives from a client redirect, frontend state, or a client-supplied subscription status.

### 9.2 Configuration

- **BILL-03** The Pro monthly and Pro yearly price IDs come from server configuration, per environment.
  - Clients request a plan key (`pro_monthly` or `pro_yearly`), never a price ID.
  - The server validates the plan key against an allowlist.
  - Sandbox integration tests verify that the configured prices exist and match the key mode.

### 9.3 Checkout and association

- **BILL-04** Each user has one Stripe customer, created server-side and stored under unique constraints. Stripe metadata carries the internal user ID only, with no additional personal data.
- **BILL-05** The server creates Checkout Sessions for the authenticated user only.
  - Sessions carry the internal user reference and that user's customer.
  - Success and cancel URLs are built from the configured application base URL, never from client input.
- **BILL-06** The checkout success page shows a "confirming" state and polls the billing-status endpoint. Redirect parameters never grant entitlement.
- **BILL-07** Checkout is refused when the user already has an entitled subscription. The user is directed to plan management instead.

### 9.4 Webhook processing

- **BILL-08** The handler reads the raw request bytes and verifies the `Stripe-Signature` header before parsing.
  - It uses the environment-specific signing secret and a timestamp tolerance.
  - Failure returns `400`, processes nothing, and logs without the body.
- **BILL-09** Idempotency: the event ID is inserted into `billing_events` under a unique constraint. An already-processed event returns `2xx` without side effects.
- **BILL-10** Ordering:
  - A relevant event triggers synchronization and is not applied blindly.
  - The handler retrieves the current Stripe object through the billing adapter and upserts it in a transaction, under a row lock per customer or subscription.
  - State never regresses because of an older payload.
  - An alternative ordering guard requires an ADR.
- **BILL-11** The handler responds `2xx` only after commit.
  - Transient failures return `5xx` so that Stripe retries.
  - Events that cannot be associated with a user are recorded as needing reconciliation and acknowledged, to prevent unbounded retries.
- **BILL-12** The handled event set is documented and verified against current Stripe documentation during implementation. At minimum it covers checkout completion, subscription create/update/delete, and invoice paid/payment failed. Unhandled event types are acknowledged and counted.
- **BILL-13** Webhook processing completes well within platform and Stripe timeouts, measured in DEC-001 and P7.
- **BILL-14** Concurrent and duplicate deliveries for the same subscription produce the correct final state. A parallel-delivery test proves this.

### 9.5 Subscription state → entitlement matrix

| Stripe state | Meaning | Entitlement | Notes |
|---|---|---|---|
| No subscription | Free user | Free | Default |
| `incomplete` | Initial payment not yet confirmed | Free | UI shows "processing" or "action required" |
| `incomplete_expired` | Initial payment never completed | Free | Terminal |
| `active` | Paid and current | Pro | — |
| `active` + `cancel_at_period_end` | Cancellation scheduled | Pro until `current_period_end` | UI shows "cancels on <date>"; reactivation allowed (BILL-17) |
| `trialing` | Trial period | **DEC-010** | Trials are out of approved scope. Interim: not entitled, and the anomaly is logged |
| `past_due` | Renewal payment failed; Stripe retrying | **DEC-010** | Implementation blocked until policy approval; UI prompts for a payment-method update |
| `unpaid` | Retries exhausted; subscription retained | Free | UI prompts for payment |
| `canceled` | Ended | Free | Re-subscribe through a new checkout |
| `paused` | Paused (for example, a trial ended without a payment method) | Free | — |
| Pending update (`pending_update` present) | A change awaits payment confirmation | Entitlement follows the current, not the pending, items | Stripe has no "pending" subscription status; "pending" refers to pending updates |

### 9.6 Plan changes, cancellation, and reactivation

- **BILL-15** Free → Pro goes through Checkout. Monthly ↔ yearly switching follows DEC-010 (Customer Portal or API), including proration and effective dates. Entitlement always follows synchronized state.
- **BILL-16** The billing adapter supports both immediate and end-of-period cancellation.
  - End-of-period: Pro continues until `current_period_end`.
  - Immediate: Free after synchronization.
- **BILL-17** Reactivation:
  - Before period end, the scheduled cancellation is removed.
  - After a subscription is `canceled`, the user goes through a new checkout.
- **BILL-18** Renewal failure surfaces a payment-action-required state with a path to update the payment method. Recovery happens on a successful payment event.

### 9.7 Reconciliation

- **BILL-19** A reconciliation command compares billing records with Stripe for one user, or for all flagged or recently changed records.
  - It is idempotent.
  - It defaults to dry-run and reports divergences.
  - Applying corrections is explicit.
  - Scheduling it (for example with a platform cron) is part of DEC-001 verification and requires approval.

### 9.8 Presentation

- **BILL-20** A single billing-status endpoint feeds the paywall, settings, and navigation. The states are: Free, Pro (renews on <date>), Pro (cancels on <date>), payment issue, and processing.
- **BILL-21** Pro-gated APIs return `403` with `code: ENTITLEMENT_REQUIRED`, which the frontend maps to the dedicated paywall. The paywall and status presentation stay consistent across all surfaces.

### 9.9 Organization context (DEC-002)

- **BILL-ORG-01** *(Superseded by BILL-ORG-02 on 2026-10-14.)* Interim rule until DEC-002 was resolved: organization contexts were not entitled.
- **BILL-ORG-02** DEC-002 option (b) ([ADR-0002](adr/0002-organization-entitlements-contacts-adopter-decisions.md)). An individual's Pro applies to that individual's own actions in every organization they currently belong to. It is never shared with other members, and roles neither grant nor remove it. Tested in `apps/api/tests/integration/test_org_entitlements.py`.

  | Actor | Context | Role | Membership | Actor's subscription (§9.5) | Pro-gated feature | Entitlement |
  |---|---|---|---|---|---|---|
  | Subscriber | Personal | — | — | Entitling | Allowed | Pro |
  | Subscriber | Organization | Admin or member | Current | Entitling | Allowed | Pro |
  | Subscriber | Organization | Admin or member | Current | Not entitling | `403 ENTITLEMENT_REQUIRED` | Free |
  | Non-subscriber | Personal or organization | Any | Current | None | `403 ENTITLEMENT_REQUIRED` | Free, even if other members are Pro |
  | Anyone | Organization | — | Not a member, or removed (enforced within TEN-07) | Any | `404` | — |

  - Billing management (checkout, Customer Portal, status) exists only in the personal context.
  - Organization administration is independent of billing: a Free admin keeps admin rights, and Pro grants none.
  - Cancellation ends Pro in organizations at the same moment as in the personal context.

### 9.10 Billing verification

- One automated test per supported row in §9.5 and per transition in §9.6.
- Webhook forgery, replay, duplicate, out-of-order, and concurrent-delivery tests.
- Sandbox integration tests use the Stripe CLI and test clocks for renewal, renewal failure, recovery, cancellation, and reactivation. They require DEC-006 sandbox authorization.

---

## 10. LLM Integration

- **LLM-01** A minimal provider-agnostic port exposes a connectivity check that returns a normalized result: status, provider, model, latency, and error code. Adding generation features requires new, approved requirements.
- **LLM-02** Provider SDKs stay behind adapters. Errors are normalized to `timeout`, `rate_limited`, `auth_failed`, `unavailable`, `invalid_request`, and `budget_exceeded`.
- **LLM-03** Every call has a timeout, and retries are bounded.
  - Retries happen only when the request was provably not accepted by the provider, such as a connection failure before sending, or a `429`/`503` with retry guidance, unless the provider supports idempotency keys.
  - Retries must not multiply billable requests.
- **LLM-04** Input size and output token limits are enforced server-side before any call.
- **LLM-05** Per-user and global rate limits apply (DEC-011), plus a per-instance concurrency bound.
- **LLM-06** Cost protection:
  - Real providers are disabled by default (`mock` mode).
  - The connectivity check is authenticated, rate-limited, and uses minimal tokens.
  - A global spending budget is enforced (DEC-011).
  - Provider-side spending limits are documented.
- **LLM-07** Prompts, responses, credentials, and personal data are not logged. Only metadata is recorded.
- **LLM-08** A deterministic mock adapter is used for automated tests.
- **LLM-09** Provider unavailability affects only explicitly dependent features. Readiness does not fail because of the LLM provider.
- **LLM-10** Configuration for enabling a real provider is documented. The provider choice is made in DEC-006.

---

## 11. Observability and Privacy

- **OBS-01** One telemetry module per application initializes the PostHog and Sentry SDKs. It is environment-aware:
  - development: disabled or local;
  - preview: non-production projects;
  - production: production projects.
- **OBS-02** Sentry configuration:
  - Personal-data sending is disabled by default.
  - A pre-send hook scrubs authorization headers, cookies, webhook signature headers, tokens in URLs, and request bodies.
  - Request bodies are never captured.
  - Breadcrumbs are filtered.
  - Sample rates are set per environment, and release and environment are tagged.
- **OBS-03** PostHog configuration:
  - Events and properties come from a typed allowlist.
  - Input autocapture is disabled or masked.
  - Session recording is disabled by default; enabling it requires approval.
  - Users are identified by internal ID, never by email.
  - Consent behavior is an adopter decision (DEC-004, ADR-0002; `docs/data-inventory.md` checklist item 8).
- **OBS-04** Backend logs are structured. They carry a request ID and pseudonymous internal user and tenant IDs, and never secrets, tokens, request bodies, or prompts.
- **OBS-05** A correlation ID is returned in responses and attached to error reports.
- **OBS-06** Telemetry is non-blocking: bounded buffers, timeouts, and graceful degradation. Authentication, billing, and authorization never depend on analytics or monitoring availability, as proven by tests with telemetry endpoints unavailable.
- **OBS-07** Telemetry volume is bounded through sampling, client-event rate limits, and error de-duplication.
- **OBS-08** Leakage tests:
  - Synthetic canary values are injected through requests and errors: fake keys, fake tokens, fake email addresses, prompt text.
  - Logs and mocked telemetry transports are captured.
  - The test asserts that the canaries are absent.
- **OBS-09** Telemetry retention and deletion behavior is documented. Retention follows each provider's project settings, and adopters choose the periods (DEC-004, ADR-0002).

---

## 12. Frontend Quality and Accessibility

- **FE-01** Layouts are responsive and markup is semantic. The proposed conformance target for template-provided UI is WCAG 2.2 Level AA. Automated checks cannot prove conformance, so a manual keyboard and screen-reader pass is required.
- **FE-02** The frontend holds no business logic, server secrets, or direct database or Stripe-secret access. If server-rendering code exists (DEC-007), it only calls the Python API. Lint rules enforce this.
- **FE-03** Loading, empty, success, validation, and error states are consistent and come from shared components.
- **FE-04** Expired authentication and session failures are handled per AUTH-06, without data loss where practical (for example, by preserving form state).
- **FE-05** Protected-route presentation complements, and never replaces, backend authorization.
- **FE-06** A dedicated paywall exists, driven by BILL-20 and BILL-21.
- **FE-07** A proper 404 page offers recoverable navigation.
- **FE-08** Error boundaries, or the framework's equivalent, isolate failures per route or region.
- **FE-09** Keyboard navigation works throughout. Focus indicators are visible, and every form control has an accessible label.
- **FE-10** One API client maps problem-details errors to consistent user-facing messages and recovery actions.
- **FE-11** Verification:
  - component tests;
  - end-to-end tests of critical flows (§14);
  - mobile and desktop viewport checks;
  - automated accessibility scans of key pages;
  - a manual accessibility pass.
- **FE-12** Bundle size and Core Web Vitals are measured. Budgets are adopter-owned (DEC-003, ADR-0002; `perf/run-plan-template.md`).
- **FE-13** Frontend code is organized by feature:
  - `src/app` holds routes, `src/features/<feature>` exposes a public `index.ts`, `src/ui` holds shared presentation, and `src/lib` holds infrastructure.
  - Lint rules forbid deep cross-feature imports and imports from upper layers.
- **FE-14** The API client is generated from the OpenAPI contract, so paths, parameters, and bodies are checked at compile time.

---

## 13. Security and Supply Chain

### 13.1 Controls

- **SEC-01** Input:
  - Every external input is schema-validated server-side.
  - Output is encoded by the framework.
  - Raw HTML injection is prohibited unless sanitized and reviewed.
  - Outbound requests never use user-controlled URLs.
- **SEC-02** Security headers are set: Content Security Policy (allowing only the required provider origins), HSTS, `X-Content-Type-Options`, `Referrer-Policy`, and frame-ancestors restrictions.
- **SEC-03** Secret management:
  - Secrets live only in environment configuration.
  - Each provider has a documented rotation procedure.
  - Any suspected exposure triggers immediate rotation.
- **SEC-04** Dependency vulnerability scanning runs in CI, and dependency updates are controlled.
- **SEC-05** Secret scanning runs in pre-commit and in CI. Push protection is recommended; enabling it is a remote setting.
- **SEC-06** Static analysis and language-specific security rules run in CI.
- **SEC-07** Dependency installation is reproducible from committed lockfiles.
- **SEC-08** Errors are handled safely (API-02). Logs never contain secrets or sensitive data.
- **SEC-09** Rate limiting (DEC-011) and resource-exhaustion controls (§15.3) are in place: payload limits, page limits, timeouts, and concurrency bounds.
- **SEC-10** Dependency licenses are checked against an allowlist that is compatible with the license chosen in DEC-012.
- **SEC-11** `SECURITY.md` documents vulnerability reporting, supported versions, and remediation expectations.
- **SEC-12** Database migrations and CI workflow changes require explicit review, for example through `CODEOWNERS` once a remote exists.
- **SEC-13** Every inbound webhook (Stripe, and Clerk if used) is signature-verified on the raw body and replay-protected.
- **SEC-14** Redirect targets are validated as relative or allowlisted.
- **SEC-15** Security testing uses isolated environments and synthetic data only. No testing is performed against external systems without authorization.

A successful scan is evidence only of the checks it performed. It is never a guarantee that the application is free of vulnerabilities.

### 13.2 Threat model (initial)

**Assets:**

- user and organization data;
- authentication tokens;
- provider secrets;
- billing state and entitlements;
- LLM spend;
- telemetry data;
- the CI pipeline and the dependency supply chain.

**Trust boundaries:**

1. Browser ↔ API.
2. API ↔ database.
3. API ↔ providers (Clerk, Stripe, PostHog, Sentry, LLM).
4. Providers → webhooks.
5. Developers and CI ↔ repository and deployments.
6. Environment boundaries (local / test / preview / production).

| ID | Threat | Boundary | Controls | Verification |
|---|---|---|---|---|
| T-01 | Forged, expired, or mis-issued token accepted | 1 | AUTH-01 to AUTH-04 | NT-11 |
| T-02 | Cross-user access by guessing or substituting an ID | 1, 2 | TEN-02, TEN-03, TEN-05, DB-06 | NT-01, NT-02 |
| T-03 | Cross-tenant access, including alternate code paths | 1, 2 | TEN-01 to TEN-06, DB-06 | NT-03, NT-04, NT-08, DB-07 |
| T-04 | Stale membership after removal or downgrade | 1, 3 | TEN-07, DEC-009 | NT-05, NT-06 |
| T-05 | Privilege escalation to administrative actions | 1 | ADM-01, ADM-02, TEN-02 | NT-07 |
| T-06 | Entitlement manipulation by the client | 1 | BILL-01, BILL-02, BILL-06, TEN-06 | NT-09, NT-10 |
| T-07 | Forged webhook | 4 | BILL-08, SEC-13 | Webhook forgery tests |
| T-08 | Replayed, duplicate, or out-of-order webhook corrupts state | 4 | BILL-09 to BILL-11, BILL-14 | Replay, ordering, and concurrency tests |
| T-09 | Price or plan tampering at checkout | 1 | BILL-03, BILL-05, BILL-07 | Checkout security tests |
| T-10 | Secret leakage to client bundles, logs, telemetry, or the repository | 1, 5 | ENV-03, ENV-06, ENV-07, SEC-05, OBS-02, OBS-04 | Bundle scan, secret scan, OBS-08 |
| T-11 | Personal data leakage into telemetry | 3 | OBS-02, OBS-03, OBS-08 | OBS-08 |
| T-12 | Injection (SQL, script) | 1, 2 | SEC-01, parameterized queries | SAST, unsafe-input tests |
| T-13 | Resource exhaustion (payload size, request rate, unbounded queries) | 1, 2 | SEC-09, DB-04, §15.3 | Resilience and stress tests |
| T-14 | LLM cost abuse | 3 | LLM-03 to LLM-06 | LLM budget and rate tests |
| T-15 | Malicious or vulnerable dependency | 5 | SEC-04, SEC-07, SEC-10 | Dependency scan, license check |
| T-16 | CI secret exfiltration through untrusted pull requests | 5 | CI-SEC-01 to CI-SEC-04 | Workflow review checklist |
| T-17 | Accidental production targeting or destructive migration | 2, 6 | ENV-01, ENV-05, MIG-02, MIG-05 | Guard tests |
| T-18 | Test-only auth path reaches production | 5, 6 | ENV-08 | Production artifact check |
| T-19 | Open redirect through return or checkout URLs | 1 | AUTH-06, BILL-05, SEC-14 | Redirect tests |
| T-20 | Information disclosure through errors | 1 | API-02, SEC-08 | Error contract tests |

Update this model whenever an endpoint, integration, data category, trust boundary, or deployment component is added.

---

## 14. Testing Strategy

| Layer | Scope | Environment | Integration mode | CI |
|---|---|---|---|---|
| Unit | Domain rules, entitlement decisions, validation, subscription transitions, error normalization, authorization policy | In-process | `mock` | Every change |
| Integration | API behavior, database constraints, RLS policies, authentication integration, repository boundaries, query counts | Ephemeral PostgreSQL | `mock` for external providers | Every change |
| Contract | OpenAPI schema validity, response and error formats, generated-type drift, frontend/backend compatibility | In-process | `mock` | Every change |
| End-to-end | Onboarding, sign-in, protected routes, Free access, Pro paywall, checkout initiation, subscription-state presentation, session expiry, failure scenarios | Local stack | `mock` (ENV-08); `sandbox` in trusted jobs | Critical flows on every change; sandbox jobs on trusted refs |
| Security | Forged or expired credentials, privilege escalation, cross-tenant access (§7.4), entitlement manipulation, webhook forgery and replay, secret leakage (OBS-08), unsafe input | Local stack | `mock` | Every change |
| Resilience | Provider timeouts and errors, database unavailability, repeated webhooks, concurrency, partial failures, bounded retry behavior, telemetry outages | Local stack with fault injection | `mock` | Every change or nightly |
| Performance | §15.2 | Local (relative) and an isolated preview | `mock` for Stripe and LLM | On demand, with owner authorization for preview |
| Sandbox integration | Real provider behavior per DEC-006 | Sandbox accounts | `sandbox` | Trusted refs only, with approval |

**Test rules:**

- **TEST-01** Tests are deterministic. They use injected clocks and seeded randomness, and do not depend on wall-clock sleeps.
- **TEST-02** A flaky test is quarantined with a tracked issue. It is never deleted or weakened to obtain a pass.
- **TEST-03** Domain and entitlement logic is covered exhaustively against its decision matrices. Coverage is reported but is not the only quality signal.
- **TEST-04** Every evidence record labels each result as `mocked`, `sandbox`, or `live`. A mocked integration is never presented as proof of live connectivity.
- **TEST-05** Tests never generate uncontrolled high-volume traffic against real Stripe or LLM services.

---

## 15. Performance and Scalability

### 15.1 Principles

- Measure before optimizing. Identify the first bottleneck and saturation point before proposing optimization work.
- Focus areas:
  - database query efficiency and connection usage;
  - tenant-scoped query patterns;
  - request and payload limits;
  - API concurrency and rate limits;
  - Stripe webhook processing;
  - LLM concurrency and provider cost;
  - memory and runtime constraints (DEC-001);
  - frontend loading performance;
  - telemetry overhead.
- No caching is introduced initially. If caching is introduced, an ADR must document its invalidation and consistency rules.
- Quantitative performance acceptance is adopter-owned (DEC-003, ADR-0002). The template never claims that a target is met.

### 15.2 Performance test plan

**Environments:**

- `local`: profiling and relative comparisons only. Local numbers are never presented as deployment performance.
- **Isolated preview:**
  - A dedicated preview deployment with an isolated non-production database and synthetic data.
  - Requires owner authorization, including acknowledgement of cost and of platform and provider limits.
- Production stress testing is prohibited without explicit authorization and an approved safety plan.

**Workload model:** each run plan states:

- the number of synthetic users and organizations;
- rows per tenant;
- the request mix;
- the think time;
- the commit SHA and dataset seed.

**External dependencies:**

- Stripe and LLM calls use mocks.
- Webhook load uses synthetic events signed with a test-only secret against a non-production deployment.
- The authentication approach for load tests is documented. Any test-only path complies with ENV-08.

| ID | Scenario | Purpose |
|---|---|---|
| PERF-01 | Baseline: low, steady load per endpoint class (health, authenticated read, tenant-scoped list, authenticated write, billing status, entitlement-gated call) | Reproducible reference numbers |
| PERF-02 | Load: stepped concurrency up to expected usage | Behavior at expected load |
| PERF-03 | Stress: stepped beyond expected load until saturation or error threshold | First bottleneck and saturation point |
| PERF-04 | Spike: sudden burst from idle | Cold starts, connection storms, recovery |
| PERF-05 | Soak: sustained moderate load over an extended, plan-specified duration | Leaks, connection churn, drift |
| PERF-06 | Webhook concurrency: duplicate and concurrent synthetic events | Idempotency and lock contention under load |
| PERF-07 | Degradation: injected database and provider latency; mocked LLM latency; telemetry enabled vs. disabled | Bounded behavior and telemetry overhead |

**Safety rules:**

- Every run has a rate cap, a duration cap, and abort criteria: an error-rate surge, cost thresholds, or provider warnings.
- Each run plan is approved before running against any deployed environment.

**Metrics and report:** each report records:

- p50, p95, and p99 latency per endpoint class;
- throughput and error rate by class;
- concurrency level;
- cold-start count and latency;
- function duration and memory;
- database connections and query time;
- test duration, environment, dataset, request mix, and commit;
- deviations and limitations.

Raw output is stored outside version control. Summaries go to `dev-docs/evidence/`.

**Reproducibility:** scripts live in `perf/`, use fixed seeds, and document their exact command lines. A rerun on the same commit and environment should reproduce results within a variance that the report states.

### 15.3 Bounded-resource defaults

These are initial engineering defaults, held in configuration. They are **not** performance targets and are revisited after P7.

| Resource | Initial default |
|---|---|
| JSON request body | 1 MB maximum (below the platform limit) |
| Page size | Default 20, maximum 100 |
| Outbound HTTP timeout (Clerk, Stripe) | 10 s |
| Database connect / statement timeout | 5 s / 10 s |
| LLM call timeout | 30 s |
| Retries for idempotent outbound calls | At most 2, exponential backoff with jitter |
| Per-instance LLM concurrency | Small fixed bound (set at P5) |

### 15.4 Approved performance targets

None in the template. Targets are adopter-owned (DEC-003, [ADR-0002](adr/0002-organization-entitlements-contacts-adopter-decisions.md)). Adopters propose and approve them with `perf/run-plan-template.md`, from their own preview baseline.

---

## 16. CI, Deployment, and Operations

### 16.1 CI pipeline

| Stage | Checks | Secrets |
|---|---|---|
| Static | Formatting, linting, TypeScript and Python strict type checking, import-boundary and cycle checks | None |
| Unit and contract | Unit tests; OpenAPI validity; generated-contract drift | None |
| Integration | Ephemeral PostgreSQL; migrations from empty; schema lint; RLS tests; API integration tests | None |
| Build | Frontend and backend builds; client-bundle secret scan (ENV-07); production-artifact check (ENV-08) | None |
| Security | Dependency vulnerability scan; static analysis; secret scan; license check | None |
| End-to-end | Critical flows in mock mode | None |
| Deployment configuration | Validate deployment configuration and build output locally; no deployment | None |
| Sandbox integration | DEC-006 procedures | Sandbox secrets via protected environments; trusted refs only, with approval |

### 16.2 CI security

- **CI-SEC-01** Workflows default to read-only repository permissions, and elevate per job only where required.
- **CI-SEC-02** Third-party actions are pinned to full commit SHAs and updated through dependency automation.
- **CI-SEC-03** Untrusted pull-request code never runs with secrets. Privileged pull-request triggers never check out pull-request code.
- **CI-SEC-04** Secrets are scoped to protected environments that require approval. Preview and production credentials are separate.
- **CI-SEC-05** Required checks are never disabled, and tests are never weakened, to obtain a passing result.

### 16.3 Deployment and rollback

- **OPS-01** Deployment, promotion, and Git-integration setup are remote actions. Each requires explicit owner authorization.
- **OPS-02** Migrations follow expand → deploy → contract.
  - Every release remains compatible with the schema of the previous release, so that rolling back to the prior deployment is safe.
  - Rollback uses the platform's revert to the previous deployment. Schema rollback is not automatic.
- **OPS-03** Readiness and health semantics follow API-06 and DEC-001.

### 16.4 Provider outage behavior

| Provider unavailable | Expected behavior |
|---|---|
| Clerk | New sign-ins fail with clear messaging. Requests with valid tokens keep working while cached keys remain valid; otherwise verification fails closed. |
| Stripe | Checkout and plan management are unavailable with clear messaging. Existing entitlements, read from synchronized records, are unaffected. Webhooks are retried by Stripe and reconciled. |
| Supabase | Readiness fails. Data operations return `503`. No partial writes. |
| PostHog / Sentry | No user-visible impact (OBS-06). |
| LLM provider | Only LLM-dependent features return `503` (LLM-09). |

### 16.5 Required operational documentation (`docs/`)

- Environment setup for local, test, preview, and production.
- Environment-variable ownership and rotation.
- Deployment and rollback procedures.
- Migration sequencing.
- Health and readiness checks.
- Provider outage handling (§16.4).
- Incident response and troubleshooting.
- Backup and restore: the local restore drill (`pnpm db:restore-drill`) and the adopter checklist with a hosted restore-test procedure (DEC-005, adopter-owned).
- Data inventory and the adopter retention, deletion, and export checklist (`docs/data-inventory.md`, DEC-004, adopter-owned).
- Python runtime limitations established by DEC-001.
- Architecture, module anatomy, and extension guide (`docs/architecture.md`); scheduled jobs, outbox operations, and connection-pool sizing (`docs/operations.md`).

---

## 17. Git and Agent Operating Rules

These rules are mandatory for every contributor and agent:

1. **NEVER** push, publish, release, deploy, or modify remote repository or service state without explicit authorization.
2. Local commits are permitted when useful and safe. Stage explicit paths, review the staged diff for secrets and unrelated changes, and never bypass hooks.
3. **NEVER** expose, commit, print, or transmit secrets or sensitive data.
4. Preserve unrelated user changes and existing work.
5. Ask for clarification before destructive operations, and before any ambiguous security-sensitive, billing-related, or architectural decision.
6. Never disable required checks or weaken tests merely to obtain a passing result.
7. Never fabricate test results, performance measurements, security findings, or deployment evidence.
8. Record assumptions, unresolved decisions, and deviations from the approved design (§3, §20).
9. Update documentation whenever requirements, architecture, threats, or operational behavior change.
10. Keep changes focused, reviewable, modular, and consistent with the architecture (§4).

---

## 18. Implementation Phases and Execution Order

Work proceeds in small, reviewable, test-backed increments. Each increment:

- updates the traceability matrix status for the requirements it touches;
- records evidence;
- may be committed locally.

Broad implementation does not begin while a decision that blocks it remains open, unless the owner explicitly directs otherwise and the deviation is recorded below.

**Recorded deviations**

- **D-001 (2026-10-14).** On the owner's explicit instruction, P2–P6 were implemented before the DEC-001 Vercel proof (P1), with local-only verification.
  - **Risk:** Vercel runtime findings may require changes to the ASGI entrypoint (`apps/api/index.py`), connection pooling, webhook raw-body handling, or project topology.
  - **Mitigations:**
    - a thin entrypoint;
    - standard ASGI;
    - pooler-compatible driver settings (prepared statements disabled);
    - a lazily opened, concurrency-safe pool;
    - raw-body webhook handlers.

  DEC-001 remains OPEN and Gate E remains BLOCKED.

- **D-002 (2026-10-14).** On the owner's explicit selection (ADR-0003), asynchronous processing was introduced before DEC-001 verified platform support (ARCH-10): the transactional outbox, after-response dispatch, and Vercel Cron jobs.
  - **Risk:** the platform may freeze the function before after-response dispatch completes, and Vercel Cron limits depend on the plan.
  - **Mitigations:**
    - no new infrastructure (PostgreSQL only);
    - at-least-once delivery with idempotent handlers;
    - the `drain-outbox` job as the fallback for anything not dispatched;
    - daily default schedules that deploy on any plan;
    - delivery guarantees and recovery are documented in `docs/architecture.md` and `docs/operations.md`.

  Verification belongs to the DEC-001 proof.

| Phase | Scope | Entry criteria | Exit evidence | Status (2026-10-14) |
|---|---|---|---|---|
| P0 | Repository inspection, specification consolidation, decision register, `.gitignore`, `AGENTS.md` | — | This revision committed locally; owner review | Done |
| P1 | DEC-001 proof (local, then authorized preview deployment) | Owner authorizes the Vercel project and preview deployment; non-production Supabase project; Stripe sandbox | `dev-docs/evidence/dec-001-vercel-python.md`; DEC-001 resolved or escalated | BLOCKED — owner chose local-only for now |
| P2 | Scaffold: layout, toolchain, configuration validation, CI skeleton, README, `.env.example`, community files, template initialization | DEC-001, DEC-007 resolved; DEC-012 for license files | Gate A checks | Implemented locally (deviation D-001); community contacts are adopter placeholders (ADR-0002); remote CI never run |
| P3 | Identity, tenancy, schema, migrations, RLS, authorization, audit | DEC-008, DEC-009 resolved | AUTH, TEN, ADM, DB, MIG requirements; NT-01 to NT-09, NT-11 | Implemented; tests PASS (mocked) |
| P4 | Billing: checkout, webhooks, synchronization, entitlements, reconciliation, paywall API | DEC-010 resolved; DEC-002 for organization scope | BILL requirements; §9.10 tests; sandbox evidence (DEC-006) | Implemented, including the DEC-002 (b) organization truth table; tests PASS (mocked Stripe API); sandbox NOT VERIFIED |
| P5 | LLM port and adapter, observability, rate limiting | DEC-011 resolved; LLM provider selected (DEC-006) | LLM, OBS, SEC-09 requirements | Implemented; tests PASS (mocked); providers NOT VERIFIED |
| P6 | Frontend flows, paywall, error and loading states, accessibility | P3 to P5 APIs available | FE requirements; end-to-end and accessibility evidence | Implemented; unit and account-free e2e PASS; browser and accessibility e2e NOT VERIFIED (needs Clerk sandbox) |
| P7 | Resilience, security hardening, performance baseline and stress; propose targets | Authorized performance environment | Resilience and security evidence; performance reports; DEC-003 target proposal | Resilience and security tests done locally. Performance harness (`perf/`) covers PERF-01 to PERF-07, all recorded locally. Preview runs need the DEC-001 environment. Targets are adopter-owned (ADR-0002) |
| P8 | Operations documentation, backup and restore, deletion and export, integration classification, handoff | DEC-003, DEC-004, DEC-005, DEC-006 resolved, or explicitly accepted by the owner | Gates D and E; handoff report (§22) | Operations docs, data inventory, and adopter checklists done (DEC-003 to DEC-005 adopter-owned); local restore drill PASS; integrations diagnostics page built. Integration classification pending DEC-006 |

---

## 19. Required Quality Gates

All gates are mandatory.

### Gate A — Repository readiness

- Clean-clone setup is reproducible (REPO-11).
- Documentation and environment examples are complete (REPO-01, REPO-02).
- No secrets or inherited credentials are present (REPO-10).
- Required CI checks run successfully in a clean environment (§16.1).

### Gate B — Functional correctness

- Build, lint, type checking, and required tests pass.
- API contracts and critical user flows are verified.
- Error and loading states behave correctly.

### Gate C — Security

- Required negative authorization tests pass (§7.4).
- Cross-tenant access is denied.
- Entitlements are enforced server-side.
- Webhook signature verification and idempotency tests pass.
- No unresolved critical security findings or unremediated secret exposures remain.

### Gate D — Resilience and performance

- Dependency-failure and concurrency tests have recorded results.
- Controlled performance tests are reproducible.
- Approved quantitative performance targets (DEC-003) are met before acceptance is claimed.
- Remaining risks and limitations are documented.

### Gate E — Deployment and handoff

- The approved deployment architecture has been verified (DEC-001).
- External integrations are classified as mocked, sandbox-tested, or live-tested (DEC-006).
- Rollback, migration, and operational guidance is available.
- Blocking decisions are resolved or explicitly accepted by the owner.

The project is never declared complete while a mandatory gate is failing, required evidence is missing, or a blocking decision remains unresolved.

---

## 20. Evidence and Status Reporting

**Evidence statuses** are used consistently in every report and in the traceability matrix:

- `PASS`: the check was executed and met its acceptance criteria.
- `FAIL`: the check was executed and did not meet its criteria.
- `BLOCKED`: execution or acceptance is prevented by a dependency or an unresolved decision.
- `NOT VERIFIED`: sufficient evidence has not been produced.
- `RETIRED`: the requirement was explicitly removed or superseded with approval.

**Evidence records** live in `dev-docs/evidence/`. Each record states:

- the requirement or decision IDs;
- status;
- date;
- commit SHA;
- environment;
- integration mode (`mocked`, `sandbox`, or `live`);
- exact commands;
- a result summary;
- the artifact location;
- limitations.

Evidence records never contain secrets, tokens, or personal data.

---

## 21. Requirements Traceability Matrix

Status as of 2026-10-14. Evidence: [`dev-docs/evidence/2026-10-14-local-verification.md`](evidence/2026-10-14-local-verification.md), [`2026-10-14-diagnostics-perf-local.md`](evidence/2026-10-14-diagnostics-perf-local.md), [`2026-10-14-adr-0002-perf07-local.md`](evidence/2026-10-14-adr-0002-perf07-local.md), and [`2026-10-14-adr-0003-modular-architecture.md`](evidence/2026-10-14-adr-0003-modular-architecture.md).

`PASS` here means the check was **executed locally** with **mocked** external integrations and met its criteria. It is not sandbox or live verification (TEST-04).

| Requirements | Summary | Verification | Gate | Blocked by | Status |
|---|---|---|---|---|---|
| ARCH-01, ARCH-02 | Layering, no cycles | `lint-imports` (8 contracts, including `exhaustive` module layers and acyclic modules); mutation-checked | B | — | PASS |
| ARCH-03 to ARCH-07, ARCH-09 to ARCH-12 | Single rules, adapters, no global state, bounded calls, ADRs | Review; LLM retry tests; timeouts configured | B | — | PASS (review) |
| ARCH-08 | No N+1 queries | Query-count tests (project lists, billing status, organization entitlement); single-statement transaction setup; at most 8 statements per request | B | — | PASS |
| ARCH-13 | Modular monolith boundaries | import-linter contracts; `tests/architecture` (api-only cross-module imports, module shape, unique permissions and event types); mutation-checked | B | — | PASS |
| ARCH-14 | Transactions: one-statement setup, request scope, release, two pools, admission gate, nesting guard | `test_request_scope.py` (16), `test_query_counts.py`; PERF-07 and PERF-03 local (starvation fix measured) | B, D | — | PASS (local); platform behavior NOT VERIFIED (DEC-001) |
| ARCH-15 | Transactional outbox | `test_outbox_jobs.py` (recording, RLS, dispatch, retries, dead letters, leases, retention, billing event) | B, C | — | PASS (local); after-response dispatch on Vercel NOT VERIFIED (D-002) |
| ARCH-16 | Scheduled jobs | `test_outbox_jobs.py` (secret auth, 404 when off, each job, registry and `vercel.json` parity, CLI) | B, C, E | — | PASS (local); Vercel Cron NOT VERIFIED (DEC-001) |
| ARCH-17 | Module generator | `pnpm test:new-module` (CI step); mutation-checked | A, B | — | PASS |
| API-01 to API-06 | Versioning, problem details, pagination, bearer-only, idempotency, health | Contract and platform tests; account-free e2e | B | — | PASS |
| ENV-01 | Separate credentials per environment | Operational | E | DEC-006 | NOT VERIFIED |
| ENV-02 to ENV-06 | Modes, validation, key/mode consistency, production guards, `.env.example` | Config tests; migration guard tests | A, C | — | PASS |
| ENV-07 | No server-only values in the bundle | `check:bundle-secrets` (plus negative test) | C | — | PASS |
| ENV-08 | No test-only auth path in the application | Review | C | — | PASS (review) |
| REPO-01 to REPO-08, REPO-10, REPO-12 | Docs, commands, lockfiles, migrations, seed, reset, init (including contacts), mock-mode tests, no credentials | Clean clone; `test:init-template` (also a CI step); gitleaks history and tracked files | A | — | PASS |
| REPO-09 | Community files and policies | Files exist; contact placeholders filled by `init-template` and enforced by `check:placeholders` (CI) | A | — | PASS (ADR-0002) |
| REPO-11 | Clean-clone onboarding | Fresh clone (shared caches; see limitations) | A | — | PASS |
| AUTH-01 to AUTH-05 | Server-side token verification | Unit tests with generated keys; end-to-end through the app | C | — | PASS (mocked IdP) |
| AUTH-06 | Session expiry UX | Component test; real Clerk flow | B | DEC-006 | NOT VERIFIED (sandbox) |
| TEN-01 to TEN-06, TEN-08 | Tenant context, deny-by-default, scoping, RLS, 404/403 | NT-01 to NT-04, NT-08, NT-09, route manifest, RLS tests | C | — | PASS |
| TEN-07 | Membership change enforcement | NT-05 (bound, webhook, outage), NT-06 (re-check, serialization) | C | — | PASS (300 s, confirmed in ADR-0002) |
| ADM-01, ADM-02 | Admin authorization and audit | NT-07; audit assertions | C | — | PASS |
| DB-01 to DB-09 | Integrity, indexes, transactions, bounds, pooling, RLS, isolation, fixtures | RLS and integration tests | B, C | — | PASS |
| DB-10 | Backup and restore | Local restore drill; adopter checklist and hosted restore-test procedure | E | — | PASS (local drill and documentation). Hosted restore is adopter-owned (ADR-0002) |
| MIG-01 to MIG-05 | Migration safety | Checksum tamper test; non-local refusal; idempotent apply | B, C | — | PASS (CI stage not yet executed remotely) |
| BILL-01 to BILL-21 | Entitlements, checkout, webhooks, transitions, reconciliation, presentation | Unit matrix; billing integration tests; web unit tests | B, C | — | PASS (mocked Stripe API; real signature verification) |
| BILL-* sandbox | Real Stripe test-mode behavior (test clocks, portal) | Sandbox procedure in docs/integrations.md | E | DEC-006 | NOT VERIFIED |
| BILL-ORG-01 | Interim organization gating | — | C | — | RETIRED (superseded by BILL-ORG-02, ADR-0002) |
| BILL-ORG-02 | Organization entitlement policy, DEC-002 (b) | Truth-table tests (`test_org_entitlements.py`); NT-12 | C | — | PASS |
| LLM-01 to LLM-10 | Port, normalization, bounded retries, limits, budget, privacy, mock, isolation, docs | Adapter and platform tests | B, C, D | — | PASS (mocked provider) |
| LLM real provider | Connectivity with a real key | Sandbox check | E | DEC-006 | NOT VERIFIED |
| OBS-01 to OBS-08 | Telemetry configuration, redaction, allowlists, non-blocking, leakage | Unit and leakage tests; outage tests | C, D | — | PASS (mocked transports) |
| OBS real services | PostHog and Sentry capture without personal data | Sandbox inspection | E | DEC-006 | NOT VERIFIED |
| OBS-09 | Telemetry retention | Documentation; adopter checklist | E | — | PASS (documented; adopters set the periods) |
| FE-02, FE-03, FE-05 to FE-07, FE-10 | Boundaries, states, protected routes, paywall, 404, error mapping | ESLint bans; Vitest; account-free e2e | B | — | PASS |
| FE-01, FE-04, FE-08, FE-09, FE-11 | Accessibility, session UX, error boundaries, keyboard, browser flows | axe and Playwright page and authenticated suites | B | DEC-006 (Clerk sandbox) | NOT VERIFIED |
| FE-13 | Feature folders and import boundaries | ESLint `import/no-restricted-paths` (`eslint.boundaries.mjs`); 23 tests lint real snippets through the real configuration; mutation-checked | B | — | PASS |
| FE-14 | Typed API client generated from OpenAPI | `tsc` with `@ts-expect-error` checks on paths and params; client tests | B | — | PASS |
| FE-12 | Frontend performance budgets | Measurements | D | Preview environment (DEC-001) | NOT VERIFIED (budgets adopter-owned; Core Web Vitals not measured) |
| SEC-01 to SEC-05, SEC-07 to SEC-10, SEC-13 to SEC-15 | Validation, headers, secrets, audits, lockfiles, errors, limits, licenses, webhooks, redirects | Tests; audits; gitleaks; license check | C | — | PASS (as of 2026-10-14) |
| SEC-06 | Static analysis (CodeQL) | Workflow defined | C | Remote CI not authorized | NOT VERIFIED |
| SEC-11 | Vulnerability reporting | SECURITY.md with an adopter placeholder enforced by `check:placeholders` | A | — | PASS (ADR-0002) |
| SEC-12 | Required review of migrations and workflows | CODEOWNERS and branch protection | A | Remote settings not authorized | NOT VERIFIED |
| TEST-01 to TEST-05 | Test discipline | Review; deterministic clocks; labeled evidence | B | — | PASS |
| PERF-01 to PERF-07 | Performance scenarios | Reports per §15.2 | D | Preview environment (DEC-001) | NOT VERIFIED (all seven recorded **locally** for relative comparison only; no preview runs) |
| Performance acceptance | Approved targets met | §15.4 | D | — | RETIRED for the template: adopter-owned (ADR-0002) |
| CI-SEC-01 to CI-SEC-05 | CI security | Workflow review; first remote run | A, C | Remote CI not authorized | NOT VERIFIED (implemented) |
| OPS-01 to OPS-03, §16.4, §16.5 | Deployment, rollback, operations | docs/operations.md | E | DEC-001 | BLOCKED |
| Account deletion and export | Deletion and export behavior | — | E | — | RETIRED for the template: adopter-owned (ADR-0002); no feature ships |
| Integration diagnostics | Per-provider configuration report and checks; operator-only; no secrets | Diagnostics integration, unit, and adapter tests | C, E | — | PASS (mocked providers) |
| Integration classification | Mocked, sandbox, or live per provider | Evidence records; `/integrations` checks in sandbox mode | E | DEC-006 | All providers **mocked** |

## 22. Required Handoff Report

Every implementation phase, and the final handoff, reports:

- Changes made and affected modules.
- Architectural decisions and deviations, with links to ADRs.
- Tests actually executed and their results, with evidence links and integration modes.
- Security and performance verification completed.
- Deployment verification and external integration coverage (mocked, sandbox, or live per provider).
- Open decisions and blockers.
- Known limitations and residual risks.
- Documentation changes.
- Local commit identifiers, if commits were created.
- Explicit confirmation that no unauthorized push, publication, deployment, or release occurred.

The report clearly distinguishes verified behavior from assumptions, mocks, and untested integrations.

---

## 23. Current Status (2026-10-14)

- **Implemented:**
  - **Python API as a modular monolith** (ADR-0003):
    - a shared kernel, and the modules identity, billing, projects, llm, and diagnostics, all with identical layers;
    - request-scoped transactions with separate request and worker pools;
    - a transactional outbox with after-response dispatch;
    - scheduled jobs (CLI and secret-protected HTTP for Vercel Cron);
    - composed per-module settings;
    - migrations with forced RLS (0001 core, 0002 outbox).
  - **Next.js web app:**
    - code organized by feature (`src/features/*`, `src/ui`, `src/lib`);
    - a typed client generated from OpenAPI (`openapi-fetch`);
    - a minimal UI for every integration, including the operator-only `/integrations` page.
  - **Developer experience:**
    - `pnpm new:module` generates a tenant-scoped module end to end, and CI verifies the generator;
    - `docs/architecture.md`;
    - init-template with contact placeholders.
  - **Product decisions:** DEC-002 (b) organization entitlements; adopter checklists for DEC-003 to DEC-005.
  - **Verification tooling:**
    - 8 import contracts plus architecture tests;
    - query-count tests;
    - the local performance harness, PERF-01 to PERF-07;
    - the local restore drill.
- **Integrations:** all **mocked**. No sandbox or live service has been contacted.
- **Local evidence:**
  - API 274 tests, web 51 unit tests, 20 account-free e2e checks, and the generator check pass.
  - Lint, strict types, build, contract drift, init-template, placeholder, bundle-secret, audit, and license checks pass.
  - At 20 ms DB RTT (local), request scope cut p50 latency by 25% to 33% beyond the one-statement setup.

  Details: [`2026-10-14-adr-0003-modular-architecture.md`](evidence/2026-10-14-adr-0003-modular-architecture.md) and the earlier records.
- **Gates:**

  | Gate | Status | Reason |
  |---|---|---|
  | A | NOT VERIFIED | Remote CI has never run (needs authorization to push) |
  | B | NOT VERIFIED | Browser and signed-in e2e need Clerk sandbox keys (DEC-006) |
  | C | NOT VERIFIED | CodeQL (remote CI) and sandbox verification pending; local security tests PASS |
  | D | NOT VERIFIED | Local PERF-01 to PERF-07 recorded. Preview runs need DEC-001. Targets are adopter-owned (ADR-0002) |
  | E | BLOCKED | DEC-001 (deployment proof, Vercel Cron, after-response dispatch), DEC-006 (integration classification) |

- **Decisions:**
  - DEC-002 to DEC-012: RESOLVED (ADR-0001, ADR-0002). DEC-003 to DEC-005 are adopter-owned.
  - Architecture: ADR-0003.
  - **Open:** DEC-001 and DEC-006, which need owner action.
- **Deviations:** D-001 and D-002 (§18).
- **Remote actions:** none. No push, publication, deployment, or release has occurred.
- **Next steps (owner action required):**
  1. Apply `dev-docs/pending/web-import-boundaries.patch` (FE-13); see `dev-docs/pending/README.md`.
  2. Add sandbox keys to `.env`, and your Clerk user ID to `DIAGNOSTICS_OPERATOR_IDS` (or use `APP_ENV=local`). Then run the `/integrations` checks, the sandbox e2e suites (`E2E_SANDBOX=1`), and the procedures in `docs/integrations.md`, and record the evidence (DEC-006).
  3. Authorize pushing to a remote, so CI, CodeQL, and the generator check run (Gates A and C).
  4. Authorize the DEC-001 Vercel preview proof. It covers the Python runtime, Vercel Cron, after-response dispatch, and preview performance runs (Gates D and E).

**Final principle:** build a template that is secure by default, modular by design, reproducible from a clean clone, and honest about its verified limits. Do not trade correctness, security, or maintainability for superficial completion.
