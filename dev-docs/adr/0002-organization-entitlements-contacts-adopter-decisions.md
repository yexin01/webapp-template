# ADR-0002 — Organization Entitlements, Community Contacts, and Adopter-Owned Decisions

- **Status:** Accepted
- **Date:** 2026-10-14
- **Decided by:** Repository owner
- **Resolves:** DEC-002 (option b); DEC-009 staleness value; DEC-012 (contacts); DEC-003, DEC-004, DEC-005 (accepted as adopter-owned for the template)

## Context

The register still had DEC-002 to DEC-005 open, plus the DEC-009 staleness value and the DEC-012 contacts. This repository is a template. Some of these questions have one answer for every project made from it. Others depend on each deployment: its users, jurisdiction, service plans, and workload. The owner was asked each question and chose the options below.

## Decisions

### DEC-002 — Individual billing in organizations: option (b)

An individual's Pro subscription applies to **that individual's own actions in every organization they currently belong to**. It is never shared with other members. Organization roles neither grant nor remove it.

Truth table (BILL-ORG-02). It is enforced by `decide()` in `apps/api/app/modules/billing/domain.py` (moved there by ADR-0003) and tested in `apps/api/tests/integration/test_org_entitlements.py`:

| Actor | Context | Role | Membership | Actor's subscription | Pro-gated feature | Entitlement |
|---|---|---|---|---|---|---|
| Subscriber | Personal | — | — | Entitling (`active`, `past_due`, canceling before its end date) | Allowed | Pro |
| Subscriber | Organization | Admin or member | Current | Entitling | Allowed | Pro |
| Subscriber | Organization | Admin or member | Current | Not entitling (`canceled`, `unpaid`, `incomplete`, ended, unknown price) | `403 ENTITLEMENT_REQUIRED` | Free |
| Non-subscriber | Personal or organization | Any | Current | None | `403 ENTITLEMENT_REQUIRED` | Free (even if other members are Pro) |
| Anyone | Organization | — | Not a member, or removed and re-verified | Any | `404` (no organization context) | — |

Consequences:

- **Joining an organization:** Pro applies there once the membership is mirrored.
- **Leaving or removal:** the organization context disappears within the staleness bound (DEC-009), or immediately on a webhook. The personal subscription is unaffected.
- **Cancellation:** Pro ends in organizations at the same moment as in the personal context. The same matrix applies (§9.5).
- **Role changes:** no effect on entitlement. Administration remains independent of billing. Pro does not grant administrative rights.
- **Billing management** (checkout, Customer Portal, status) stays in the personal context. There are no organization-scoped billing routes.
- **Account deletion:** adopter-owned (DEC-004 below).
- **Isolation:** the API reads only the actor's own subscriptions. The RLS policy `subscriptions_api_select` (`user_id = app.ctx_user_id()`) also enforces this.

### DEC-009 — Staleness bound

`MEMBERSHIP_MAX_STALENESS_SECONDS = 300` is confirmed. NT-05 tests against it.

### DEC-012 — Community contacts: adopter placeholders

- `SECURITY.md` and `CODE_OF_CONDUCT.md` ship with the placeholders `{{SECURITY_CONTACT}}` and `{{CONDUCT_CONTACT}}`. The template contains no personal contact details.
- `pnpm init-template` requires `--security-contact` and `--conduct-contact` while the placeholders are present. Each value must be an email address or an `https` URL.
- `pnpm check:placeholders` runs in CI:
  - In the un-initialized template, where the package name is still `saas-template`, it reports the placeholders and passes.
  - In a renamed project, it fails while any placeholder remains.

### DEC-003, DEC-004, DEC-005 — accepted as adopter-owned

Performance targets, data retention and deletion, and backup and recovery depend on each deployment. The owner accepts them as **adopter-owned** for the template, which satisfies the "explicitly accepted by the owner" clause of Gates D and E for the template itself. Adopters still decide them before their own production launch.

| Decision | Template ships | Template does **not** ship or claim |
|---|---|---|
| DEC-003 performance targets | `perf/` harness for PERF-01 to PERF-07; a run-plan and targets worksheet (`perf/run-plan-template.md`); local reference results | Any performance target, or any claim that a target is met |
| DEC-004 retention, export, deletion | Data inventory and adopter decision checklist (`docs/data-inventory.md`) | Account deletion or export features; any deletion-completeness claim. Payment records are never deleted by template code |
| DEC-005 backup and recovery | Local restore drill (`pnpm db:restore-drill`); adopter backup checklist and hosted restore-test procedure (`docs/operations.md`) | Any RPO or RTO, or any recovery-readiness claim |

## Still open

- **DEC-001** (Vercel preview proof): needs owner authorization and a Vercel account.
- **DEC-006** (sandbox verification): needs sandbox keys in `.env`.
