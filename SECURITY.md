# Security Policy

## Reporting a vulnerability

**Do not open public issues for security problems.**

Report vulnerabilities privately through this repository's **Security → Report a vulnerability** form (GitHub private vulnerability reporting).

> Maintainers: private vulnerability reporting must be enabled in the repository settings, and a fallback contact still needs to be chosen (DEC-012 in the engineering specification). Until both are done, this channel may be unavailable.

Please include:

- affected component and version or commit;
- reproduction steps and impact;
- any suggested fix.

Use synthetic data only. Never include real credentials or personal data.

## What to expect

- Acknowledgement as soon as maintainers are able. Response-time targets are pending the owner's decision (DEC-012).
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
