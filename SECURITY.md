# Security Policy

## Reporting a vulnerability

**Do not open public issues for security problems.**

Report vulnerabilities privately to: **{{SECURITY_CONTACT}}**

<!--
Template adopters: `pnpm init-template --security-contact ...` replaces the
placeholder above, and `pnpm check:placeholders` (run in CI) fails once the
project is renamed while a placeholder remains. GitHub private vulnerability
reporting is a good channel; enable it in the repository settings first.
-->


Please include:

- affected component and version or commit;
- reproduction steps and impact;
- any suggested fix.

Use synthetic data only. Never include real credentials or personal data.

## What to expect

- Acknowledgement as soon as maintainers are able.<!-- Adopters: state a response-time target here only if you commit to one. -->
- An assessment and remediation plan once the issue is reproduced.
- Coordinated disclosure after a fix is available. We will credit you unless you prefer otherwise.

## Scope and rules

- Test only against your own deployment, with your own accounts and synthetic data.
- Do not test against other people's deployments or third-party services (Clerk, Stripe, Supabase, PostHog, Sentry, LLM providers).
- No denial-of-service, spam, or social engineering.

## Supported versions

Only the latest commit on the default branch receives security fixes while the project is pre-release.

## Security model (summary)

The full threat model is in [`dev-docs/engineering-implementation-plan.md`](dev-docs/engineering-implementation-plan.md) §13.2.

- Every API operation verifies the Clerk session token server-side and applies a deny-by-default authorization policy.
- Tenant isolation is enforced by the application *and* by forced PostgreSQL row-level security.
- Pro entitlements come only from signature-verified Stripe data that is re-read from Stripe.
- Secrets never reach the browser bundle. This is checked in CI.
- Logs and telemetry are redacted and collect no request bodies.

Passing automated scans is evidence of those checks only. It does not prove the absence of vulnerabilities.
