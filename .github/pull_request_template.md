## What and why

<!-- Link the requirement IDs (e.g. BILL-08, TEN-07) or decision (DEC-…) this change implements. -->

## Verification

- [ ] Tests added or updated
- [ ] `pnpm verify` passes (the checks of CI's api, web and secrets jobs; `pnpm test:vercel-build` and `pnpm test:supabase` cover the other two; use `pnpm verify:quick` while iterating). First-time setup: `docs/setup.md`
- [ ] Evidence is labeled **mocked**, **sandbox**, or **live**; nothing unverified is claimed as verified

## Safety checklist

- [ ] No secrets, tokens, real personal data, or production identifiers in code, tests, logs, or screenshots
- [ ] Authorization is enforced server-side for any new or changed endpoint (tenant isolation tests updated)
- [ ] Migrations are additive, or a reviewed expand → contract plan is included
- [ ] CI workflow permission changes were reviewed
- [ ] Docs and the specification are updated if behavior, threats, or operations changed
