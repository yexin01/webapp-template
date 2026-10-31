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

- Every API operation verifies the Clerk session token server-side and applies a deny-by-default authorization policy. A session that someone else started (a Clerk impersonation or an actor token) is refused unless `CLERK_ALLOW_IMPERSONATION=true`, and is audited when allowed.
- Tenant isolation is enforced by the application *and* by forced PostgreSQL row-level security. The API logs in as a role that owns nothing and cannot bypass row-level security, and in preview and production it refuses to start database work under a login role that could. Row-level security does not stop SQL injection: code that runs arbitrary SQL can still read across tenants through the worker role.
- Pro entitlements come only from signature-verified Stripe data that is re-read from Stripe.
- Secrets never reach the browser bundle. This is checked in CI. On Vercel, a project with more than one service gives every service every project variable, so the web service's server processes can read the API's secrets; the template ships that topology. Vercel documents the sharing; it has not been observed here.
- Logs and telemetry are redacted and collect no request bodies. They carry the request ID and internal, pseudonymous user and organization IDs; some billing log lines also carry Stripe record IDs. The hosting platform keeps its own request logs ([`docs/data-inventory.md`](docs/data-inventory.md#external-services)).
- Pages require sign-in by default, but the API is the authority: it authorizes every operation whatever the web app shows.

Known limitations that bear on security are listed in [`docs/limitations.md`](docs/limitations.md).

Passing automated scans is evidence of those checks only. It does not prove the absence of vulnerabilities.
