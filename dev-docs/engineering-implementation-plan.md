# SaaS Template — Engineering Implementation Plan

| | |
|---|---|
| **Document role** | Authoritative engineering implementation specification for this repository |
| **Revision** | 4 — 2026-10-31 (cycle 1 fix round, round 1b, cycle 2 and fix round 2: [ADR-0004](adr/0004-cycle-1-adopted-decisions.md)) |
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
| Architecture Decision Records | `dev-docs/adr/NNNN-short-title.md` | ADR-0001 (approved recommendations for DEC-007 to DEC-012); ADR-0002 (DEC-002 option (b), DEC-009 value, DEC-012 contacts, DEC-003 to DEC-005 adopter-owned); ADR-0003 (modular monolith, request-scoped transactions, outbox, jobs, generator); ADR-0004 (decisions adopted by delegation in the cycle 1 fix round, awaiting owner confirmation) |
| Evidence records | `dev-docs/evidence/` | `2026-10-14-local-verification.md`, `2026-10-14-diagnostics-perf-local.md`, `2026-10-14-adr-0002-perf07-local.md`, `2026-10-14-adr-0003-modular-architecture.md`, `dec-001-vercel-python.md` (the DEC-001 local proof), `2026-10-15-review-fixes.md` (a full review and its fixes), the two `2026-10-15-second-review-*.md` records, `2026-10-30-cycle-1-fix-round.md` (the cycle 1 fixes by package, the merged-tree checks, and what was not run), and `2026-10-31-round-1b-cycle-2-fix-round-2.md` (round 1b, the cycle 2 review and its findings by disposition, fix round 2, and the documentation pass) (mocked integrations, local only) |
| Adopter-facing documentation | `README.md`, `docs/setup.md`, `docs/deployment.md`, `docs/architecture.md`, `docs/integrations.md`, `docs/operations.md`, `docs/limitations.md`, `docs/data-inventory.md`, `perf/README.md`, `perf/run-plan-template.md`, `supabase/README.md` | Exists |
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
- A decision that the owner delegated ("recommend the options, then choose the recommended one") and an agent adopted keeps the status `OPEN` with the qualifier "adopted by delegation". It records the chosen option, the alternative, and the cost of reversal in an ADR, and stays reversible. It becomes `RESOLVED` only when the owner records approval (date and option) as above. Nothing is ever recorded as "decided by the owner" without that approval.

### 3.2 Register summary

| ID | Decision | Status | Blocks | Next action |
|---|---|---|---|---|
| DEC-001 | Python backend on Vercel | OPEN — local proof recorded 2026-10-14 ([evidence](evidence/dec-001-vercel-python.md)); topology B (one project with Vercel Services, Beta) adopted by delegation 2026-10-30 ([ADR-0004 §1](adr/0004-cycle-1-adopted-decisions.md)); platform verification pending | The platform verification of the backend (Gate E); implementation ran ahead of it under deviation D-001 | Owner authorizes the preview deployment, runs the two-minute check, and confirms or reverses the topology (§18, P1; §23) |
| DEC-002 | Individual billing vs. organization entitlements | RESOLVED 2026-10-14 — option (b) ([ADR-0002](adr/0002-organization-entitlements-contacts-adopter-decisions.md)) | — | — |
| DEC-003 | Performance targets | RESOLVED 2026-10-14 — adopter-owned (ADR-0002) | — (the template claims no targets) | Each adopter sets targets from its own preview baseline (`perf/run-plan-template.md`) |
| DEC-004 | Data retention, export, and deletion | RESOLVED 2026-10-14 — adopter-owned (ADR-0002) | Deletion and export features remain out of the template; no deletion-completeness claim | Each adopter completes the checklist in `docs/data-inventory.md` |
| DEC-005 | Backup and recovery | RESOLVED 2026-10-14 — adopter-owned (ADR-0002) | No recovery-readiness claim | Each adopter follows the checklist in `docs/operations.md` (a local restore drill exists) |
| DEC-006 | External integration verification | OPEN until verified | Any "sandbox-tested" or "live-tested" claim; Gate E | Owner provides sandbox keys in `.env`; then run `pnpm verify:sandbox` (setup check, Stripe sandbox suite, sandbox e2e) and the `/integrations` checks |
| DEC-007 | Frontend framework, repository layout, toolchain, and API contracts | RESOLVED 2026-10-14 — recommendation approved ([ADR-0001](adr/0001-approved-recommendations.md)) | — | — |
| DEC-008 | Data access path and RLS enforcement model | RESOLVED 2026-10-14 — option A approved (ADR-0001); carried out by the `app_login` role (DB-11, [ADR-0004 §3](adr/0004-cycle-1-adopted-decisions.md)) | — | Validate on Vercel in DEC-001 |
| DEC-009 | Organization membership source of truth and staleness bound | RESOLVED 2026-10-14 — option A approved (ADR-0001); bound 300 s confirmed (ADR-0002) | — | — |
| DEC-010 | Billing state and plan-change policy | RESOLVED 2026-10-14 — recommendation approved (ADR-0001) | — | — |
| DEC-011 | Rate limiting and shared counters | RESOLVED 2026-10-14 — option A approved (ADR-0001) | — | Configure platform rate-limit rules at deployment time |
| DEC-012 | License and community contacts | RESOLVED 2026-10-14 — Apache-2.0 (ADR-0001); contacts are adopter placeholders (ADR-0002) | — | — |
| DEC-013 | Preventing a second subscription while one is unsettled | OPEN (recorded 2026-10-15) — option (B) adopted by delegation 2026-10-30 ([ADR-0004 §2](adr/0004-cycle-1-adopted-decisions.md)); implemented and tested with mocks, sandbox verification NOT RUN | Owner confirmation; sandbox verification (DEC-006) | Owner confirms or reverses option (B) |
| DEC-014 | The diagnostics operator capability: approval (ADM-02) and a second factor for operators | OPEN (recorded 2026-10-30) — option (B) adopted by delegation 2026-10-31 ([ADR-0004 §15](adr/0004-cycle-1-adopted-decisions.md)) | Owner confirmation | Owner confirms or reverses option (B) |

### 3.3 Decision entries

#### DEC-001 — Python Backend on Vercel

- **Status:** OPEN — mandatory technical validation. Topology B was adopted by delegation on 2026-10-30 ([ADR-0004 §1](adr/0004-cycle-1-adopted-decisions.md)), reversible, awaiting owner confirmation.
- **Owner:** Repository owner.
- **Blocks:**
  - Backend implementation beyond the proof (it ran ahead under deviation D-001, with local-only verification).
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
- **Local proof (2026-10-14):** [`proofs/dec-001/`](../proofs/dec-001/README.md), with results in [`evidence/dec-001-vercel-python.md`](evidence/dec-001-vercel-python.md). It found three defects, all fixed. Items that only the platform can show remain `NOT VERIFIED`.
- **Topology (adopted by delegation, 2026-10-30):** option B, one Vercel project with Vercel Services: `web` and `api` and the four crons in the root [`vercel.json`](../vercel.json) ([ADR-0004 §1](adr/0004-cycle-1-adopted-decisions.md)). The condition in the evidence record ("Services generally available") is **not met**: Services is Beta on all plans. Every project variable reaches both services, so FE-02 holds in the code and not at the environment level (T-22). Whether the Next.js proxy runs inside a service is **NOT VERIFIED**: Vercel's reference says "Middleware is not supported inside a service" and `vercel build` emits the proxy anyway ([sources](evidence/dec-001-vercel-python.md#nextjs-proxy-under-services-not-verified)). Page protection therefore has a second, client-side layer (FE-15), and the API authorizes every operation either way. The two-minute check for the first preview is in [`proofs/dec-001/README.md`](../proofs/dec-001/README.md#first-preview-the-two-minute-check).
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
  - No backend code exists outside the proof. *(Superseded on 2026-10-14 by deviation D-001: the template was implemented ahead of the proof, with local-only verification.)*
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

- **Status:** RESOLVED 2026-10-14 — accepted as **adopter-owned** (ADR-0002). The template ships the data inventory and an adopter checklist (`docs/data-inventory.md`). It has no deletion or export feature and makes no deletion-completeness claim. The interim rule below stays in force for template code. For question 2 the code takes one default so that a user who deleted their Clerk account is not billed on: their subscription is set to end at the period's end ([ADR-0004 §21](adr/0004-cycle-1-adopted-decisions.md), BILL-16); the adopter's policy replaces it.
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
  - `disabled`: off; no provider is called (analytics, error reporting, and the LLM check).
- **Mocked tests never prove connectivity.**

| Provider | Credentials (names only, never values) | Callback / configuration surfaces | Verification procedure |
|---|---|---|---|
| Clerk | Publishable key; secret key; JWT verification key or JWKS URL (the key, `CLERK_JWT_KEY`, is read by the API and, as `clerkMiddleware`'s `jwtKey`, by the web proxy); webhook signing secret (if DEC-009 uses webhooks) | Sign-in/up redirect URLs; allowed origins / authorized parties; webhook endpoint; Organizations enabled with **Membership optional** (personal accounts allowed) | Sandbox: development instance with synthetic users; verify token validation, session expiry, organization membership changes |
| Stripe | Secret key (test mode/sandbox); publishable key; per-endpoint, per-environment webhook signing secret; Pro monthly and yearly price IDs | Checkout success/cancel URLs; Customer Portal return URL (if DEC-010 adopts it); webhook endpoint | Sandbox: Stripe CLI and test clocks for checkout, renewal, failure, cancellation, reactivation, retry |
| Supabase | Pooled connection string for the `app_login` role (`DATABASE_URL`); separate migration-role connection string (`MIGRATION_DATABASE_URL`); project URL | Network restrictions; roles; RLS | Sandbox: non-production project; migrations, RLS tests, connection behavior under load |
| PostHog | Project ingest key (public); host | Allowed domains; data retention settings | Sandbox: separate non-production project; event allowlist and redaction checks |
| Sentry | Frontend and backend DSNs; auth token, organization slug, and project slug for source maps (the web project's build environment, not CI) | Environments; scrubbing rules; sample rates | Sandbox: separate project or environment; redaction checks |
| LLM provider | API key; model identifier (neither is needed with `LLM_MODE=disabled`) | Spend limits at the provider | Provider not yet selected. Sandbox: a minimal-token connectivity check under an approved spending cap |

- **Clerk Organizations setting.** The template needs personal accounts (TEN-01: the personal workspace and the personal Pro context). Clerk's default for applications created since 2025-08-22 is Membership required, under which every user must create or join an organization and has no personal workspace: the documentation says "This is the default setting since on August 22, 2025" ([Clerk: configure organizations](https://clerk.com/docs/guides/organizations/configure)). Only a person can change it, in the Clerk dashboard or with `npx clerk@latest enable orgs` without `--force-selection`; the instance secret key cannot (the Backend API has no field for it, per the review).
- **Required evidence:** for each provider, a dated evidence record stating the mode, environment, procedure, and result. Live verification happens only with explicit authorization.
- **Automated entry points** (built, tested with mocks or locally, **NOT RUN** against any provider): `pnpm check:setup [--send-test-events]` (the probes behind `/integrations`, an evidence block, and the manual steps still needed), `pnpm setup:stripe`, `pnpm test:stripe-sandbox` (`apps/api/tests/sandbox/`), and `pnpm verify:sandbox`, which runs the setup check, the Stripe sandbox suite, and the signed-in end-to-end tests, each only when its keys are present, and prints a record in the form of section 20.
- **Resolution criteria:** each provider is classified as mocked, sandbox-tested, or live-tested, with evidence. The LLM provider is selected and recorded.
- **Interim rule:** every integration is reported as `mocked`.

#### DEC-007 — Frontend Framework, Repository Layout, Toolchain, and API Contracts

- **Status:** RESOLVED 2026-10-14 — the owner approved the recommendation; see [ADR-0001](adr/0001-approved-recommendations.md). The text below is kept as the decision record. Local PostgreSQL is plain `postgres:17.6-alpine` (`pnpm db:up`), not the Supabase CLI listed under Toolchain: deviation D-004.
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

- **Status:** RESOLVED 2026-10-14 — the owner approved the recommendation; see [ADR-0001](adr/0001-approved-recommendations.md). The text below is kept as the decision record. Option A is carried out by the `app_login` role: DB-11 and [ADR-0004 §3](adr/0004-cycle-1-adopted-decisions.md).
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

#### DEC-013 — Preventing a Second Subscription While One Is Unsettled

- **Status:** OPEN — recorded 2026-10-15 by the review of the billing module. **Option (B) adopted by delegation on 2026-10-30** ([ADR-0004 §2](adr/0004-cycle-1-adopted-decisions.md)), reversible, awaiting owner confirmation. BILL-07 implements it; it is tested with mocks, and the sandbox verification below is NOT RUN.
- **Owner:** Repository owner.
- **Blocks:** owner confirmation, and sandbox verification of the flow (DEC-006).
- **Rationale:** BILL-07 used to refuse checkout only when the user already had an *entitling* subscription. A user whose subscription is `unpaid` or `paused`, who has a paying subscription on a price outside the catalog, or who starts two checkouts in two tabs, could complete a second checkout. Stripe then holds two subscriptions and charges both. The entitlement decision picks one, so nothing visible goes wrong except the second charge. *Correction (2026-10-30): this entry first named `incomplete` subscriptions from failed Checkout payments. Since API version 2025-03-31.basil, Stripe creates the subscription only after payment completes, and "Failed payment attempts no longer result in an incomplete subscription with a finalized invoice" ([Stripe changelog](https://docs.stripe.com/changelog/basil/2025-03-31/checkout-legacy-subscription-upgrade)). So `incomplete` is only a defensive block.*
- **Already in place (no policy change):** synchronization logs `billing_multiple_entitling_subscriptions` when one user has more than one entitling subscription, and `docs/operations.md` tells operators to cancel and refund the duplicate.
- **Options:**
  - **(A)** Refuse checkout while any non-terminal subscription exists (`incomplete`, `past_due`, `unpaid`, `paused`), and send the user to the Customer Portal instead.
  - **(B)** (A) plus storing the open Checkout Session per user and expiring it before creating another, which closes the two-tab case.
  - **(C)** Keep BILL-07 as is and rely on detection and manual refunds.
- **Recommendation:** (B). Adopted by delegation.
- **Required evidence:** owner choice; sandbox verification of the chosen flow with Stripe test cards for asynchronous and authentication-required payments (DEC-006).
- **Resolution criteria:** BILL-07 updated, tests for each refused state and for the two-tab case. Done with mocks (`apps/api/tests/integration/test_billing.py`); the owner's approval and the sandbox run remain.
- **Interim rule:** option (B) as built (BILL-07). Detection (`billing_multiple_entitling_subscriptions`) and the operator procedure stay.

#### DEC-014 — The Diagnostics Operator Capability

- **Status:** OPEN — recorded 2026-10-30 by the cycle 1 review (finding `secarch-adm02-operator-approval`). Option (B) was adopted by delegation on 2026-10-31 ([ADR-0004 §15](adr/0004-cycle-1-adopted-decisions.md)): reversible, awaiting owner confirmation.
- **Owner:** Repository owner.
- **Blocks:** the owner's confirmation of the ADM-02 entry. Nothing else: the capability exists as described below.
- **Rationale:** ADM-02 requires a register entry and approval for any operator or support capability. The diagnostics tooling had neither. A signed-in user whose Clerk user ID is in `DIAGNOSTICS_OPERATOR_IDS` can read the integration configuration report and run authenticated checks against the configured providers (a Sentry test event, a PostHog event, Clerk and Stripe calls, the LLM provider). It is on by default outside production, can be switched on in production with `DIAGNOSTICS_ENABLED=true`, answers 404 to everyone else, is rate-limited, and audits every run and refusal. `POST /api/v1/llm/check` applies the same policy ([ADR-0004 §23](adr/0004-cycle-1-adopted-decisions.md)) and answers 404 to everyone else, but writes no audit entry. In `local` with no operators listed, any signed-in user is an operator. An operator is a listed account with a valid session: no second factor is required. It was introduced after a broken-access-control review ([evidence](evidence/2026-10-14-diagnostics-perf-local.md)). Its defaults were chosen during implementation and have not been approved by the owner.
- **Questions:**
  1. Should the capability exist in the template, and in which environments?
  2. Is a single factor enough for an operator outside `local`?
  3. Who may be listed, and where (Clerk user IDs differ per instance)?
- **Options:**
  - **(A)** Keep the current model and require a verified second factor for operators outside `APP_ENV=local`: expose the session token's `fva` claim from `ClerkTokenVerifier`, and answer 404 when the second factor is unverified or older than 60 minutes. The review notes that Clerk v2 session tokens carry `fva` by default ([session tokens](https://clerk.com/docs/guides/sessions/session-tokens); not re-read for this entry). Needs code and tests; not built.
  - **(B)** Keep the current model without the second-factor rule.
  - **(C)** Diagnostics only in `local` and `preview`.
- **Recommendation:** (B) for the template, with multi-factor authentication required at the identity provider for every listed operator. (A) is the stronger rule, but it cannot be exercised against a real Clerk instance here, and it would put MFA enrolment in front of the first preview check, which runs on the Integrations page. An adopter who enables diagnostics in production should choose (A).
- **Impact:** the diagnostics module, the identity verifier, their tests, and the operations guide.
- **Required evidence:** owner choice; for (A), tests for an unverified and a stale second factor.
- **Resolution criteria:** an ADR records the choice, and the ADM-02 row of §21 is updated.
- **Interim rule:** the current model, as built. The capability stays off in production unless `DIAGNOSTICS_ENABLED=true`.

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
- **ARCH-18** Composition fails loudly. A wiring mistake stops the application at startup or fails a test; it is never ignored:
  - an adapter override that no builder reads, and a settings keyword that names no setting, raise;
  - two probes for one integration raise. Modules contribute probes and analytics events through their `Contribution`, so adding either never edits the composition root;
  - no two settings classes read one environment variable, and `app/settings.py` calls every module's `*_problems` function;
  - layer imports are allowlisted per layer (`apps/api/tests/architecture/test_layer_imports.py`), so a package that no import-linter contract names cannot enter a domain, service, probe, or route.

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
- **API-07** A module declares its own error outcomes. A `DomainError` subclass declares `status`, a client error (4xx) or 503, or the class cannot be defined; the error handler maps it with no kernel change. A request transaction keeps its writes for such an error and rolls back for anything else. A 5xx `DomainError` is reported to the error tracker with its code, at most once a minute per cause per instance; `NOT_CONFIGURED` is reported at warning level, except a key or request that the provider refuses (a `ProviderRejectedError`, the same answer), which is reported at error level.
- **API-08** Every response conforms to the OpenAPI document. The committed document declares each operation's errors as `application/problem+json`, and a test session fails when any response differs from it in status, content type, or body. A test that makes a route raise an error it cannot raise is marked `contract_exempt`.

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
- **ENV-02** Each provider has an explicit integration mode: `mock`, `sandbox`, `live`, or `disabled` (off: no provider is called; analytics, error reporting, and the LLM check).
  - `mock` is the default for automated tests.
  - `live` is accepted only when `APP_ENV=production`; startup fails otherwise.
  - In `preview` and `production` every mode is set explicitly (ENV-09).
- **ENV-03** Each application has one centralized, typed configuration module.
  - It validates at startup which settings are required or optional per environment, and their formats.
  - It fails fast with a message that names the variable but never prints its value.
  - The frontend module separates public variables from server-only variables.
  - The API and web validators are held to one shared set of cases (`packages/contracts/config-cases.json`): a rule that one learns and the other lacks fails a test.
- **ENV-04** Key/mode consistency checks run at startup. For example, a live Stripe key outside production, or a test key in production live mode, refuses to start.
- **ENV-05** Production-targeting safeguards:
  - Development and test commands require `APP_ENV` to be `local` or `test`.
  - Database URLs must match an allowlist of local or designated test targets.
  - Destructive commands (reset, seed, migration rollback) also require an explicit confirmation flag and refuse to run otherwise.
- **ENV-06** `.env.example` lists every variable with its purpose, the environments where it applies, whether it is required or optional, and a safe placeholder.
  - Real `.env*` files are git-ignored.
  - Secret scanning runs in pre-commit and in CI.
- **ENV-07** A CI check scans built frontend assets for the values of server-only variables and for secret patterns. It compares values, not names: a variable's name may appear in SDK code that browsers never execute (`scripts/check-bundle-secrets.mjs`).
- **ENV-08** The application contains **no** test-only authentication path. Tests replace adapters through the composition root (`build_container(..., verifier=...)`) from test code only. If a test-only path is ever added, it:
  - is excluded from production builds;
  - refuses to activate unless `APP_ENV=test`;
  - is covered by a CI check that asserts it is absent from production artifacts.
- **ENV-09** Configuration is bound to the platform and to its origins. Messages name variables and enum labels, never values.
  - An unset `APP_ENV` on Vercel is taken from `VERCEL_ENV` (`development` is `local`). A contradicting value is refused: `production` on a preview or development deployment, `local` or `test` on a preview or production deployment, and (API) `local` in a deployed bundle. `preview` stays valid on a production deployment (a first deployment or a staging project). On Vercel the web build requires `APP_ENV` and `CLERK_SECRET_KEY`.
  - `APP_BASE_URL` is required in `preview` and `production`, by the API and by the web build. It is an http(s) origin and uses https in production. It is never derived from a `VERCEL_*` variable.
  - `CLERK_AUTHORIZED_PARTIES` entries are origins. Unset means the origin of `APP_BASE_URL`. When `APP_ENV` and `VERCEL_ENV` are both `preview`, the deployment's own origins are added to the list. The API and the web proxy compute the list with the same rules, and the web build inlines it, so a change needs a new deployment.
  - Only the repository-root `.env` is read. A `.env.local`, `.env.development*`, `.env.production*`, `.env.test*`, or `apps/web/.env*` file makes the web build fail, naming the file.

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
- **REPO-03** One documented set of commands is used identically locally and in CI: setup, dev, lint, format, typecheck, unit, integration, e2e, build, `db:migrate`, `db:seed`, `db:reset`, and the performance scripts. `pnpm verify` runs the checks of CI's api, web and secrets jobs, in CI's order (the Supabase image and the deployment configuration are `pnpm test:supabase` and `pnpm test:vercel-build`), and the CI jobs `api` and `web` run the same script (`node scripts/verify.mjs --job api` and `--job web`). A run in which no step passed ends NOTHING RAN with exit status 2, never PASS. The API tests require a database (`pytest --require-db`) instead of skipping.
- **REPO-04** Lockfiles are committed, and installs use them as they are: `pnpm install --frozen-lockfile`, and `uv sync --locked` (CI, `pnpm bootstrap`, `pnpm verify`), which fails when `pyproject.toml` has moved past `uv.lock`. Runtime versions are pinned in version files.
- **REPO-05** Database migrations are version-controlled (§8.3). Seed data is deterministic and synthetic.
- **REPO-06** A safe development reset procedure exists. It works only on local targets and is guarded by ENV-05.
- **REPO-07** A template initialization script:
  - replaces project-specific identifiers (name, package names, display strings);
  - generates a local `.env` from `.env.example` without copying any credential;
  - is idempotent.

  CI runs it in a temporary copy and asserts that no original identifiers remain.
- **REPO-08** A clean clone runs the full automated suite in mock mode without any external account. Running the app locally with real authentication requires only the adopter's own provider accounts, and the documentation says so. The web app shows no page without a real Clerk instance, so `pnpm dev` stops with one line asking for the two Clerk keys, while `pnpm build:check`, the tests, and `pnpm check:setup` need no account.
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
- **REPO-13** An initialized copy carries no template history. On its first run `pnpm init-template` removes the template's own records (`proofs/`, `docs-notes/`, and the files in `dev-docs/evidence/`), replaces `AGENTS.md` with an adopter version, keeps this specification and the ADRs as inherited reference under a banner, drops the template-only prose, and turns links to removed files into plain text, so `pnpm check:links` passes. A finished copy is never touched again, and `--keep-template-material` opts out.

---

## 7. Authentication, Authorization, and Tenancy

Clerk provides authentication. It does not replace application authorization.

### 7.1 Authentication

- **AUTH-01** Every protected request verifies the Clerk session token server-side:
  - the signature, using the configured public key or a cached JWKS;
  - `exp`, plus `nbf` or `iat` within a bounded clock skew;
  - `iss` matches the configured issuer;
  - `azp` is among the configured authorized origins;
  - `aud` includes `CLERK_AUDIENCE`, where it is set: a token whose `aud` (a string, or a list) lacks it, or has none, is refused. Unset: not checked. The API checks it; the web proxy does not.
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
- **TEN-02** Every protected backend operation invokes an explicit, deny-by-default authorization policy. A route-manifest test fails if any route lacks a declared authorization requirement. A route declares it with `requires(...)` (a permission, or an access kind for a route no permission governs). A declared permission that the request never evaluates answers 500 and is rolled back (fail closed).
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
- **ADM-03** A session that someone else started (a Clerk dashboard impersonation, a Backend API actor token, or an agent task: the token carries an `act` claim) is refused with `403` unless `CLERK_ALLOW_IMPERSONATION=true`. When it is allowed, every audit entry names the actor (`app.audit_log.impersonator_clerk_id`) and the request logs `actor_session`. Diagnostics answer `404` to such a session.

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
| NT-13 | A session started by someone else (the token carries an `act` claim) | `403` unless `CLERK_ALLOW_IMPERSONATION=true`; then the audit entry names the actor |

---

## 8. Data Model, Database, and Migrations

### 8.1 Logical data model (proposed; final after DEC-008, DEC-009, and DEC-010)

| Entity | Purpose | Integrity requirements |
|---|---|---|
| `users` | Internal user mapped to the Clerk user | Unique Clerk user ID |
| `organizations` | Organization tenant | Unique external organization ID (if DEC-009 option A) |
| `organization_memberships` | User ↔ organization with role | Unique (organization, user); role check constraint; foreign keys |
| `billing_customers` | User ↔ Stripe customer | Unique user; unique Stripe customer ID. The ID can be replaced after Stripe deleted the customer (BILL-04) |
| `subscriptions` | Synchronized Stripe subscription state for one user | Unique Stripe subscription ID; status check constraint; price, interval, current period end, cancel-at-period-end, last-synchronized marker. The customer ID it ended under stays on the row, with no reference to `billing_customers` (BILL-04) |
| `billing_events` | Webhook ledger for idempotency | Primary key is the Stripe event ID; type; object ID; received/processed timestamps; processing status. No full payload stored by default (DEC-004) |
| `billing_checkout_sessions` | The user's open Stripe Checkout Session (BILL-07) | One row per user (primary key, removed with the user); session ID, request key, expiry; no URL. Readable and writable by the API role only |
| `audit_log` | Administrative and security-relevant actions | Append-only for the application role. Names the impersonating actor when impersonation is allowed (ADM-03) |
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
- **DB-11** The API logs in as a role of its own, `app_login` (DEC-008, [ADR-0004 §3](adr/0004-cycle-1-adopted-decisions.md)): it owns nothing, has no `BYPASSRLS`, `CREATEROLE`, or `REPLICATION`, is `NOINHERIT`, and is a member of `app_api` and `app_worker` only. It has no `LOGIN` or password until `pnpm db:api-login` sets both. Migrations, seed, reset, and `api-login` use `MIGRATION_DATABASE_URL`, which the running API never reads.
  - In `preview` and `production`, a process whose login role is a superuser, has `BYPASSRLS` or `CREATEROLE`, is a member of such a role, or owns a table, view, or materialized view in schema `app` refuses every database transaction (a 503 that names the fix) and reports not ready.
  - Non-local hosts default to `sslmode=require`. That does not check the server's certificate, so it does not stop an active man-in-the-middle.
  - **Residual risk.** `app_login` can still `SET ROLE app_worker`, whose policies let every row through on several tables. Code that runs arbitrary SQL (an injection, raw driver use in an adopter's module) can therefore still read across tenants. Row-level security does not stop SQL injection. A second login role that is a member of `app_worker` only, with its own pool, would close the rest; it is not built.

### 8.3 Migration safety

- **MIG-01** Migrations are ordered, version-controlled, and immutable once merged. Shared environments only roll forward. Down migrations are optional and local-only. One recorded exception: D-003.
- **MIG-02** A destructive change requires:
  - explicit review;
  - an expand → migrate → contract plan where overlapping application versions are possible;
  - verified backups;
  - owner authorization before it runs against any shared environment.

  Destructive changes include drops, renames, type narrowing, and data rewrites.
- **MIG-03** CI applies all migrations to an empty database, runs schema linting, verifies that generated types and contracts are current, and runs the RLS tests.
- **MIG-04** A dedicated migration role runs migrations in a controlled deployment step. The application never runs migrations at request time. The role's connection is `MIGRATION_DATABASE_URL`, and the hosted step is the manually dispatched workflow `.github/workflows/db-migrate.yml`; nothing migrates on deploy.
- **MIG-05** No agent or automated process runs migrations or data deletion against non-local databases without owner authorization.

### 8.4 Backup and restore

- **DB-10** Backup and restore procedures are documented, and restoration is tested in an isolated environment with synthetic data. The template tests restoration locally (`pnpm db:restore-drill`). The procedure works for any target, including a Supabase project where the restoring role is not a superuser: `pg_restore --no-owner --single-transaction`, then `supabase/restore/post-restore.sql`. Recovery objectives and the hosted restore test are adopter-owned (DEC-005, ADR-0002; checklist in `docs/operations.md`).

---

## 9. Billing and Entitlements

### 9.1 Layers of truth

| Layer | Owner | Rule |
|---|---|---|
| 1. Stripe payment and subscription state | Stripe | The external source of truth for payment state |
| 2. Synchronized billing records | Application database | Updated only from verified Stripe data (webhooks, reconciliation, and a re-read the signed-in user requests for their own customer) |
| 3. Entitlement decision | Domain function | Pure function of user, context, billing records, and time |
| 4. Plan and billing presentation | Frontend | Renders server-provided state; decides nothing |

- **BILL-01** There is exactly one entitlement implementation: `decide(user, context, billing_snapshot, now)`. It returns the plan and the feature entitlements, and is unit-tested against the matrix in §9.5.
- **BILL-02** Pro access derives only from synchronized billing records that originate in verified Stripe data. It never derives from a client redirect, frontend state, or a client-supplied subscription status.

### 9.2 Configuration

- **BILL-03** The Pro monthly and Pro yearly price IDs come from server configuration, per environment.
  - Clients request a plan key (`pro_monthly` or `pro_yearly`), never a price ID.
  - The server validates the plan key against an allowlist.
  - Sandbox integration tests verify that the configured prices exist and match the key mode.
  - Pro is granted by the Pro product, also from server configuration (`STRIPE_PRODUCT_PRO`, required when billing is not mocked). A configured price, or any other price of that product that bills every month or every year, maps to `pro_monthly` or `pro_yearly`; any other price has no plan. Checkout still sells only the two configured prices. The `/integrations` Stripe check fails when a configured price belongs to another product ([ADR-0004 §16](adr/0004-cycle-1-adopted-decisions.md)).
- **BILL-23** The template never creates or changes the Customer Portal configuration (ADR-0001, DEC-010). It reads it (`describe_portal()`). The `/integrations` Stripe check also lists one page of subscriptions and fails (`permission_denied`) when the key cannot read them, because every synchronization would fail; it fails (`portal_mismatch`) when the default configuration is missing or inactive, payment-method update or invoice history is off, cancellation is off or not at period end, switching is off or lacks `price`, or the switchable prices are not exactly the two Pro prices; a key that cannot read portal configurations gives "not checked", not a failure. A rejected API key or a rejected create call is `503 NOT_CONFIGURED`, reported once at error level (details in one warning log line, `stripe_configuration_error`); a Checkout or portal URL outside `stripe.com` and the optional `STRIPE_CUSTOM_DOMAIN` is `503 NOT_CONFIGURED` with one ERROR log. `pnpm setup:stripe` creates only the Pro product and the missing prices, in a sandbox, and never writes `.env`.

### 9.3 Checkout and association

- **BILL-04** Each user has one Stripe customer, created server-side and stored under unique constraints. Stripe metadata carries the internal user ID only, with no additional personal data. When Stripe has deleted the customer, the user's next checkout replaces it: the worker points the user's link at a new customer (the Stripe idempotency key names the old one), unless a subscription of the old customer is not over, which is `409` with an ERROR log. Subscriptions that ended under the old customer keep its ID, so `subscriptions` has no reference to `billing_customers` ([ADR-0004 §20](adr/0004-cycle-1-adopted-decisions.md), migration `20261031000070`).
- **BILL-05** The server creates Checkout Sessions for the authenticated user only.
  - Sessions carry the internal user reference and that user's customer, and offer cards only (`allowed_payment_method_types`; since API version 2026-09-30.endive `payment_method_types` is removed from this call): Pro follows the subscription's status, and a delayed bank debit reads `active` before the funds arrive (docs/limitations.md).
  - Success and cancel URLs are built from the configured application base URL, never from client input.
- **BILL-06** The checkout success page shows a "confirming" state, polls the billing-status endpoint, and after 3 s asks the server once to re-read from Stripe (BILL-22). Redirect parameters never grant entitlement. *(Changed 2026-10-30. This is a billing behavior change that awaits the owner's approval, §23.)*
- **BILL-07** Checkout is refused while the user has any subscription that is not over, and the user is directed to plan management instead (DEC-013 option B, adopted by delegation: [ADR-0004 §2](adr/0004-cycle-1-adopted-decisions.md)). The states: an entitled subscription (`already_pro`); `unpaid` or `paused`, or `past_due` on a price outside the catalog (`payment_required`); `trialing`, or `active` on a price outside the catalog (`subscription_exists`); `incomplete` (`payment_pending`, defensive: no plan-management button). The refusal is decided from stored state with no Stripe call (`409 CONFLICT`).
  - Before a later checkout creates a session, the server expires the user's stored open session at Stripe and re-reads Stripe's own subscriptions; if one is not over, it refuses again. If the earlier session cannot be closed, no new one is created (`503`, and the stored row stays so a retry closes it).
  - The open session is stored per user (`billing_checkout_sessions`: session ID, request key, expiry; never the URL). A retry with the same `Idempotency-Key` returns its session while it is open.
- **BILL-22** The signed-in user can ask the server to re-read their own customer from Stripe (`POST /api/v1/billing/sync`), with the same lock and synchronization as a webhook. The request takes no input and never reads a session ID. It counts against the per-instance Stripe-call bound, never waits, never takes the last synchronization slot (webhooks and reconciliation keep one), and is limited per user (`BILLING_SYNCS_PER_USER_PER_HOUR`); no limit is shared by all users. A failure leaves the page polling.

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
- **BILL-12** The handled event set is documented and verified against current Stripe documentation during implementation. At minimum it covers checkout completion, subscription create/update/delete, and invoice paid/payment failed. Unhandled event types are acknowledged and counted. The event destination uses the **Snapshot** payload format: a correctly signed body that is not a snapshot event (a Thin event) is answered `400 INVALID_REQUEST` with a message that says to choose Snapshot, and is logged without its body.
- **BILL-13** Webhook processing completes well within platform and Stripe timeouts, measured in DEC-001 and P7. Not measured yet: PERF-01 to PERF-07 and the platform items of DEC-001 are NOT VERIFIED (§21). The Stripe client allows 10 s per attempt and two retries.
- **BILL-14** Concurrent and duplicate deliveries for the same subscription produce the correct final state. A parallel-delivery test proves this.

### 9.5 Subscription state → entitlement matrix

| Stripe state | Meaning | Entitlement | Notes |
|---|---|---|---|
| No subscription | Free user | Free | Default |
| `incomplete` | Initial payment not yet confirmed | Free | UI shows "processing" or "action required" |
| `incomplete_expired` | Initial payment never completed | Free | Terminal |
| `active` | Paid and current | Pro | — |
| `active` + `cancel_at_period_end` | Cancellation scheduled | Pro until `current_period_end` | UI shows "cancels on <date>"; reactivation allowed (BILL-17) |
| `trialing` | Trial period | Free | No trials (DEC-010, [ADR-0001](adr/0001-approved-recommendations.md)): not entitled, and the anomaly is logged (`billing_trialing_anomaly`) |
| `past_due` | Renewal payment failed; Stripe retrying | Pro until Stripe moves the subscription to `unpaid` or `canceled` | DEC-010 ([ADR-0001](adr/0001-approved-recommendations.md)). The UI shows a payment issue with a path to update the payment method. The Stripe Retries rule must cancel or mark unpaid, or a subscription left past due keeps Pro (setup step S6) |
| `unpaid` | Retries exhausted; subscription retained | Free | UI prompts for payment |
| `canceled` | Ended | Free | Re-subscribe through a new checkout |
| `paused` | Paused (for example, a trial ended without a payment method) | Free | — |
| Pending update (`pending_update` present) | A change awaits payment confirmation | Entitlement follows the current, not the pending, items | Stripe has no "pending" subscription status; "pending" refers to pending updates |
| `active` or `past_due` on a price no plan matches (BILL-03) | Paid, but not a Pro price of `STRIPE_PRODUCT_PRO` | Free | UI says the subscription could not be matched (`unrecognized`; `past_due`: payment required) and offers Manage billing, not Upgrade; `billing_unknown_price` is logged at ERROR |

### 9.6 Plan changes, cancellation, and reactivation

- **BILL-15** Free → Pro goes through Checkout. Monthly ↔ yearly switching follows DEC-010 (Customer Portal or API), including proration and effective dates. Entitlement always follows synchronized state.
- **BILL-16** The billing adapter supports end-of-period cancellation (`cancel_subscription_at_period_end`: Stripe's `cancel_at_period_end`). Immediate cancellation is not built.
  - End-of-period: Pro continues until `current_period_end`. The Customer Portal does it for the user. The API does it for a user the identity provider reports as deleted, because nobody can reach the portal after that: identity records `identity.user_deleted` with the revocation, and billing ends the user's subscriptions that are not over at the end of their period (`BILLING_ON_USER_DELETED`, default `cancel_at_period_end` outside mock mode, adopted by delegation and reversible: [ADR-0004 §21](adr/0004-cycle-1-adopted-decisions.md)). A subscription that cannot be ended is an ERROR naming the customer, and the outbox retries.
  - Immediate: Free after synchronization. Not built; it is an adopter decision together with refunds.
- **BILL-17** Reactivation:
  - Before period end, the scheduled cancellation is removed.
  - After a subscription is `canceled`, the user goes through a new checkout.
- **BILL-18** Renewal failure surfaces a payment-action-required state with a path to update the payment method. Recovery happens on a successful payment event.

### 9.7 Reconciliation

- **BILL-19** A reconciliation command compares billing records with Stripe for one user, or for all flagged or recently changed records.
  - It is idempotent.
  - The command defaults to dry-run and reports divergences. Applying corrections is explicit (`--apply`).
  - The scheduled job (`reconcile-billing`, a daily cron) applies Stripe's state when `BILLING_MODE` is `sandbox` or `live`, and only reports in `mock`; `BILLING_RECONCILE_APPLY` forces either. A lost `customer.subscription.deleted` would otherwise leave Pro in place until someone ran the command (adopted by delegation, reversible: [ADR-0004 §20](adr/0004-cycle-1-adopted-decisions.md)). It is tested through `/api/v1/jobs/reconcile-billing` with the setting on and off.
  - Scheduling it (for example with a platform cron) is part of DEC-001 verification and requires approval. The four crons are in the root `vercel.json`; that Vercel registers them and calls the routes is NOT VERIFIED (§21).

### 9.8 Presentation

- **BILL-20** A single billing-status endpoint feeds the paywall, settings, and navigation. The states are: Free, Pro (renews on <date>), Pro (cancels on <date>), payment issue, processing, and a subscription no plan matches (`unrecognized`: the user pays and Pro is not active; `past_due` on such a price is a payment issue). It carries `checkout_blocked` (BILL-07); the page offers Upgrade only when that is null.
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
- Sandbox integration tests use the Stripe CLI and test clocks for renewal, renewal failure, recovery, cancellation, and reactivation. They require DEC-006 sandbox authorization. The opt-in suite is `pnpm test:stripe-sandbox` (`apps/api/tests/sandbox/`, 5 tests): it collects and skips without a key, and **has not been run**. It does not cover retry exhaustion.

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
  - Real providers are off by default (`mock` mode). `disabled` is also supported in every environment: it needs no key or model and wires no provider. Production accepts `disabled` or `live`.
  - The connectivity check is authenticated, limited to diagnostics operators (`POST /api/v1/llm/check` applies the diagnostics policy and answers `404` to anyone else, so other accounts cannot spend the shared budget), rate-limited, and uses minimal tokens.
  - A global spending budget is enforced (DEC-011).
  - Provider-side spending limits are documented.
- **LLM-07** Prompts, responses, credentials, and personal data are not logged. Only metadata is recorded.
- **LLM-08** A deterministic mock adapter is used for automated tests.
- **LLM-09** Provider unavailability affects only explicitly dependent features. Readiness does not fail because of the LLM provider. The connectivity check is optional: with `LLM_MODE=disabled` it answers `200` with `error_code: not_configured`, and the diagnostics page skips it.
- **LLM-10** Configuration for enabling a real provider is documented. The provider choice is made in DEC-006.

---

## 11. Observability and Privacy

- **OBS-01** One telemetry module per application initializes the PostHog and Sentry SDKs. It is environment-aware:
  - development: disabled or local;
  - preview: non-production projects;
  - production: production projects.
- **OBS-02** Sentry configuration:
  - Personal-data sending is disabled by default.
  - A pre-send hook scrubs authorization headers, cookies, webhook signature headers, tokens in URLs, provider IDs in URL paths (replaced by `{id}`), and request bodies.
  - Request bodies are never captured.
  - Breadcrumbs are filtered.
  - Sample rates are set per environment, and release and environment are tagged.
  - The browser records no spans and sends no sampling decision, so the API samples a page's requests at `SENTRY_TRACES_SAMPLE_RATE`.
  - No call to another service carries a `sentry-trace` or `baggage` header: the API and the web server set an empty trace propagation list, and the browser's default is the page's own origin.
- **OBS-03** PostHog configuration:
  - Events and properties come from an allowlist: the template's own, plus the events a module registers at wiring time (`Contribution.analytics_events`; registration refuses a duplicate name, a property with a personal-data or credential word in its name, the name split on `_` (`user_email`, `first_name`, `ip_address`), and a name outside lower-case snake case). The browser enforces its allowlist in `before_send`, so a direct SDK call cannot send another event or property. `posthog-js` may be imported only from `apps/web/src/lib/analytics.ts`; the lint guard that enforces it takes effect with §23 next step 4.
  - Input autocapture is disabled or masked.
  - Session recording is disabled by default; enabling it requires approval.
  - Users are identified by internal ID, never by email.
  - Consent behavior is an adopter decision (DEC-004, ADR-0002; `docs/data-inventory.md` checklist item 8).
- **OBS-04** Backend logs are structured. They carry a request ID and pseudonymous internal user and tenant IDs (`user_id` and `org_id`, on every line of an authenticated request and as tags on error events; never an identity provider's IDs, except the `actor_session` line that names an impersonating actor), and never secrets, tokens, request bodies, or prompts.
- **OBS-05** A correlation ID is returned in responses and attached to error reports. The error state shows it to the user (`Reference: <request ID>`), and a browser report about a failed API call carries it as the `request_id` tag.
- **OBS-06** Telemetry is non-blocking: bounded buffers, timeouts, and graceful degradation. Authentication, billing, and authorization never depend on analytics or monitoring availability, as proven by tests with telemetry endpoints unavailable.
- **OBS-07** Telemetry volume is bounded through sampling, client-event rate limits, and error de-duplication.
- **OBS-08** Leakage tests:
  - Synthetic canary values are injected through requests and errors: fake keys, fake tokens, fake email addresses, prompt text.
  - Logs and mocked telemetry transports are captured.
  - The test asserts that the canaries are absent.
- **OBS-09** Telemetry retention and deletion behavior is documented. Retention follows each provider's project settings, and adopters choose the periods (DEC-004, ADR-0002).
- **OBS-10** Telemetry reaches the provider before an idle instance is frozen. A request that queued an error event or an analytics event hands a bounded flush to a thread after its response is sent: it waits at most 2 s (`TELEMETRY_FLUSH_SECONDS`), never changes the response, is single-flight, and runs outside the request-thread budget. Whether Vercel keeps the instance alive long enough is **NOT VERIFIED** (DEC-001); the sampled transaction of the request itself can still be lost.

---

## 12. Frontend Quality and Accessibility

- **FE-01** Layouts are responsive and markup is semantic. The proposed conformance target for template-provided UI is WCAG 2.2 Level AA. Automated checks cannot prove conformance, so a manual keyboard and screen-reader pass is required.
- **FE-02** The frontend holds no business logic, server secrets, or direct database or Stripe-secret access. If server-rendering code exists (DEC-007), it only calls the Python API. Lint rules enforce this. The recorded baseline renders no API data on the server: static route shells and data loaded in the browser ([ADR-0004 §6](adr/0004-cycle-1-adopted-decisions.md)). Under the Vercel Services topology the web service can read every project variable (T-22): FE-02 holds in the code, not at the environment level.
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
- **FE-15** Pages are default-deny. A page requires sign-in unless it is listed in `PUBLIC_PAGES` (`apps/web/src/lib/page-access.ts`), the one allowlist, and a path it does not match is not public. Two layers read the list: the proxy (`apps/web/src/proxy.ts`) on the server, and `PageGate` in the root layout in the browser, so protection does not depend on the proxy running everywhere the app is deployed (DEC-001). Making a page public also means listing it in `EXPECTED_PUBLIC_ROUTES` (`apps/web/src/lib/app-routes.testing.ts`); fitness tests fail when a page is public that was not meant to be. The layers are presentation (FE-05): the API authorizes every operation.
- **FE-16** Operator tools (the header's Integrations link, the home page's check button, the dashboard's LLM card) render only when `GET /api/v1/diagnostics/access` answers `available: true`. The endpoint answers every signed-in caller, writes no audit entry or counter, and shares one predicate with the integrations listing. The UI reads no Clerk role: a screen offers an action only when the list response's `can` allows it (ARCH-03; `apps/web/src/no-client-roles.test.ts`).

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
| T-12 | Injection (SQL, script) | 1, 2 | SEC-01, parameterized queries; DB-11 (partial: code that runs arbitrary SQL can still `SET ROLE app_worker`) | SAST, unsafe-input tests |
| T-13 | Resource exhaustion (payload size, request rate, unbounded queries) | 1, 2 | SEC-09, DB-04, §15.3 | Resilience and stress tests |
| T-14 | LLM cost abuse | 3 | LLM-03 to LLM-06 | LLM budget and rate tests |
| T-15 | Malicious or vulnerable dependency | 5 | SEC-04, SEC-07, SEC-10 | Dependency scan, license check |
| T-16 | CI secret exfiltration through untrusted pull requests | 5 | CI-SEC-01 to CI-SEC-04 | Workflow review checklist |
| T-17 | Accidental production targeting or destructive migration | 2, 6 | ENV-01, ENV-05, MIG-02, MIG-05 | Guard tests |
| T-18 | Test-only auth path reaches production | 5, 6 | ENV-08 | Production artifact check |
| T-19 | Open redirect through return or checkout URLs | 1 | AUTH-06, BILL-05, SEC-14 | Redirect tests |
| T-20 | Information disclosure through errors | 1 | API-02, SEC-08 | Error contract tests |
| T-21 | A provider-side actor (Clerk dashboard impersonation, a Backend API actor token, an agent task) acts as a user without attribution | 3 | ADM-01, ADM-02, ADM-03. Holders of `CLERK_SECRET_KEY` stay trusted: sign-in tokens carry no `act` claim | `test_impersonation.py`, NT-13 |
| T-22 | API secrets are readable by the web service's server processes, because one Vercel project gives every service every variable (topology B) | 5, 6 | FE-02 (code level), ENV-07 (value-based bundle scan with `--require-values`), DB-11 and the rule that `MIGRATION_DATABASE_URL` and `API_DB_PASSWORD` never go in the project | `pnpm test:vercel-build` (local); platform behavior **NOT VERIFIED** (DEC-001); the owner's acceptance is open ([ADR-0004 §1](adr/0004-cycle-1-adopted-decisions.md)) |

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
- **TEST-04** Every evidence record labels each result as `mocked`, `sandbox`, or `live`. A command run on one machine against local services (a local database, a container, a build) is labeled `local`, which is never `sandbox` or `live`. A mocked integration is never presented as proof of live connectivity.
- **TEST-05** Tests never generate uncontrolled high-volume traffic against real Stripe or LLM services.
- **TEST-06** The end-to-end suite has two tiers. The account-free tier (`pnpm test:e2e`) opens the public pages in Chromium with every non-local request aborted, so clerk-js never loads. It checks the pages, axe (WCAG 2.2 AA; Clerk-drawn controls are not scanned), keyboard order, the proxy redirect, and the sign-in-unavailable state, and it fails on an uncaught page error, a CSP violation, or a failing first-party response. The sandbox tier (`E2E_SANDBOX=1`, or `pnpm verify:sandbox`) signs in with a Clerk sign-in token and needs a development instance with Organizations set to Membership optional.

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
| Database round trip (client-side deadline) | Statement timeout + connect timeout (15 s) |
| LLM call timeout | 30 s |
| Retries for idempotent outbound calls | At most 2, exponential backoff with jitter |
| Per-instance LLM concurrency | Small fixed bound (set at P5) |
| Writes per signed-in user | 60 a minute (`WRITES_PER_USER_PER_MINUTE`), on routes with no limit of their own |
| Runs of one job started over HTTP | One at a time; at least 60 s between starts (`JOB_MIN_SPACING_SECONDS`) |

### 15.4 Approved performance targets

None in the template. Targets are adopter-owned (DEC-003, [ADR-0002](adr/0002-organization-entitlements-contacts-adopter-decisions.md)). Adopters propose and approve them with `perf/run-plan-template.md`, from their own preview baseline.

---

## 16. CI, Deployment, and Operations

### 16.1 CI pipeline

| Stage | Checks | Secrets |
|---|---|---|
| Static | Formatting, linting, TypeScript and Python strict type checking, import-boundary and cycle checks | None |
| Unit and contract | Unit tests; OpenAPI validity; generated-contract drift | None |
| Integration | Ephemeral PostgreSQL; migrations from empty; schema lint (squawk); RLS tests; API integration tests; the same database tests on Supabase's Postgres image (job `api-supabase`) | None |
| Build | Frontend and backend builds; client-bundle secret scan (ENV-07); production-artifact check (ENV-08) | None |
| Security | Dependency vulnerability scan; static analysis; secret scan; license check | None |
| End-to-end | Critical flows in mock mode | None |
| Deployment configuration | `vercel build` of the root `vercel.json` with the pinned CLI, and checks of the output (job `deploy-config`); no deployment | None |
| Sandbox integration | DEC-006 procedures | Sandbox secrets via protected environments; trusted refs only, with approval |
| Migration (manual) | `.github/workflows/db-migrate.yml`: `workflow_dispatch` only, from the default branch, for a protected GitHub Environment; `pnpm db:migrate --allow-remote` (MIG-04, MIG-05) | `MIGRATION_DATABASE_URL`, one per Environment |

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

- **D-003 (2026-10-30).** A one-time exception to MIG-01: the first migration (`20261014000001_core_schema.sql`) was edited in place. `GRANT ... TO CURRENT_USER` crashes Supabase images older than 17.6.1.152 (supautils before 3.2.3) when run as `postgres`, and a later migration cannot run after a first one that crashes.
  - **Risk:** a database that applied the old file reports `Migration 20261014000001_core_schema.sql was modified after being applied`.
  - **Mitigations:** nothing shared had applied it (pre-release); a local database is reset with `pnpm db:reset --yes` (ask the owner before resetting theirs); a test rejects `CURRENT_USER`, `SESSION_USER`, and `CURRENT_ROLE` as grantee in any migration, the module template, and the restore drill.

- **D-004 (2026-10-30).** Local PostgreSQL, and the CI database, is plain `postgres:17.6-alpine` (`pnpm db:up`), not the Supabase CLI that DEC-007's toolchain lists.
  - **Mitigation:** Supabase parity comes from `pnpm test:supabase` and the CI job `api-supabase`, which run the migrations and the database tests on Supabase's Postgres image (the tag is pinned in `scripts/test-supabase-image.mjs`). A hosted Supabase project has not been tested.

| Phase | Scope | Entry criteria | Exit evidence | Status (2026-10-30) |
|---|---|---|---|---|
| P0 | Repository inspection, specification consolidation, decision register, `.gitignore`, `AGENTS.md` | — | This revision committed locally; owner review | Done |
| P1 | DEC-001 proof (local, then authorized preview deployment) | Owner authorizes the Vercel project and preview deployment; non-production Supabase project; Stripe sandbox | `dev-docs/evidence/dec-001-vercel-python.md`; DEC-001 resolved or escalated | Local part done ([evidence](evidence/dec-001-vercel-python.md)): packaging, Services topology, Vercel's runtime shim, transaction pooling, database faults, and a cold-start baseline. It found and fixed three defects. Topology B (one project with Vercel Services) adopted by delegation on 2026-10-30 ([ADR-0004 §1](adr/0004-cycle-1-adopted-decisions.md)): root `vercel.json`, `pnpm test:vercel-build`, and the CI job `deploy-config` give local packaging evidence. The proxy under Services and the shared environment are NOT VERIFIED. Preview deployment BLOCKED: needs owner authorization |
| P2 | Scaffold: layout, toolchain, configuration validation, CI skeleton, README, `.env.example`, community files, template initialization | DEC-001, DEC-007 resolved; DEC-012 for license files | Gate A checks | Implemented locally (deviation D-001); community contacts are adopter placeholders (ADR-0002); `pnpm verify`, `pnpm check:setup`, `pnpm build:check`, and the adopter-copy policy of init-template (REPO-13) added 2026-10-30; remote CI never run |
| P3 | Identity, tenancy, schema, migrations, RLS, authorization, audit | DEC-008, DEC-009 resolved | AUTH, TEN, ADM, DB, MIG requirements; NT-01 to NT-09, NT-11 | Implemented; tests PASS (`local` database, mocked IdP). The API logs in as `app_login` (DB-11); hosted Supabase behavior NOT VERIFIED |
| P4 | Billing: checkout, webhooks, synchronization, entitlements, reconciliation, paywall API | DEC-010 resolved; DEC-002 for organization scope | BILL requirements; §9.10 tests; sandbox evidence (DEC-006) | Implemented, including the DEC-002 (b) organization truth table; tests PASS (mocked Stripe API). Added 2026-10-30: DEC-013 option B and the user re-read (BILL-07, BILL-22), the portal check, `pnpm setup:stripe`, and the opt-in sandbox suite; the suite and `setup:stripe` were NOT RUN; sandbox NOT VERIFIED |
| P5 | LLM port and adapter, observability, rate limiting | DEC-011 resolved; LLM provider selected (DEC-006) | LLM, OBS, SEC-09 requirements | Implemented; tests PASS (mocked); providers NOT VERIFIED |
| P6 | Frontend flows, paywall, error and loading states, accessibility | P3 to P5 APIs available | FE requirements; end-to-end and accessibility evidence | Implemented; unit tests PASS; the account-free browser tier (public pages, axe, keyboard, proxy redirect, sign-in-unavailable state) PASS (mocked: clerk-js blocked, Clerk-drawn controls not scanned); signed-in flows and their accessibility NOT VERIFIED (needs Clerk sandbox) |
| P7 | Resilience, security hardening, performance baseline and stress; propose targets | Authorized performance environment | Resilience and security evidence; performance reports; DEC-003 target proposal | Resilience and security tests done locally. Performance harness (`perf/`) covers PERF-01 to PERF-07, all recorded locally; PERF-03, PERF-04, and PERF-06 must be re-recorded with the harness corrected on 2026-10-15. Preview runs need the DEC-001 environment. Targets are adopter-owned (ADR-0002) |
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
- integration mode (`mocked`, `sandbox`, or `live`; a command run on one machine against local services is also labeled `local`, which is never `sandbox` or `live`);
- exact commands;
- a result summary;
- the artifact location;
- limitations.

Evidence records never contain secrets, tokens, or personal data.

---

## 21. Requirements Traceability Matrix

Status as of 2026-10-31. Evidence: [`dev-docs/evidence/2026-10-14-local-verification.md`](evidence/2026-10-14-local-verification.md), [`2026-10-14-diagnostics-perf-local.md`](evidence/2026-10-14-diagnostics-perf-local.md), [`2026-10-14-adr-0002-perf07-local.md`](evidence/2026-10-14-adr-0002-perf07-local.md), [`2026-10-14-adr-0003-modular-architecture.md`](evidence/2026-10-14-adr-0003-modular-architecture.md), [`dec-001-vercel-python.md`](evidence/dec-001-vercel-python.md), the two second-review records of 2026-10-15, [`2026-10-30-cycle-1-fix-round.md`](evidence/2026-10-30-cycle-1-fix-round.md), which holds the cycle 1 results by package (cited below as "cycle 1: <package>"), the merged-tree checks, and what was not run, and [`2026-10-31-round-1b-cycle-2-fix-round-2.md`](evidence/2026-10-31-round-1b-cycle-2-fix-round-2.md), which holds round 1b, the cycle 2 review with its findings by disposition, fix round 2, and the full `pnpm verify` runs of the merged trees.

`PASS` here means the check was **executed locally** with **mocked** external integrations and met its criteria. It is not sandbox or live verification (TEST-04). `PARTIAL` means part of the requirement is verified and the row names the rest. Where a row says `NOT RUN`, a test or command exists and was not executed. A test file named without a path is under `apps/api/tests` (API) or `apps/web/src` (web).

| Requirements | Summary | Verification | Gate | Blocked by | Status |
|---|---|---|---|---|---|
| ARCH-01, ARCH-02 | Layering, no cycles | `lint-imports` (8 contracts, including `exhaustive` module layers and acyclic modules); mutation-checked | B | — | PASS |
| ARCH-03 | The frontend renders server decisions and re-derives none | `can` equals the routes for a personal context, an organization admin, and an organization member, and follows a permission change (`test_project_capabilities.py`, and the generated module's test); the UI reads no Clerk role (`apps/web/src/no-client-roles.test.ts`); panels render `can` (`projects-panel.test.tsx`). Mutation: `PROJECT_DELETE` granted to `ALL_ROLES` flips the member's `can.delete` with no web change. [cycle 1: caps](evidence/2026-10-30-cycle-1-fix-round.md#caps) | B | — | PASS (tested; mocked and local) |
| ARCH-04 to ARCH-07, ARCH-09 to ARCH-12 | Adapters, no global state, bounded calls, ADRs | Review; LLM retry tests; timeouts configured; database round-trip deadline (`test_database_faults.py`); no vendor SDK outside an adapter is also enforced by `tests/architecture/test_layer_imports.py` | B | — | PASS (review; database deadline tested) |
| ARCH-08 | No N+1 queries | Query-count tests (project lists, billing status, organization entitlement); single-statement transaction setup; at most 8 statements per request; the membership refresh is 8 round trips for any number of memberships (it was 2N+6, 106 at N=50), measured by `test_query_counts.py`. [cycle 1: apix](evidence/2026-10-30-cycle-1-fix-round.md#apix) | B | — | PASS |
| ARCH-13 | Modular monolith boundaries | import-linter contracts; `tests/architecture` (api-only cross-module imports, module shape, unique permissions and event types); mutation-checked; `tests/architecture/test_layer_imports.py` allowlists the imports of each layer (mutation-checked: installed but unlisted packages injected into a service, the kernel core, a probe, and a router failed it while `lint-imports` stayed green). [cycle 1: apix](evidence/2026-10-30-cycle-1-fix-round.md#apix) | B | — | PASS |
| ARCH-14 | Transactions: one-statement setup, request scope, release, two pools, admission gate, nesting guard | `test_request_scope.py` (26 tests: also swallowed statement errors, `release()` inside a block, admission given back on `release()`), `test_query_counts.py`, `test_database_faults.py` (hung database, dead idle connections); PERF-07 and PERF-03 local (starvation fix measured); DEC-001 local proof through PgBouncer | B, D | — | PASS (local); platform behavior NOT VERIFIED (DEC-001) |
| ARCH-15 | Transactional outbox | `test_outbox_jobs.py` (recording, RLS, dispatch, retries, dead letters, leases, retention, billing event; crash-looping events dead-lettered, one lease per event, batched pruning) | B, C | — | PASS (local); after-response dispatch on Vercel NOT VERIFIED (D-002) |
| ARCH-16 | Scheduled jobs | `test_outbox_jobs.py` (secret auth, 404 when off, each job, registry and `vercel.json` parity, CLI); every job bounded per run (`test_billing.py` batch rotation, drain and prune budgets); `test_job_guard.py` (over HTTP a job runs once at a time and no sooner than `JOB_MIN_SPACING_SECONDS` after its last start, a failed run does not lock it, a lost lease expires, the CLI is not guarded; mutation-checked) | B, C, E | — | PASS (local); Vercel Cron NOT VERIFIED (DEC-001) |
| ARCH-17 | Module generator | `pnpm test:new-module` (CI step): on a copy with three generated modules it runs lint, types, import contracts and migration lint, the whole API suite (including the contract sweep the generator writes into, and the write-limit manifest) and the whole web lint, types, format and test suite; mutation-checked | A, B | — | PASS |
| ARCH-18 | Composition fails loudly | `tests/unit/test_bootstrap.py` (a misspelled override and an unknown settings keyword raise, each unknown name is reported); `tests/unit/test_diagnostics_service.py` (two probes for one integration are refused); `tests/architecture/test_settings.py` (no two settings classes read one variable; every `*_problems` function runs); `tests/integration/test_composition.py` (a probe contributed by any module is listed). Mutation-checked. [cycle 1: apix](evidence/2026-10-30-cycle-1-fix-round.md#apix) | B | — | PASS |
| DEC-001 (local proof) | Services routing, runtime shim, transaction pooling, faults, cold-start baseline | `proofs/dec-001/run.sh` at `966d3b3` ([evidence](evidence/dec-001-vercel-python.md)); not run since, although the template changed (the `app_login` login, the root `vercel.json`, the Services layout); `test_deployed_layout.py` | E | DEC-001 (preview deployment) | PASS at `966d3b3` (local); NOT RUN since; platform NOT VERIFIED |
| DEC-001 (local packaging) | The root `vercel.json` builds with the pinned CLI: services and routes, crons equal to the job registry, one region, no test files, Linux x86-64 libraries, size | `pnpm test:vercel-build` and the CI job `deploy-config` (13 checks; 11 planted defects caught; `local`); `test_vercel_config.py`. [cycle 1: deploy](evidence/2026-10-30-cycle-1-fix-round.md#deploy) | E | DEC-001 (preview deployment) | PASS (local; the CI job has not run remotely); platform NOT VERIFIED |
| DEC-001 (topology) | Topology B, one project with Vercel Services, adopted by delegation | The root `vercel.json`; `test_vercel_config.py`; `pnpm test:vercel-build`. Beta on all plans. NOT VERIFIED on the platform: the shared environment (T-22), whether the proxy runs inside a service, the Framework Preset, the crons (production only), the router's order, `VERCEL_REGION`, limits, Instant Rollback ([evidence](evidence/dec-001-vercel-python.md#nextjs-proxy-under-services-not-verified); [ADR-0004 §1](adr/0004-cycle-1-adopted-decisions.md)) | E | DEC-001 (preview deployment); the owner's confirmation | NOT VERIFIED (adopted by delegation, awaiting confirmation) |
| API-01 to API-06 | Versioning, problem details, pagination, bearer-only, idempotency, health | Contract and platform tests; account-free e2e | B | — | PASS |
| API-07 | Module-declared errors; 5xx reports | `test_error_taxonomy.py`, `tests/architecture/test_errors.py`, `test_server_error_reporting.py` (a captured Sentry transport: one scrubbed event with the request ID, once a minute per cause), `test_request_scope.py`. Mutation-checked. [cycle 1: apix](evidence/2026-10-30-cycle-1-fix-round.md#apix) | B, C | — | PASS (mocked transports) |
| API-08 | Responses conform to the OpenAPI document | `tests/openapi_contract.py` as a session-wide recorder; `test_contract_conformance.py` (every operation signed in, signed out, invalid, and with the identity provider down); `test_openapi_contract.py` (the checker and the document's policy). Eight edits of the committed document each failed the test that should. The check found two tests that make a route raise an error it cannot raise (`test_request_scope.py`, and the conflict case of `test_outbox_jobs.py`); both are marked `contract_exempt`. [cycle 1: test](evidence/2026-10-30-cycle-1-fix-round.md#test) | B | — | PASS (local) |
| ENV-01 | Separate credentials per environment | Operational. An automated guard binds `APP_ENV` to `VERCEL_ENV` (ENV-09), so local or test values are refused on Vercel and production values on a preview (`test_platform_config.py`, `env.server.test.ts`). [cycle 1: config](evidence/2026-10-30-cycle-1-fix-round.md#config) | E | DEC-006; the DEC-001 preview | NOT VERIFIED |
| ENV-02 to ENV-05 | Modes, validation, key/mode consistency, production guards | Config tests; migration guard tests (libpq `host`/`hostaddr`/`service` overrides, `APP_ENV` for seed and reset); web `env.ts`/`env.server.ts` tests; the shared cases in `packages/contracts/config-cases.json` (116 checks pass in each app). [cycle 1: config](evidence/2026-10-30-cycle-1-fix-round.md#config), [cycle 1: web2](evidence/2026-10-30-cycle-1-fix-round.md#web2) | A, C | — | PASS (2026-10-15 review fixes; cycle 1) |
| ENV-06, REPO-02 | `.env.example` lists every variable with its purpose, the environments where it applies, required or optional, and a safe placeholder | `test_config.py` (the file's variables load); `apps/web/src/lib/env-example.test.ts` (the shipped Clerk placeholder pair passes the web validators for a build and fails in production); the tag above each variable gives its app, kind, whether it is required, and the scopes it applies in; `apps/api/tests/unit/test_env_example.py` and `apps/web/src/lib/env-example-parity.test.ts` tie the file to `Settings` (aliases included), `SERVER_VARIABLES`, the `NEXT_PUBLIC_*` reads, what `next.config.ts` fixes at build time (the `baked` tag), and `SERVER_ONLY_NAMES`. Each kind of drift is run against an edited copy of the file and reported by variable name; both tests were also mutation-checked on the file itself. [ADR-0004 §18](adr/0004-cycle-1-adopted-decisions.md) | A, C | — | PASS (local) |
| ENV-07 | No server-only values in the bundle | `check:bundle-secrets` (plus negative test) (now also the passwords inside both database URLs and `API_DB_PASSWORD`, PostHog secret keys, the lines of a PEM `CLERK_JWT_KEY`, and `--require-values`, which fails a scan that compared nothing; the web service of `pnpm test:vercel-build` is built with generated canary values). [cycle 1: deploy](evidence/2026-10-30-cycle-1-fix-round.md#deploy) | C | — | PASS |
| ENV-08 | No test-only auth path in the application | Review | C | — | PASS (review) |
| ENV-09 | Platform binding, origins, one root `.env` | `test_platform_config.py` (the `VERCEL_ENV` x `APP_ENV` matrix, derivation, bundle), `test_deployed_layout.py`, `test_config.py`, `test_config_cases.py`; `env.server.test.ts`, `env-cases.test.ts`, `src/next-config.test.ts`, `env-files.test.ts`. 48 mutation reverts each failed their test. [cycle 1: config](evidence/2026-10-30-cycle-1-fix-round.md#config), [cycle 1: web2](evidence/2026-10-30-cycle-1-fix-round.md#web2) | A, C, E | The DEC-001 preview (real `VERCEL_ENV`, `VERCEL_URL`, `VERCEL_BRANCH_URL`) | PASS (local); the platform's inputs NOT VERIFIED |
| REPO-01, REPO-04 to REPO-08, REPO-10, REPO-12 | Docs, lockfiles, migrations, seed, reset, init (including contacts), mock-mode tests, no credentials | Clean clone (2026-10-14, see REPO-11); `test:init-template` (also a CI step); gitleaks history and tracked files; `pnpm build:check`, the tests, and `pnpm check:setup` need no account. [cycle 1: onboard](evidence/2026-10-30-cycle-1-fix-round.md#onboard) | A | — | PASS |
| REPO-03 | One command set, locally and in CI | `pnpm verify` (steps in CI's order; `scripts/tests/verify.test.mjs`); the CI jobs `api` and `web` run `node scripts/verify.mjs --job api` and `--job web`, and nothing else (`test_ci_workflows.py`); a run in which every step was skipped ends NOTHING RAN; `pytest --require-db` (`test_require_db.py`). [cycle 1: test](evidence/2026-10-30-cycle-1-fix-round.md#test) | A | Remote CI not authorized | PASS (local: every step ran, but not as one `pnpm verify` command); CI calling it NOT VERIFIED (never run remotely) |
| REPO-09 | Community files and policies | Files exist; contact placeholders filled by `init-template` and enforced by `check:placeholders` (CI). The recommended GitHub settings (branch protection, required checks, secret scanning push protection, private vulnerability reporting) are listed for the owner in `docs/setup.md`; none is applied, because applying them is a remote change. No `CODEOWNERS` file exists (SEC-12) | A | Remote settings not authorized | PASS for the files (ADR-0002); the settings NOT VERIFIED (not applied) |
| REPO-11 | Clean-clone onboarding | Fresh clone on 2026-10-14 (shared caches; see limitations). Cycle 1 changed the flow: the first build needs no Clerk key (`pnpm build:check`), `pnpm check:setup` is new, and `pnpm dev` stops until the two Clerk keys are pasted. Re-record the run (bootstrap, init-template, `pnpm db:up`, `pnpm db:migrate`, the tests, `pnpm build:check`, `pnpm check:setup`) with that one human step. [cycle 1: onboard](evidence/2026-10-30-cycle-1-fix-round.md#onboard). The cycle 2 review ran the new flow in a fresh clone at `dd12f44` (`init-template` 0.9 s, `bootstrap` 10.5 s, `db:up` 3.7 s, `db:migrate` 5.8 s, `check:setup` 8.7 s, `build:check` 33 s, `verify:quick` 6 min 1 s, `pnpm dev` stopping at the Clerk keys) and found each step as documented; the caches were warm, so the bootstrap time is not a clean-cache figure ([record](evidence/2026-10-31-round-1b-cycle-2-fix-round-2.md#cycle-2-review)) | A | An isolated, clean-cache run; the Clerk keys | PARTIAL (`local`, a reviewer's run, warm caches) |
| REPO-13 | An initialized copy carries no template history | `scripts/test-init-template.mjs`: removal, the neutral `AGENTS.md`, exactly one inherited-reference banner, template-only prose stripped, links resolve, no file names a path under a removed folder, `--keep-template-material` changes nothing, a finished copy is never touched again. Every assertion was mutation-checked. [cycle 1: onboard](evidence/2026-10-30-cycle-1-fix-round.md#onboard) | A | — | PASS (local) |
| AUTH-01 to AUTH-05 | Server-side token verification | Unit tests with generated keys (`test_clerk_verifier.py`: the required claims, the 5 s skew, the one-hour key-set lifetime, the pinned algorithm against HMAC confusion, and the optional `aud` check); end-to-end through the app; the web proxy applies the same `azp` list, and with `CLERK_JWT_KEY` set verifies without fetching Clerk's key set (`proxy.test.ts`, with real Clerk verification against a throwaway key pair). [cycle 1: web2](evidence/2026-10-30-cycle-1-fix-round.md#web2) | C | — | PASS (mocked IdP) |
| AUTH-06 | Session expiry UX | Silent refresh implemented and tested with mocks (`client.test.ts`: a 401 for a request that carried a token is retried once with a fresh token, under the same deadline). The sign-in step is a link the user clicks, so form state is kept (FE-04), not an automatic redirect: a deviation from AUTH-06's wording for the owner to confirm. [cycle 1: web](evidence/2026-10-30-cycle-1-fix-round.md#web) | B | DEC-006 | NOT VERIFIED (sandbox: the real Clerk flow) |
| TEN-01 to TEN-06, TEN-08 | Tenant context, deny-by-default, scoping, RLS, 404/403 | NT-01 to NT-04, NT-08, NT-09, route manifest, RLS tests; every route declares `requires(...)` (the manifest: `tests/integration/test_platform.py`), and a declared permission that is never evaluated fails closed (`tests/unit/test_authorization.py`). A deleted organization stays out of reach through each query that filters it, and a malformed organization identifier is answered like an unknown one without a provider read (`test_platform.py`, `test_project_write_rechecks.py`). [cycle 1: apix](evidence/2026-10-30-cycle-1-fix-round.md#apix) | C | — | PASS |
| TEN-07 | Membership change enforcement | NT-05 (bound, webhook, outage), NT-06 (re-check, serialization; a downgrade started in the middle of a project create or delete waits for it, through the service: `test_project_write_rechecks.py`) | C | — | PASS (300 s, confirmed in ADR-0002) |
| ADM-01 | Admin authorization and audit | NT-07; audit assertions on the whole entry (actor, organization, target, outcome, request ID: `test_api_security.py`); the impersonating actor is recorded when impersonation is allowed (`test_impersonation.py`); the access probe writes no audit entry (`test_diagnostics_access.py`) | C | — | PASS |
| ADM-02 | No super-admin bypass; an operator capability needs a register entry and approval | NT-07; review (no bypass in the code). The diagnostics operator allowlist is the operator capability, registered as DEC-014 | C | DEC-014 (owner confirmation) | PASS (local) for the absence of a bypass and for the register entry; option (B) is adopted by delegation, so the owner's confirmation is pending |
| ADM-03 | Actor sessions refused unless allowed, and audited | `test_impersonation.py`, `test_clerk_verifier.py`; mutation-checked (actor refusal off, actor not passed to the audit insert, `act` ignored, operator check without the actor). NT-13. [cycle 1: apix](evidence/2026-10-30-cycle-1-fix-round.md#apix) | C | — | PASS (mocked IdP); real Clerk actor tokens NOT VERIFIED |
| DB-01 to DB-09 | Integrity, indexes, transactions, bounds, pooling, RLS, isolation, fixtures | RLS and integration tests. Integrity: `test_constraints.py` inserts one violating row per CHECK, UNIQUE, FOREIGN KEY and NOT NULL constraint of the core tables and expects the constraint the migration names; dropping any constraint it names fails it (`local` sweep). Policies: `test_rls.py` runs INSERT, UPDATE and DELETE for callers in both organizations, with UPDATE and DELETE free of a `WHERE` clause so the SELECT policy cannot stand in for them. Privileges, read from the catalog: no runtime role holds TRUNCATE, TRIGGER or REFERENCES, PUBLIC holds nothing in `app`, `audit_log` is append-only, and the outbox grants only what requests and the worker need. Each is mutation-checked | B, C | — | PASS |
| DB-10 | Backup and restore | Local restore drill (restores with `pg_restore --no-owner --single-transaction`, then `supabase/restore/post-restore.sql`; fails when that file is emptied); the same procedure by hand on the Supabase image; adopter checklist and hosted restore-test procedure. [cycle 1: db](evidence/2026-10-30-cycle-1-fix-round.md#db) | E | — | PASS (local drill and documentation). Hosted restore is adopter-owned (ADR-0002) |
| DB-11 | Runtime login role `app_login` | `test_login_role.py`, `test_runtime_roles.py`, the assertion migrations `20261030000011` and `20261015000006`; `pnpm test:supabase` on the Supabase image (757 passed, 5 skipped, `local`); `test_database_tls.py`. Mutation-checked (the guard call removed: 4 tests; `app_login` created with `INHERIT`: 16 tests). Per-command policies on `projects` (`test_a_member_may_update_a_row_another_member_created`). The runtime check refuses a login that has `CREATEROLE`, or that is a member of a `BYPASSRLS` role (directly or through another role) or of a `CREATEROLE` role, each tested with a real role ([ADR-0004 §22](adr/0004-cycle-1-adopted-decisions.md)); `pnpm db:api-login` prints the Transaction pooler port 6543 for `*.pooler.supabase.com` (`test_database_connection.py`). [cycle 1: db](evidence/2026-10-30-cycle-1-fix-round.md#db) | B, C | A hosted Supabase project (DEC-006) | PASS (local); hosted behavior NOT VERIFIED. Residual risk: a query can still `SET ROLE app_worker` (DB-11) |
| MIG-01, MIG-02, MIG-05 | Migration safety | Checksum tamper test; non-local refusal; idempotent apply; the one-time in-place edit of migration 1 is deviation D-003, guarded by `test_migration_grants.py`; `.gitattributes` keeps migrations and shell scripts on LF in every checkout, because the checksum is over the file's bytes (`test_line_endings.py`). [cycle 1: db](evidence/2026-10-30-cycle-1-fix-round.md#db) | B, C | — | PASS, with the recorded exception D-003 |
| MIG-03 | Schema lint, empty-database apply, contracts, RLS tests in CI | squawk 2.70.0 over the migrations (`pnpm api:lint`, `.squawk.toml`, `test_migration_lint.py`); the CI jobs `api` and `api-supabase`; `tests/integration/test_maintenance_commands.py`, which runs `python -m app.cli migrate` twice against an empty database (a test, so `pnpm verify` runs it too; a CI step did it before). `CREATE INDEX CONCURRENTLY` cannot run in the transactional runner, so that rule is switched off and the manual procedure is documented. [cycle 1: db](evidence/2026-10-30-cycle-1-fix-round.md#db) | B, C | — | PASS (local); the CI stage has not run remotely |
| MIG-04 | A dedicated migration role in a controlled step | `MIGRATION_DATABASE_URL`; `.github/workflows/db-migrate.yml` (`workflow_dispatch` only, default branch, protected Environment; `test_ci_workflows.py`); its commands ran in a fresh archive copy against a scratch database (`local`). [cycle 1: deploy](evidence/2026-10-30-cycle-1-fix-round.md#deploy) | B, C, E | A hosted project; DEC-001; DEC-006 | PASS (a controlled step exists); the hosted run NOT VERIFIED |
| BILL-01 | One entitlement implementation returns the plan and the feature entitlements | Unit matrix (`test_entitlements.py`: the plan and the feature set for every row of §9.5). Both `/pro/insight` routes gate on `BillingService.require_feature` (`test_org_entitlements.py`, `test_billing.py`: a feature no plan lists is refused even to a Pro user). Mutation-checked: no features on a Pro decision, features on a Free one, a plan check in place of the feature check | B, C | — | PASS (mocked Stripe API). `decide()` returns the plan, the reason, the subscription, and `features`; no API response carries `features` yet |
| BILL-03 (Pro by product) | Pro is granted by `STRIPE_PRODUCT_PRO`; checkout sells only the two configured prices; the Stripe check names the other product of a configured price | `test_price_catalog.py` (the mapping table), `test_adapters.py` and `test_stripe_adapter.py` (Stripe payloads and the SDK's objects), `test_billing_settings.py` and `test_platform_config.py` (required, `prod_`, in sandbox and live), `test_diagnostics.py` and `test_diagnostics_adapters.py` (the check), `test_stripe_setup.py` (the printed line, one product), `test_billing.py` (`reconcile --apply` gives Pro back to a subscriber a price change dropped). Mutation-checked: the product match, the interval, the interval count, an unset product, the settings rules, the adapter's product and interval, the wiring, the check, the printed line. [ADR-0004 §16](adr/0004-cycle-1-adopted-decisions.md) | B, C | DEC-006 for the sandbox suite | PASS (mocked Stripe API; loopback stubs). The sandbox suite and `pnpm setup:stripe` NOT RUN; that Stripe accepts the custom product ID `prod_pro` NOT VERIFIED |
| BILL-02 to BILL-05, BILL-08 to BILL-11, BILL-14, BILL-15, BILL-17, BILL-18, BILL-19 (command and job), BILL-21 | Entitlements, checkout, webhooks, transitions, reconciliation, presentation | Unit matrix; billing integration tests (the pending update, the monthly to yearly switch, renewal recovery without a second entitlement event, the forged-webhook log without the body, and `reconcile --user` included); web unit tests | B, C | — | PASS (mocked Stripe API; real signature verification) |
| BILL-13 | Webhook processing completes well within platform and Stripe timeouts | None: PERF-01 to PERF-07 and the platform items of DEC-001 are NOT VERIFIED. The Stripe client allows 10 s per attempt and two retries (`StripeBillingProvider`) | D | DEC-001 (preview deployment); P7 | NOT VERIFIED |
| BILL-19 (scheduling) | The reconciliation job runs on a schedule | The cron is in the root `vercel.json` and equals the job registry (`pnpm test:vercel-build`, `test_outbox_jobs.py`); the job route is tested over HTTP with the setting on, off and unset (`test_billing.py`) | E | DEC-001 (a production deployment: previews never run crons) | NOT VERIFIED (Vercel Cron) |
| BILL-06, BILL-07, BILL-12, BILL-20, BILL-22 | Success-page re-read, checkout refusal (DEC-013 option B), Snapshot payloads, `checkout_blocked`, the user's re-read | `test_billing.py`, `test_stripe_adapter.py`, `test_entitlements.py`, `test_rls.py`; web `features/billing` tests. Mutation-checked: expire before create, live re-read, retry replay, failure to close, replaced-session cleanup, users never take the last slot, a re-read counts against the Stripe-call bound, no shared bucket. [cycle 1: stripe](evidence/2026-10-30-cycle-1-fix-round.md#stripe) | B, C | The owner's approval of the behavior change (§23); DEC-006 for the sandbox run | PASS (mocked Stripe API); sandbox NOT RUN |
| BILL-23 | Portal configuration verified read-only; a misconfigured Stripe is visible | `test_portal_policy.py`, `test_stripe_adapter.py`, `test_stripe_setup.py`. Field names come from the installed SDK's types and ran against loopback stubs only. [cycle 1: stripe](evidence/2026-10-30-cycle-1-fix-round.md#stripe) | B, E | DEC-006 | PASS (mocked); a real portal configuration, `setup:stripe`, and the restricted-key permission NOT VERIFIED |
| BILL-16 | End-of-period cancellation exists: the Customer Portal does it for the user, and the API does it for a user Clerk reports as deleted (their subscription is set to end at the period's end; identity does not import billing). Immediate cancellation is not built (the adopter's, with refunds) | `test_user_deleted.py` (the event recorded once with the revocation, none for an unknown user; the handler ends the subscription, leaves a scheduled one alone, retries and alerts on a failure, and only logs with `log`), `test_stripe_adapter.py` (the exact request, every key checked against the SDK's `SubscriptionUpdateParams`), `test_billing_settings.py` (the default by mode), `test_modules.py` (the cross-module rule). Mutation-checked: no event, no subscription, a repeat, a subscription that is over, a swallowed failure, the default | B, C | DEC-004 for the rest of deletion | PASS (mocked Stripe API; loopback stub). The real call is NOT RUN; Clerk's `delete_self` default and the Dashboard step are NOT VERIFIED |
| BILL-* sandbox | Real Stripe test-mode behavior (test clocks, portal) | `pnpm test:stripe-sandbox` (`apps/api/tests/sandbox/`, 5 tests: collects, and skips without a key); sandbox procedure in docs/integrations.md. Retry exhaustion is not covered. [cycle 1: stripe](evidence/2026-10-30-cycle-1-fix-round.md#stripe) | E | DEC-006 | NOT VERIFIED (the suite exists and has NOT been run) |
| BILL-ORG-01 | Interim organization gating | — | C | — | RETIRED (superseded by BILL-ORG-02, ADR-0002) |
| BILL-ORG-02 | Organization entitlement policy, DEC-002 (b) | Truth-table tests (`test_org_entitlements.py`); NT-12 | C | — | PASS |
| LLM-01 to LLM-10 | Port, normalization, bounded retries, limits, budget, privacy, mock, isolation, docs | Adapter and platform tests; the hosts, auth headers and API version each provider sends (`test_adapters.py`, `TestLLMProvidersOnTheWire`, against a stub transport: no provider was called); `disabled` mode (`test_llm_disabled.py`: no key or model, no provider, `200 not_configured` without the rate limiter); the operator policy (`test_llm_access.py`: the check answers where `GET /diagnostics/access` says available and `404` elsewhere, and a refused call uses no limit). [cycle 1: config](evidence/2026-10-30-cycle-1-fix-round.md#config) | B, C, D | — | PASS (mocked provider) |
| LLM real provider | Connectivity with a real key | Sandbox check | E | DEC-006 | NOT VERIFIED |
| OBS-01 to OBS-08, OBS-10 | Telemetry configuration, redaction, allowlists, non-blocking, leakage, flush after the response | Unit and leakage tests; outage tests; `test_telemetry_wire.py` (canaries absent from every payload a loopback stub received, in raw, URL-encoded, and JSON-escaped form, with a signed-in user's request, the real Clerk adapter against a stub provider, provider ID canaries, and no trace header at the provider); `test_request_telemetry.py` (OBS-04, OBS-05; with Sentry's default integrations on); `test_telemetry_flush.py` (OBS-10); `test_analytics_events.py` and the web analytics tests (OBS-03: registry and runtime allowlist, and every option of `posthog.init` and `Sentry.init` pinned in `analytics.test.tsx` and `sentry-config.test.ts`; OBS-07: `posthog.init` sets the client-side event limit, and `analytics-rate-limit.test.tsx` shows the real SDK enforcing it, [ADR-0004 §24](adr/0004-cycle-1-adopted-decisions.md)); `test_sentry_tracing.py` (provider IDs in paths); the web `privacy.test.ts`. Mutation-checked. [cycle 1: obs](evidence/2026-10-30-cycle-1-fix-round.md#obs) | C, D | — | PASS (mocked transports); OBS-10's platform half NOT VERIFIED (DEC-001); the `posthog-js` import guard waits for the lint wiring (§23 next step 4) |
| OBS real services | PostHog and Sentry capture without personal data | Sandbox inspection | E | DEC-006 | NOT VERIFIED |
| OBS-09 | Telemetry retention | Documentation; adopter checklist | E | — | PASS (documented; adopters set the periods) |
| FE-02, FE-03, FE-06, FE-10 | Boundaries, states, paywall, error mapping | ESLint bans; Vitest (an error code the web does not know shows the server's message for a 4xx and offers a retry for a 503: `client.test.ts`); account-free e2e | B | Owner applies the `eslint.config.mjs` change (§23) | PASS, except the stricter FE-02/ENV-07 lint guards (`eslint.guards.mjs`), which are written and tested but not yet enabled. Under Services, FE-02 holds in the code, not at the environment level (T-22) |
| FE-05, FE-07 | Protected-route presentation; 404 page | Proxy default deny (`page-access.test.ts`, `proxy.test.ts`: mocked); client page gate (`page-gate.test.tsx`, `layout.test.tsx`: mocked); account-free e2e (the 307 to this app's own sign-in page, and the public pages: mocked). A signed-in user sees the 404 page (`not-found.test.tsx`; in a browser: sandbox only); a signed-out visitor to an unknown path is sent to sign-in. [cycle 1: web](evidence/2026-10-30-cycle-1-fix-round.md#web), [cycle 1: web2](evidence/2026-10-30-cycle-1-fix-round.md#web2) | B | The DEC-001 preview; DEC-006 | PASS (mocked). Whether the proxy runs inside a Vercel service: NOT VERIFIED. The real redirect after Clerk loads: NOT RUN (sandbox). The API authorizes every call either way |
| FE-15 | Pages are default-deny, in two layers | `page-access.test.ts` (walks `src/app`; a page that is public and not expected fails; a second root layout fails), `proxy.test.ts`, `page-gate.test.tsx`, `layout.test.tsx` (renders the real layout around every route, signed out). Built output: the seven pages are still static. Mutation-checked. [cycle 1: web2](evidence/2026-10-30-cycle-1-fix-round.md#web2) | B | — | PASS (mocked: all gate tests mock Clerk) |
| FE-16 | Operator tools follow the API's answer | `test_diagnostics_access.py` (unit and integration: agreement with the integrations route for eight configurations, no audit row or counter, the body is exactly `{"available": bool}`); `diagnostics-access.test.tsx`; `no-client-roles.test.ts`. [cycle 1: web2](evidence/2026-10-30-cycle-1-fix-round.md#web2) | B, C | — | PASS |
| FE-01, FE-04, FE-08, FE-09, FE-11 | Accessibility, session UX, error boundaries, keyboard, browser flows | Account-free tier: axe (WCAG 2.2 AA) on the landing page, `/sign-in`, and `/sign-up`; keyboard order; the sign-in-unavailable state; desktop and Pixel 7 (`pages.spec.ts` and `http.spec.ts`: 56 passed, 14 skipped in all, with `authenticated.spec.ts`; `mocked`, clerk-js blocked). The 404 page's axe scan (WCAG 2 A and AA) runs in the sandbox tier only (`authenticated.spec.ts`): a signed-out visitor never sees that page. Error boundaries (FE-08): component tests of `error.tsx` and `global-error.tsx`. Every browser spec runs under the guard against an uncaught error, a CSP violation, and a failing request of the app's own (`support/browser.ts`; `e2e-support.test.ts` fails when a spec that opens a page does not use it). Authenticated suites: `authenticated.spec.ts` (sandbox). [cycle 1: test](evidence/2026-10-30-cycle-1-fix-round.md#test) | B | DEC-006 (Clerk sandbox) | NOT VERIFIED. The public pages PASS (mocked); the Clerk-drawn controls and the signed-in flows are NOT RUN |
| FE-13 | Feature folders and import boundaries | ESLint `import/no-restricted-paths` (`eslint.boundaries.mjs`); 23 tests lint real snippets through the real configuration; mutation-checked | B | — | PASS |
| FE-14 | Typed API client generated from OpenAPI | `tsc` with `@ts-expect-error` checks on paths and params; client tests | B | — | PASS |
| FE-12 | Frontend performance budgets | Measurements | D | Preview environment (DEC-001) | NOT VERIFIED (budgets adopter-owned; Core Web Vitals not measured) |
| SEC-01 to SEC-05, SEC-07, SEC-08, SEC-10, SEC-13 to SEC-15 | Validation, headers, secrets, audits, lockfiles, errors, licenses, webhooks, redirects | Tests; audits; gitleaks (CI: the pinned CLI, SHA-256 checked, over the history reachable from the commit; a pre-commit hook installed with `pnpm hooks:install`, which falls back to a pinned container image); the production dependency audit through npm's bulk advisory endpoint (`scripts/npm-audit-bulk.mjs`); license allowlist with reviewed exceptions; the whole CSP pinned token by token, for production and for development (`csp.test.ts`), with Clerk's required hosts named per directive, except its telemetry host, which the CSP leaves out because the root layout and `next.config.ts` turn the SDK's telemetry off (`layout.test.tsx`, `next-config.test.ts`, [ADR-0004 §29](adr/0004-cycle-1-adopted-decisions.md)), and `'unsafe-inline'` kept (nonce CSP deferred, [ADR-0004 §11](adr/0004-cycle-1-adopted-decisions.md)); the other headers, HSTS for the host only ([ADR-0004 §24](adr/0004-cycle-1-adopted-decisions.md)), and the policy built for the providers that are on (`next-config.test.ts`); all of it on the built app (`e2e/http.spec.ts`). [cycle 1: deploy](evidence/2026-10-30-cycle-1-fix-round.md#deploy), [cycle 1: web](evidence/2026-10-30-cycle-1-fix-round.md#web) | C | Owner reviews the license exceptions (§23) | PASS (as of 2026-10-30, `local`). The hook ran in a scratch repository (a clean commit passes, a planted key is refused); the CI scan has not run remotely |
| SEC-09 | Rate limiting (DEC-011) and resource-exhaustion controls | Per-user PostgreSQL counters for checkout, the portal, the billing re-read, LLM checks, diagnostics, and the membership miss refresh; a per-user write limit on the projects router and on every generated module (`WRITES_PER_USER_PER_MINUTE`; `test_write_limit.py` lists each write route as counted or exempt with its reason); one run at a time and a minimum spacing for a job started over HTTP (`test_job_guard.py`); payload, page, timeout, and concurrency bounds (§15.3); their tests. [ADR-0004 §17](adr/0004-cycle-1-adopted-decisions.md) | C | — | PASS (local). Known limits: the coarse per-IP rule is configured on the platform at deployment (DEC-011), not in code, and is not applied (`docs/limitations.md`); application limits count in fixed windows |
| SEC-06 | Static analysis (CodeQL) | Workflow defined | C | Remote CI not authorized | NOT VERIFIED |
| SEC-11 | Vulnerability reporting | SECURITY.md with an adopter placeholder enforced by `check:placeholders` | A | — | PASS (ADR-0002) |
| SEC-12 | Required review of migrations and workflows | CODEOWNERS and branch protection | A | Remote settings not authorized | NOT VERIFIED |
| TEST-01 | Tests are deterministic, with injected clocks and seeded randomness, and do not depend on wall-clock sleeps | Where code takes a clock, tests inject it. Counted at `1729bd0`: 34 `time.sleep(` calls in the API tests outside the sandbox suite, 19 `setTimeout(resolve` waits in the web tests, and 105 `perf_counter` or `monotonic` readings in 15 API test modules, some of which assert an elapsed-time bound (lock waits, concurrency, timeouts, flushes: there is no clock to inject). A loaded machine can trip them; fix round 2 gave several more room. The unchecked part is "no wall-clock sleeps" | B | — | PARTIAL (the sleeps and bounds above; TEST-02 applies to a flaky one) |
| TEST-02, TEST-04, TEST-05 | Test discipline | Review; labeled evidence; `pytest --require-db` instead of silent skips | B | — | PASS |
| TEST-03 | Exhaustive decision matrices; coverage reported | Review; the decision matrices are tested. `pnpm test:coverage` prints the coverage of the API (`pytest-cov`, with branches) and the web app (`@vitest/coverage-v8`) in the terminal and fails on nothing: a threshold is the adopter's decision ([ADR-0004 §19](adr/0004-cycle-1-adopted-decisions.md)). The command ran locally; no figure is recorded as evidence. [cycle 1: test](evidence/2026-10-30-cycle-1-fix-round.md#test) | B | A threshold (adopter); a recorded run | PARTIAL |
| TEST-06 | Two end-to-end tiers | Account-free: 56 passed, 14 skipped (`mocked`; the final run, committed state, `--retries=0`); sandbox: `pnpm verify:sandbox` with no `.env` skips every part, exits 0, and prints a record that says NOT VERIFIED. Mutation-checked, including the guards for a CSP violation, an uncaught error, and a skipped handshake. [cycle 1: test](evidence/2026-10-30-cycle-1-fix-round.md#test) | B, E | DEC-006 | PASS (mocked) for the account-free tier; the sandbox tier NOT RUN |
| PERF-01 to PERF-07 | Performance scenarios | Reports per §15.2 | D | Preview environment (DEC-001) | NOT VERIFIED (all seven recorded **locally** for relative comparison only; PERF-03, PERF-04, and PERF-06 to be re-recorded with the corrected harness; no preview runs) |
| Performance acceptance | Approved targets met | §15.4 | D | — | RETIRED for the template: adopter-owned (ADR-0002) |
| CI-SEC-01 to CI-SEC-05 | CI security | Workflow review; `apps/api/tests/unit/test_ci_workflows.py` (every action pinned to a commit, no secrets and no `pull_request_target` in CI, the migration workflow is manual with least privilege and one secret); first remote run. [cycle 1: deploy](evidence/2026-10-30-cycle-1-fix-round.md#deploy) | A, C | Remote CI not authorized | NOT VERIFIED (implemented) |
| OPS-01 to OPS-03, §16.4, §16.5 | Deployment, rollback, operations | `docs/deployment.md` and `docs/operations.md`, written for topology B; nothing was deployed | E | DEC-001 (preview deployment; the owner's confirmation of B) | NOT VERIFIED (documented for the adopted topology; no procedure has run on the platform) |
| §16.1 Deployment configuration | Validate deployment configuration and build output locally; no deployment | `pnpm test:vercel-build` and the CI job `deploy-config`: `vercel build` of the root `vercel.json` with the pinned CLI, 13 checks, 11 planted defects caught; `test_vercel_config.py`. [cycle 1: deploy](evidence/2026-10-30-cycle-1-fix-round.md#deploy) | A, E | Remote CI not authorized | PASS (local packaging evidence, TEST-04); platform NOT VERIFIED (DEC-001) |
| Account deletion and export | Deletion and export behavior | — | E | — | RETIRED for the template: adopter-owned (ADR-0002); no feature ships |
| Integration diagnostics | Per-provider configuration report and checks; operator-only; no secrets; the command-line equivalent | Diagnostics integration, unit, and adapter tests; `GET /api/v1/diagnostics/access`; `pnpm check:setup` and `diagnose` (`test_doctor.py`, `test_diagnostics_cli.py`, `scripts/tests/preflight.test.mjs`); the Stripe portal check (BILL-23); `pnpm verify:sandbox` (`scripts/tests/verify-sandbox.test.mjs`). [cycle 1: onboard](evidence/2026-10-30-cycle-1-fix-round.md#onboard) | C, E | — | PASS (mocked providers); no command ran against a provider |
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

## 23. Current Status (2026-10-31)

- **Implemented:**
  - **Python API as a modular monolith** (ADR-0003):
    - a shared kernel, and the modules identity, billing, projects, llm, and diagnostics, all with identical layers;
    - request-scoped transactions with separate request and worker pools, a client-side deadline on every database round trip, and transparent replacement of connections that died while idle;
    - a transactional outbox with after-response dispatch;
    - scheduled jobs (CLI and secret-protected HTTP for Vercel Cron);
    - composed per-module settings;
    - migrations with forced RLS: core schema, outbox, billing reconciliation, membership verification, the outbox lease index, the `app_login` runtime role and its assertion, per-command project policies, the open Checkout Session per user, the impersonating actor on audit entries, the per-job run guard (`job_runs`), and the relink of a deleted Stripe customer (the subscriptions table no longer references the customer link).
  - **Cycle 1 additions** ([record](evidence/2026-10-30-cycle-1-fix-round.md); decisions in [ADR-0004](adr/0004-cycle-1-adopted-decisions.md)):
    - the API logs in as `app_login`, never the table owner, and refuses an unsafe login role in preview and production (DB-11);
    - module-declared errors, 5xx reports, composition that fails loudly, and route authorization declarations that fail closed (API-07, ARCH-18, TEN-02); sessions started by someone else are refused unless allowed (ADM-03);
    - billing: DEC-013 option B, the user's own re-read, the read-only portal check, `pnpm setup:stripe`, and the opt-in sandbox suite (BILL-06, BILL-07, BILL-22, BILL-23);
    - configuration bound to the platform and to its origins (ENV-09), and `LLM_MODE=disabled`;
    - telemetry: internal user and organization IDs on logs and error events, a runtime analytics allowlist, module-owned events, and a flush after the response (OBS-03 to OBS-05, OBS-10).
  - **Round 1b and fix round 2 additions** ([record](evidence/2026-10-31-round-1b-cycle-2-fix-round-2.md); decisions in [ADR-0004 §16 to §26](adr/0004-cycle-1-adopted-decisions.md)):
    - billing: Pro granted by the Stripe product (BILL-03), Checkout offers cards only (BILL-05), a deleted Stripe customer is replaced at the next checkout (BILL-04), a deleted Clerk user's subscription is set to end at the period's end (BILL-16), the scheduled job applies Stripe's state outside mock mode (BILL-19), a paying subscription no plan matches is shown as such (BILL-20), and a rejected Stripe key is reported at error level (BILL-23);
    - identity and database: the login-role guard also refuses membership in a `CREATEROLE` role (DB-11), `db:api-login` prints the transaction pooler port, an optional `CLERK_AUDIENCE` (AUTH-01), migrations and shell scripts stay on LF (MIG-01), and `POST /api/v1/llm/check` follows the diagnostics operator policy (LLM-06);
    - web: same-origin credentials on API calls so a protected preview answers, HSTS for the host only and the CSP and headers pinned token by token (SEC-02), PostHog's client-side limit written out (OBS-07);
    - telemetry privacy: provider IDs replaced in the URLs Sentry gets, no trace header to a provider, analytics property names refused by word, no commit data in the Sentry build (OBS-02, OBS-03);
    - tooling: the lock is asserted (`uv sync --locked`), `pnpm verify` ends NOTHING RAN when no step passed, `pnpm build:check` reads no `.env`, the generator extends the contract sweep and is checked on the whole suite (ARCH-17), the link check resolves `#fragments`;
    - tests that pin existing behavior: grants, policies, constraints, the token verifier's bounds, provider hosts and headers, production guards, CORS, billing transitions and concurrency, tenant keys and the paywall, error boundaries. Each was mutation-checked.
  - **Next.js web app:**
    - code organized by feature (`src/features/*`, `src/ui`, `src/lib`), with the boundaries enforced by ESLint (FE-13);
    - a typed client generated from OpenAPI (`openapi-fetch`);
    - a minimal UI for every integration, including the `/integrations` page, shown only to a caller the API says may use it (FE-16);
    - pages default-deny in two layers (FE-15), and panels that offer only what the API's `can` allows (ARCH-03).
  - **Developer experience:**
    - `pnpm new:module` generates a tenant-scoped module end to end, including its `can` flags, and CI verifies the generator;
    - `pnpm verify`, `pnpm verify:quick`, `pnpm verify:sandbox`, `pnpm check:setup`, `pnpm build:check`, and `pnpm setup:stripe`;
    - `docs/architecture.md`;
    - init-template with contact placeholders and the adopter-copy policy (REPO-13).
  - **Deployment:** the root `vercel.json` (topology B, adopted by delegation), `pnpm test:vercel-build` and the CI job `deploy-config`, a manually dispatched migration workflow, and the pinned secret scan.
  - **Deployment proof (DEC-001, local part):** `proofs/dec-001` packages the template with the Vercel CLI and runs it through Vercel's runtime shim, Services routing, and a transaction-mode pooler, without deploying. It found three defects, all fixed: the API could not start from a bundle, a hung database was unbounded, and stale pooled connections failed requests after a pooler restart. It last passed at `966d3b3` and has not been run since (NOT RUN).
  - **Product decisions:** DEC-002 (b) organization entitlements; adopter checklists for DEC-003 to DEC-005; DEC-013 option (B) by delegation.
  - **Verification tooling:**
    - 8 import contracts plus architecture tests, including a per-layer import allowlist;
    - query-count tests, and a recorder that checks every test response against the OpenAPI document;
    - the telemetry wire harness;
    - the account-free browser tier;
    - the local performance harness, PERF-01 to PERF-07;
    - the local restore drill.
- **Integrations:** all **mocked**. No sandbox or live service has been contacted.
- **Local evidence:**
  - The latest record is [`2026-10-31-round-1b-cycle-2-fix-round-2.md`](evidence/2026-10-31-round-1b-cycle-2-fix-round-2.md): round 1b, the cycle 2 review and its findings by disposition, fix round 2, the full `pnpm verify` runs of the merged trees (`dd12f44`: 22 of 23 steps passed, and the one failure was fixed and rerun; `ecfd9d8`: 21 of 23, the two failures a merge seam fixed at `1729bd0`), and everything not run. Its counts are each from one tree and do not add up to one run.
  - [`2026-10-30-cycle-1-fix-round.md`](evidence/2026-10-30-cycle-1-fix-round.md): the results of each cycle 1 package, the checks run on that merged tree, and everything that was not run.
  - The earlier records: [`2026-10-15-second-review-identity-db.md`](evidence/2026-10-15-second-review-identity-db.md), [`2026-10-15-second-review-billing-web-tooling.md`](evidence/2026-10-15-second-review-billing-web-tooling.md), and, for the first review's fixes, [`2026-10-15-review-fixes.md`](evidence/2026-10-15-review-fixes.md).
  - The DEC-001 local proof passes ([`dec-001-vercel-python.md`](evidence/dec-001-vercel-python.md)); packaging is also asserted by `pnpm test:vercel-build` (13 checks).
  - At 20 ms DB RTT (local), request scope cut p50 latency by 25% to 33% beyond the one-statement setup.

  Details: [`dec-001-vercel-python.md`](evidence/dec-001-vercel-python.md), [`2026-10-14-adr-0003-modular-architecture.md`](evidence/2026-10-14-adr-0003-modular-architecture.md), and the earlier records.
- **Gates:**

  | Gate | Status | Reason |
  |---|---|---|
  | A | NOT VERIFIED | Remote CI has never run (needs authorization to push). The clean-clone run (REPO-11) is PARTIAL: a reviewer's fresh-clone run with warm caches is recorded, a clean-cache run is not |
  | B | NOT VERIFIED | Signed-in end-to-end tests need Clerk sandbox keys (DEC-006). The account-free browser tier passes (mocked) |
  | C | NOT VERIFIED | CodeQL (remote CI) and sandbox verification pending; local security tests PASS; DEC-014 option (B) awaits the owner's confirmation |
  | D | NOT VERIFIED | Local PERF-01 to PERF-07 recorded; PERF-03, PERF-04, and PERF-06 must be re-recorded (the earlier figures were whole-run averages, and PERF-06 could not detect reprocessing). Preview runs need DEC-001. Targets are adopter-owned (ADR-0002) |
  | E | BLOCKED | DEC-001 (preview deployment; topology B is adopted by delegation, and the proxy under Services and the shared environment are NOT VERIFIED), DEC-006 (integration classification) |

- **Decisions:**
  - DEC-002 to DEC-012: RESOLVED (ADR-0001, ADR-0002). DEC-003 to DEC-005 are adopter-owned.
  - Architecture: ADR-0003. ADR-0004 records the decisions that agents adopted by delegation in cycle 1, round 1b and fix round 2 (§1 to §26).
  - **Open:** DEC-001 (topology B adopted by delegation; the preview deployment and the owner's confirmation are pending), DEC-006, DEC-013 (option (B) adopted by delegation; confirmation and the sandbox run pending), and DEC-014 (the diagnostics operator capability; option (B) adopted by delegation, confirmation pending).
  - **Awaiting owner approval:**
    - granting Pro by the Stripe product (`STRIPE_PRODUCT_PRO`, in place of the retained-price settings; a billing-related choice, §17 rule 5; [ADR-0004 §16](adr/0004-cycle-1-adopted-decisions.md)), and the defaults and choices listed under "Not fixed" in both second-review records;
    - the billing behavior change of BILL-06: the success page, the portal return, and a refused checkout ask the server to re-read from Stripe once (BILL-22);
    - the billing behavior changes of fix round 2 (§17 rule 5; [ADR-0004 §20 and §21](adr/0004-cycle-1-adopted-decisions.md)): the scheduled job applies Stripe's state outside mock mode (BILL-19), Checkout offers cards only (BILL-05), a deleted Stripe customer is replaced at the user's next checkout (BILL-04), and a deleted user's subscription is set to end at the period's end (BILL-16);
    - the other decisions of fix round 2 ([ADR-0004 §22 to §26](adr/0004-cycle-1-adopted-decisions.md)): the login-role guard also refuses membership in a `CREATEROLE` role and `db:api-login` prints port 6543 (§22); the LLM check is an operator tool and `CLERK_AUDIENCE` can pin the token audience (§23); HSTS covers the host only and PostHog's client limit is written out (§24); the second telemetry privacy pass (§25); the tooling gates (§26);
    - the other decisions of ADR-0004, including the departure from FE-02 at the environment level under Services, and the AUTH-06 wording deviation (a sign-in link the user clicks, not an automatic redirect).
- **Deviations:** D-001 to D-004 (§18).
- **Remote actions:** none. No push, publication, deployment, or release has occurred. One disclosed accident: during a manual run, the real Stripe CLI ran once with a synthetic, invalid key and Stripe rejected it; nothing was created or changed.
- **Known limitations and deferred work** (cycle 1 and fix round 2; the reasons are in the evidence records and `docs/limitations.md`):
  - Eleven review findings were deferred: provider-call bulkheads at the ports, generated modules that copy security code, oversized units, untyped outbox events, a per-feature web data layer, a forms foundation, a test kit for adopters, PostHog organization groups, online index builds, a Clerk organization-lifecycle sandbox test, and supply-chain gates that need a pnpm major upgrade.
  - SEC-09: the coarse per-IP rule is configured on the platform at deployment (step D7), not in code, and is not applied.
  - DB-11: a query can still `SET ROLE app_worker`; row-level security does not stop SQL injection.
  - The CSP keeps `'unsafe-inline'`, with no nonce and no violation reporting; there is no first-party telemetry relay ([ADR-0004 §11](adr/0004-cycle-1-adopted-decisions.md)).
  - Plans are hard-wired as Free and Pro. Pro is granted by the Stripe product, so every monthly or yearly price of it grants Pro, whatever it charges (BILL-03). `decide()` returns the feature set, and no API response carries it (BILL-01).
  - Coverage is reported (`pnpm test:coverage`) with no threshold, and no figure is recorded as evidence (TEST-03).
  - Billing: Checkout offers cards only, so a customer cannot pay by a bank debit, and whether wallets and Link behave as cards under the filter is NOT VERIFIED; refunds and disputes do not change Pro; a deleted user's subscription ends only when Clerk's webhook arrives; a replacement customer that loses a race leaves one unlinked customer in Stripe. Four Stripe calls have only met stubs (NOT RUN): card-only Checkout, retrieving a deleted customer, `subscriptions.list(limit=1)`, and `cancel_at_period_end`.
  - Operator runs of `POST /api/v1/llm/check` are not audited (ADM-01); HSTS covers the host only; the PostHog SDK's rate-limit warning is dropped by the allowlist; Sentry events from the API still carry `server_name` and `extra.sys.argv`; an ID of fewer than ten characters after its prefix is not replaced in Sentry paths.
  - TEST-01 is PARTIAL: the tests still wait in real time in places. `CLERK_AUDIENCE` and a custom `aud` claim, Clerk's `delete_self` default, and the Deployment Protection cookie on API calls of a protected preview are NOT VERIFIED.
- **Next steps (owner action required):**
  1. Create a Clerk development instance with Organizations enabled and **Membership optional**, decide whether users may delete their own account (setup step S12), add the sandbox keys to `.env`, and put your Clerk user ID in `DIAGNOSTICS_OPERATOR_IDS` (or use `APP_ENV=local`). In the Stripe Dashboard (test mode) save the Customer Portal, turn off bank debits and bank transfers under Settings > Payment methods, and set the Retries rule (steps S5 and S6). Run `pnpm check:setup`, then `pnpm verify:sandbox`, which runs the setup check, the Stripe sandbox suite (after `pnpm setup:stripe`), and the signed-in end-to-end tests, each when its keys are present. The first sandbox run settles the NOT RUN list: the sandbox suite, the four Stripe calls that only stubs have answered, `pnpm setup:stripe`, and the signed-in flows. Save the record it prints under `dev-docs/evidence/` (DEC-006).
  2. Authorize pushing to a remote, so CI, CodeQL, the generator check, and the `deploy-config` job run (Gates A and C).
  3. Authorize the DEC-001 Vercel preview deployment, follow the first-preview order in [`docs/deployment.md`](../docs/deployment.md#the-first-preview-in-order) (a first pass with billing mocked, then the webhook endpoints at the real URL), run the [two-minute check](../proofs/dec-001/README.md#first-preview-the-two-minute-check), and confirm or reverse topology B ([ADR-0004 §1](adr/0004-cycle-1-adopted-decisions.md), and the [evidence](evidence/dec-001-vercel-python.md#topology-options-input-for-the-owner-not-a-decision)). The preview covers what only the platform can show (Gates D and E):
     - whether the Next.js proxy runs inside a service, whether the Services Framework Preset is required, and which variables the web service can read;
     - whether a protected preview lets the app's own API calls through (the dashboard lists projects);
     - cold starts and limits;
     - the production router;
     - Supabase's pooler;
     - genuine Stripe deliveries;
     - Vercel Cron (it runs on production deployments only, so a preview cannot verify it);
     - after-response dispatch and the telemetry flush;
     - preview performance runs.
  4. Enable the stricter web lint guards: in `apps/web/eslint.config.mjs`, spread `guardConfigs(import.meta.dirname)` from `eslint.guards.mjs` and remove the inline env and import rules, then remove `.skip` from the wiring test in `src/boundaries.test.ts`. Agents are not permitted to edit lint configuration in this repository. The same step turns on the `posthog-js` import guard (OBS-03).
  5. Review the license exceptions in `scripts/check-licenses.mjs`: the Sentry CLI (`sentry`, FSL-1.1-Apache-2.0, build time only) and the weak-copyleft packages (psycopg, certifi, libvips, tzdata).
  6. Confirm or reverse DEC-013 option (B), and approve the BILL-06 behavior change.
  7. Confirm or reverse granting Pro by the Stripe product, and confirm the defaults listed under "Not fixed" in the two second-review records (for example: outbox lease 900 s, migration lock timeout 5 s, `DATABASE_POOL_MAX_SIZE` at most 30, a domain `503` keeps the request's writes). The cycle 1 review offered two options for the first; option (B) was adopted by delegation and built ([ADR-0004 §16](adr/0004-cycle-1-adopted-decisions.md)): any monthly or yearly price of the product `STRIPE_PRODUCT_PRO` grants Pro, and the retained-price settings are deleted. Set `STRIPE_PRODUCT_PRO` (`prod_...`) in every environment that uses Stripe; `pnpm setup:stripe` prints it for a sandbox. Option (A) would list replaced prices in settings again (§16 gives the cost of reversing). If a price was replaced before the product was set, `pnpm billing:reconcile --apply` gives Pro back to the subscribers it dropped. `decide()` also returns a feature set now (BILL-01).
  8. Confirm or reverse DEC-014 option (B): keep the operator model and require MFA for the listed operators in Clerk. Option (A) adds a verified second factor in code (the session token's `fva` claim); option (C) limits diagnostics to local and preview.
  9. Confirm or reverse the other decisions of [ADR-0004](adr/0004-cycle-1-adopted-decisions.md), §16 to §26 included (§20 and §21 are listed above under "Awaiting owner approval").
- **Next steps (no owner action needed):** re-record PERF-03, PERF-04, and PERF-06 locally with the corrected harness; run `pnpm verify` with gitleaks installed (the secrets step was skipped in both recorded `pnpm verify` runs) and record it; re-run `proofs/dec-001/run.sh` on a quiet machine; re-record the clean-clone run (REPO-11) with clean caches.

**Final principle:** build a template that is secure by default, modular by design, reproducible from a clean clone, and honest about its verified limits. Do not trade correctness, security, or maintainability for superficial completion.
