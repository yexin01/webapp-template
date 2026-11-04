# Evidence: The Resend Email Integration (2026-11-04)

| Field | Value |
|---|---|
| Scope | The `email` module added on the branch `claude/template-readiness-audit` after `7901ff1`: a sender port, the Resend adapter, a mock, the sandbox recipient rule, the integration check, `EMAIL_MODE` and its variables, and the documents (DEC-015, EMAIL-01 to EMAIL-07, setup steps S13 and D18) |
| Why | On 2026-11-04 the owner named the minimum set of integrations: "The minimum is supabase, stripe, clerk, vercel, resend". Resend was the one missing |
| Environment | Linux (4 CPUs, a cloud VM). Node 22.23.4, pnpm 9.15.0, uv 0.11.32, the API's Python 3.12 virtual environment. PostgreSQL 17.6 from `pnpm db:up` (Docker). gitleaks not installed |
| Integration mode | `mocked` (a stub HTTP transport, fakes and the module's mock) and `local` (commands on this machine). **Nothing is `sandbox` or `live`: no Resend account was contacted.** |
| Remote actions | Six documentation pages of Resend were read (below). One push of the branch to `origin`, under the owner's authorization of 2026-11-02 ("you can commit and push them"). No pull request, deployment, release or provider call |

## Labels

- `mocked`: a test double stands in for the provider. It proves behavior against our model of Resend, never connectivity.
- `local`: a command run on one machine against local services. It is not `sandbox`.
- `NOT RUN`: a test or command exists and was not run. `NOT VERIFIED`: no sufficient evidence exists. Neither is PASS.

## What was built

| Part | Where | Requirement |
|---|---|---|
| Port `EmailSender`, the Resend adapter over `httpx` (HTTPS only, no redirects, a `User-Agent`, one deadline across attempts), and `MockEmailSender` | `apps/api/app/modules/email/ports.py`, `adapters.py` | EMAIL-01, EMAIL-04 |
| Message limits checked before any call, the sandbox recipient rule, a per-instance bound on sends in flight | `domain.py`, `service.py` | EMAIL-03, EMAIL-04 |
| `EMAIL_MODE` (`mock` by default), `RESEND_API_KEY`, `EMAIL_FROM`, `EMAIL_SANDBOX_RECIPIENTS`, the timeout and the bound, with startup refusals; `EMAIL_MODE` required in preview and production like every mode | `settings.py`, `app/settings.py`, `.env.example` | EMAIL-02, ENV-02 |
| The check: the domain list (a key with sending access only is reported as such), then one email to `delivered@resend.dev`, only from `/integrations` and from `pnpm check:setup --send-test-events` | `probe.py`, `module.py`, `app/doctor.py` | EMAIL-07 |
| `RESEND_API_KEY` in the bundle scan's server-only names, and a canary key in `pnpm test:vercel-build` | `scripts/check-bundle-secrets.mjs`, `scripts/test-vercel-build.mjs` | ENV-07 |
| `pnpm verify:sandbox` counts `EMAIL_MODE=sandbox` as something to check | `scripts/lib/verify-sandbox.mjs` | — |

Not built, and listed in [limitations.md](../../docs/limitations.md#follow-ups-in-the-code): Resend's webhooks (bounces, complaints), a suppression list, attachments, `cc` and `bcc`, batch and scheduled sends, templates, and any product email (DEC-015).

## Facts taken from Resend's documentation

Read on 2026-11-04 and **not observed against an account** (`NOT VERIFIED` until a sandbox run):

- The API reference: `https://api.resend.com`, `Authorization: Bearer re_...`, a required `User-Agent`, `POST /emails` answering `{"id"}`, an `Idempotency-Key` of up to 256 characters kept for 24 hours, `GET /domains` (every domain without `limit`), the domain statuses, the error names (`validation_error`, `restricted_api_key`, `rate_limit_exceeded`, `daily_quota_exceeded`, `monthly_quota_exceeded`, `invalid_idempotent_request`, `concurrent_idempotent_requests`, `application_error`), and the key permissions `full_access` and `sending_access` ([create an API key](https://resend.com/docs/api-reference/api-keys/create-api-key)). The shape of an error body was not confirmed: the adapter reads only `name`, bounded, and treats anything else as `unknown`.
- [Send test emails](https://resend.com/docs/dashboard/emails/send-test-emails): `delivered@resend.dev` and the other test addresses, labels after `+` ("The suppressed test email address does not support labeling yet"), and "Test emails count against your account's sending quota."
- [403 error using the resend.dev domain](https://resend.com/docs/knowledge-base/403-error-resend-dev-domain): "The `resend.dev` domain is only available for testing purposes and can only send emails to the email address associated with your Resend account." Whether that sender may send to the test addresses is not documented, so the check fails with `testing_domain` for a sender on `resend.dev`, and live mode refuses one at startup.
- [Domains](https://resend.com/docs/dashboard/domains/introduction) and [add a domain](https://resend.com/docs/add-a-domain): the Domains page, **Add Domain**, the SPF and DKIM records (`TXT`, and `MX` or `CNAME`). [API keys](https://resend.com/docs/dashboard/api-keys/introduction): a key has a name, a permission and an optional domain restriction. The labels of the dashboard's key page were not seen.

## Results

| Check | Command | Result | Label |
|---|---|---|---|
| Full gate | `pnpm verify` on `3f0317b` (working tree clean) | **PASS**: 23 steps passed, 1 skipped in 16 min 54 s. API: 2417 passed, 9 skipped (the Stripe sandbox suite, which needs keys). Web: 1021 passed, 1 skipped. Scripts: 128 passed. Generated modules (`new-module`): passed in 9 min 07 s. Account-free browser tier: 56 passed, 14 skipped. Skipped: `secrets`, because gitleaks is not installed; the pre-commit hook scanned the staged changes of both commits with the pinned gitleaks image and found no leaks | `local`, `mocked` |
| Earlier full gate | `pnpm verify` on `da21514` | **FAIL** at `init-template` (REPO-07): the adapter's `User-Agent` named the template, so a copy renamed by `pnpm init-template` kept the original identifier in `adapters.py`; 10 later steps did not run. Fixed in `3f0317b` (httpx's own `User-Agent`); `pnpm verify --only init-template` then passed, and so did the full run above | `local` |
| Deployment configuration | `pnpm test:vercel-build` on `3f0317b` | ok: `vercel build` (CLI 65.2.0) and 13 output checks; the bundle scan found none of 19 server-only values, the `RESEND_API_KEY` canary among them; the function bundle is 75.1 MiB | `local` |
| Supabase's image | `pnpm test:supabase` on `3f0317b` | Running when this record was first committed: the migrations, the role checks and `api-login` had passed, and the API's database tests were under way. The next commit of this record gives the result | `local` |
| Mutation checks | Each rule of the module was broken by hand and its test run: the 15 of the first pass, then the `resend.dev` sender in the check and in live mode (2), the parsing of a named sender (1), the bound on a domain's status (1), the wording of a status (1), and the CLI step for `EMAIL_MODE=disabled` (1) | All 21 killed; the files were restored and compared | `local` |
| Resend | `pnpm check:setup`; `pnpm check:setup --send-test-events` | **NOT RUN**: no Resend key here, and the second sends an email (A3) | — |

Also NOT RUN: `pnpm verify:sandbox`, the Stripe sandbox suite, and `proofs/dec-001/run.sh`, none of which this change touches.
