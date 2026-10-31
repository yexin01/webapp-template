# Deployment

How the template is meant to run on Vercel and Supabase: what ships, which variable goes where, how to bring up a first preview, how to verify it, and how to go to production. Day-two procedures (rotation, migrations, incidents, backups) are in [operations.md](operations.md). The steps that need a person carry **[YOU]** with an ID; [setup.md](setup.md#manual-steps-checklist) lists them all in order.

> **NOT VERIFIED on the platform.** The configuration below is built and tested on a development machine with the pinned Vercel CLI (`pnpm test:vercel-build`, local packaging evidence only). Nothing has been deployed to Vercel, and no Vercel, Supabase or GitHub account was contacted. Vercel Services is in Beta. The assumptions that only a first preview deployment can confirm are listed in [limitations.md](limitations.md#assumptions-not-verified); the first preview checks are in [Verify the first preview](#verify-the-first-preview).

Deploying, promoting, and changing hosting or Git settings are remote actions: each needs the repository owner's authorization ([Authorizations](#authorizations)).

## What ships

**One Vercel project with Vercel Services** (DEC-001, adopted by delegation of the owner and reversible; the alternative is two projects, one for the web app and one for the API, which is not shipped; [ADR-0004](../dev-docs/adr/0004-cycle-1-adopted-decisions.md) records the choice). The root `vercel.json` declares:

- `services.web`: the Next.js app in `apps/web`.
- `services.api`: the FastAPI app in `apps/api` (entrypoint `index.py`, Python 3.12, x86_64), with `excludeFiles` keeping `tests/` and tool caches out of the bundle, and one function region.
- Rewrites, in order: `/api/v1/(.*)` to the `api` service, then `/(.*)` to `web`. The platform is meant to send `/api/v1/*` to the API before Next.js sees it, so the web app's own `/api/v1` rewrite and `API_ORIGIN` are not used (NOT VERIFIED until the first preview).
- The four crons ([operations.md](operations.md#scheduled-jobs)). Crons must be in the root file: a service's own `vercel.json` crons are not registered.

Other pieces: the GitHub workflow `db-migrate.yml` ([migrations](#hosted-database-and-migrations)), and the CI jobs in `.github/workflows/ci.yml`, which include `deploy-config` (`vercel build` with the pinned CLI, no deployment).

Sources: [Vercel Services](https://vercel.com/docs/services), [configuration reference](https://vercel.com/docs/services/config-reference), [routing](https://vercel.com/docs/services/routing). Services is available in Beta on all plans; the project's Framework Preset must be **Services** or the `services` key is not used (NOT VERIFIED until a project exists).

**Plan.** The Hobby plan is for non-commercial, personal use only ([Vercel: Hobby](https://vercel.com/docs/plans/hobby)); it also allows one WAF rate-limit rule, daily cron jobs only, and Vercel may fire a daily cron anywhere within the scheduled hour ([cron accuracy](https://vercel.com/docs/cron-jobs/manage-cron-jobs#cron-jobs-accuracy)). A commercial launch needs the Pro plan: your cost decision.

## Environments

| Environment | `APP_ENV` | Database | Modes | Keys |
|---|---|---|---|---|
| Local | `local` | Docker PostgreSQL (`pnpm db:up`) or a non-production Supabase project | `mock`, `sandbox` | Developer keys only |
| Test | `test` | Ephemeral database created by the test suite. No `.env` is read | `mock` | None |
| Vercel Preview | `preview` | Isolated non-production database with synthetic data | `mock`, `sandbox`, `disabled` ([allowed per variable](integrations.md#modes)) | Sandbox and Clerk development keys |
| Vercel Production | `production` | Production database | `live`, or `disabled` where the integration is optional ([per variable](integrations.md#modes)) | Live keys. Startup rejects `mock` and `sandbox` |

- **`APP_ENV` on Vercel.** Leave it unset: it is taken from `VERCEL_ENV` (`production`, `preview`; `development` is `local`). A value that contradicts the platform is refused: `production` on a Preview deployment, or `local` / `test` on a Preview or Production one. Both the API (at start) and the web build (when `VERCEL=1`) refuse a deployment with neither `APP_ENV` nor `VERCEL_ENV`, so keep "Automatically expose System Environment Variables" on (the default).
- **A staging or pre-launch project** may set `APP_ENV=preview` on the Production scope.
- **Explicit modes.** In Preview and Production, set `BILLING_MODE`, `LLM_MODE`, `ANALYTICS_MODE` and `ERROR_REPORTING_MODE`; none defaults there.
- **Never** import the local `.env` into a Vercel project (`APP_ENV=local` is refused). Put production values only in the Production scope. Live keys are refused unless `APP_ENV=production`, and test keys are refused in production.

## Variables: which service reads what

Vercel gives a deployment's build and its functions the project's variables for the chosen scopes ([Vercel: environment variables](https://vercel.com/docs/environment-variables/managing-environment-variables)). Under Services every project variable reaches both services, and there is no per-service setting. The Development scope is not used by this template: local runs read the root `.env`.

**A change to a variable applies only to new deployments: redeploy after every change.** On Vercel a redeploy rebuilds the web app, which also refreshes the values the web build fixes (marked *baked* below). Opening the app at an origin that `APP_BASE_URL` and `CLERK_AUTHORIZED_PARTIES` do not name causes a `/sign-in` loop; the API logs `clerk_azp_refused` with the origin to add. The same names are tagged variable by variable in [`.env.example`](../.env.example).

**Shared-environment consequence.** The web service's server processes receive the API's secrets: `DATABASE_URL`, `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `CLERK_WEBHOOK_SIGNING_SECRET`, `LLM_API_KEY` and `CRON_SECRET`; and the API function receives the web build's `SENTRY_AUTH_TOKEN`. The web app's code reads only its own variables (the local `.env` loader filters the rest), so "the web app holds no server secrets" (FE-02) holds in the code but not at the environment level. Mitigations: keep API-only secrets few; never put `MIGRATION_DATABASE_URL` or `API_DB_PASSWORD` in the Vercel project; and `pnpm test:vercel-build` builds the web service with canary values for every server-only variable and fails if one reaches the browser bundle. Running that scan inside every Vercel build is not wired up ([limitations.md](limitations.md#follow-ups-in-the-code)). Accepting this is part of choosing the single-project topology.

Legend: **web** = the Next.js service, **api** = the Python service. *Baked* = fixed when the web app is built. *Secret* = store as a secret variable; *public* = shipped in the browser bundle.

**Core**

| Variable | Read by | Kind | Preview | Production |
|---|---|---|---|---|
| `APP_ENV` | web, api; baked | config | Unset | Unset (`preview` for staging) |
| `APP_BASE_URL` | web, api; baked | config | Required: the stable preview URL you test on ([Preview deployments](#preview-deployments)) | Required: the production origin, `https` |
| `CLERK_AUTHORIZED_PARTIES` | web, api; baked | config | Optional; the deployment's URL and branch URL are added | Every origin that serves the app (apex and `www` if both do) |
| `API_ORIGIN` | web build | config | Leave unset | Leave unset |
| `DATABASE_URL` | api | secret | Preview database, `app_login`, transaction pooler | Production database, `app_login`, transaction pooler |

**Clerk**

| Variable | Read by | Kind | Preview | Production |
|---|---|---|---|---|
| `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` (API alias `CLERK_PUBLISHABLE_KEY`) | web, api; baked | public | `pk_test_` of the development instance | `pk_live_` of the production instance |
| `CLERK_SECRET_KEY` | web (proxy; required at build on Vercel), api | secret | `sk_test_` | `sk_live_` |
| `CLERK_JWT_KEY` | web (proxy), api; baked | config | Empty | Empty, or the production instance's PEM |
| `CLERK_WEBHOOK_SIGNING_SECRET` | api | secret | The Preview endpoint's secret | The Production endpoint's secret |
| `CLERK_ALLOW_IMPERSONATION` | api | config | Leave unset | Leave unset |

**Stripe**

| Variable | Read by | Kind | Preview | Production |
|---|---|---|---|---|
| `BILLING_MODE` | api | config | `sandbox` (or `mock`) | `live` |
| `STRIPE_SECRET_KEY` | api | secret | `sk_test_` / `rk_test_` | `sk_live_` / `rk_live_` |
| `STRIPE_WEBHOOK_SECRET` | api | secret | The Preview endpoint's secret | The Production endpoint's secret |
| `STRIPE_PRICE_PRO_MONTHLY`, `STRIPE_PRICE_PRO_YEARLY` | api | config | Sandbox price IDs | Live price IDs |
| `STRIPE_PRODUCT_PRO` | api | config | Sandbox product ID | Live product ID |
| `STRIPE_CUSTOM_DOMAIN` | web, api; baked | config | Unset | Optional, live mode only |
| `BILLING_RECONCILE_APPLY` | api | config | Leave unset (the job applies Stripe's state) | Leave unset; `false` makes the job only report |
| `BILLING_ON_USER_DELETED` | api | config | Leave unset (a deleted user's subscription is set to end at the period's end) | Leave unset; `log` only reports it |

**LLM, analytics, errors**

| Variable | Read by | Kind | Preview | Production |
|---|---|---|---|---|
| `LLM_MODE` | api | config | `disabled`, `mock` or `sandbox` | `disabled` or `live` |
| `LLM_PROVIDER`, `LLM_MODEL` | api | config | With `sandbox` | With `live` |
| `LLM_API_KEY` | api | secret | With `sandbox` | With `live` |
| `ANALYTICS_MODE` | web, api; baked | config | `disabled` or `sandbox` | `disabled` or `live` |
| `NEXT_PUBLIC_POSTHOG_KEY` (API alias `POSTHOG_PROJECT_API_KEY`) | web, api; baked | public | Preview project token | Production project token |
| `NEXT_PUBLIC_POSTHOG_HOST` (API alias `POSTHOG_HOST`) | web, api; baked | public | Optional | Optional |
| `ERROR_REPORTING_MODE` | web, api; baked | config | `disabled` or `sandbox` | `disabled` or `live` |
| `SENTRY_DSN` | api | config | API project DSN | API project DSN |
| `NEXT_PUBLIC_SENTRY_DSN` | web; baked | public | Web project DSN | Web project DSN |
| `SENTRY_TRACES_SAMPLE_RATE` | web, api | config | Optional | Optional |
| `SENTRY_RELEASE` (alias `VERCEL_GIT_COMMIT_SHA`, set by Vercel) | api | config | Leave unset | Leave unset |
| `SENTRY_AUTH_TOKEN`, `SENTRY_ORG`, `SENTRY_PROJECT` | web build | secret, config, config | Optional | Source maps upload only with all that apply ([Sentry](integrations.md#sentry)) |

**Operations**

| Variable | Read by | Kind | Preview | Production |
|---|---|---|---|---|
| `DIAGNOSTICS_ENABLED` | api | config | Unset (on) | Unset (off), or `true` temporarily |
| `DIAGNOSTICS_OPERATOR_IDS` | api | config | Your user ID in the development instance | Your user ID in the production instance (IDs differ per instance) |
| `CRON_SECRET` | api | secret | Optional, to call job routes by hand | Required for the crons: at least 32 characters, `openssl rand -hex 32`. Vercel sends it only from the Production scope |
| `WRITES_PER_USER_PER_MINUTE` | api | config | Leave unset (60) | Leave unset (60) |
| `JOB_MIN_SPACING_SECONDS` | api | config | Leave unset (60) | Leave unset (60); lower it only for a schedule of a minute or less |
| Other tuning (the "Advanced" section of `.env.example`) | api | config | Leave unset | Leave unset |

Never in the Vercel project: `MIGRATION_DATABASE_URL` and `API_DB_PASSWORD` (the migration credential lives in a GitHub Environment secret), and the `E2E_*` variables. Set by Vercel, never by you: `VERCEL`, `VERCEL_ENV`, `VERCEL_URL`, `VERCEL_BRANCH_URL`, `VERCEL_GIT_COMMIT_SHA`, and `VERCEL_AUTOMATION_BYPASS_SECRET` when a bypass secret exists.

Count for a full sandbox environment: about 21 required variables per scope, and about 25 with the optional ones, so roughly 42 to 50 entries for Preview and Production together. Optional and dashboard-only (plan availability not confirmed): the team security policy "Separate Production Secret Values", which forces different secret values in Production and Preview.

## Set up Vercel

Each step is a person's; none can be automated from this repository.

1. **[YOU D1]** Create the Vercel project from the Git repository, with Root Directory set to the repository root. Needs the repository on a Git host ([Authorizations](#authorizations), A1).
2. **[YOU D2]** In Project Settings, set the Framework Preset to **Services**, and keep "Automatically expose System Environment Variables" on. Without the preset the `services` key is not used.
3. **[YOU D3]** Set the function region to the region of your database. Read the region from the pooled `DATABASE_URL` host (`aws-<n>-<region>.pooler.supabase.com`), find the Vercel region below, and set `services.api.functions["index.py"].regions` in `vercel.json` to that one value (an agent can make the edit and commit). Preview and Production share one value, so create both Supabase projects in the same region (a project's region cannot be changed later). The shipped `iad1` fits `us-east-1` only; a mismatch costs about one network round trip per database statement of every API request. `apps/api/tests/unit/test_vercel_config.py` rejects an unknown code or more than one.

   | Supabase / AWS | Vercel | Supabase / AWS | Vercel |
   |---|---|---|---|
   | us-east-1 | iad1 | eu-west-3 | cdg1 |
   | us-east-2 | cle1 | eu-central-1 | fra1 |
   | us-west-1 | sfo1 | eu-central-2 | fra1 (no Zurich region) |
   | us-west-2 | pdx1 | eu-north-1 | arn1 |
   | ca-central-1 | yul1 | ap-south-1 | bom1 |
   | eu-west-1 | dub1 | ap-southeast-1 | sin1 |
   | eu-west-2 | lhr1 | ap-northeast-1 | hnd1 |
   | sa-east-1 | gru1 | ap-northeast-2 | icn1 |
   | ap-southeast-2 | syd1 | | |

   These are the nearest Vercel regions, chosen by the template's maintainers ([Vercel: regions](https://vercel.com/docs/regions)). Only the API service has a region setting: the web service keeps Vercel's default region, because it never queries the database. Whether the function runs in the configured region is NOT VERIFIED (the build output only carries the field).
4. **[YOU D4]** Add the environment variables for Preview and for Production from the [map](#variables-which-service-reads-what), pasting values from each provider's dashboard (sandbox values in Preview, live values in Production). Generate `CRON_SECRET` with `openssl rand -hex 32` and set it in Production. Mark every secret as a secret variable. Redeploy after the last change.
5. **[YOU D5]** Decide Deployment Protection. Check the project's Deployment Protection settings: where they cover previews, a provider cannot reach a protected preview. To let Stripe and Clerk deliver webhooks to it, create a Protection Bypass for Automation secret in the project settings and append it to the endpoint URLs ([Preview deployments](#preview-deployments)). Vercel sets `VERCEL_AUTOMATION_BYPASS_SECRET` from it, which `perf/` uses. Production access is your decision.
6. **[YOU D6]** Choose the plan: Pro for a commercial product (see above).
7. **[YOU D7]** Add the platform rate-limit rule (DEC-011), the coarse per-IP layer. In the project, open **Firewall**, select **Configure**, then **+ New Rule**. Add **If** conditions (all must be true): the request path starts with `/api/v1/`, does not start with `/api/v1/webhooks/`, and does not start with `/api/v1/jobs/`. **Then** **Rate Limit**, fixed window, key **IP**, time window 60 s, request limit 300 (a starting point: it is not measured, tune it to your traffic), first with the **Log** action, then the default **429**. **Save Rule**, **Review Changes**, **Publish**. The first time, Vercel asks you to review its **Rate Limiting Pricing** dialog. Hobby allows one such rule per project, and counters are kept per region ([Vercel: WAF rate limiting](https://vercel.com/docs/vercel-firewall/vercel-waf/rate-limiting), checked 2026-10-31). Never put webhook or job paths behind a rate limit or challenge: Stripe and Clerk deliver from shared addresses, and throttling them causes retries.

   With your authorization an agent can stage the same rule with the Vercel CLI, from a directory linked to the project (`vercel link`; [`vercel firewall`](https://vercel.com/docs/cli/firewall), checked 2026-10-31). A staged rule is a draft that changes nothing until published:

   ```bash
   vercel firewall rules add "API per-IP limit" \
     --condition '{"type":"path","op":"pre","value":"/api/v1/"}' \
     --condition '{"type":"path","op":"pre","value":"/api/v1/webhooks/","neg":true}' \
     --condition '{"type":"path","op":"pre","value":"/api/v1/jobs/","neg":true}' \
     --action rate_limit --rate-limit-window 60 --rate-limit-requests 300 --rate-limit-keys ip \
     --rate-limit-action log --yes
   vercel firewall diff
   ```

   Read the diff, then publish it yourself (`vercel firewall publish --yes`), watch the logged traffic, and switch the rule to refusing with `vercel firewall rules edit "API per-IP limit" --rate-limit-action rate_limit --yes`, `vercel firewall diff`, and another `publish`. `vercel firewall discard --yes` drops a draft. The pinned `vercel@65.2.0` lists these flags in its `--help` (local; no account was contacted). NOT VERIFIED: the dashboard's wording for the negated path conditions, that the CLI's `rate_limit` action answers 429 as the dashboard's default does, and the rule's effect on a deployment.

   The application limits per signed-in user and per job as well ([operations.md](operations.md#limits)): this rule is for floods that carry no valid session.

## Set up GitHub

Applying these settings is a remote change: it needs the owner's authorization ([Authorizations](#authorizations)). An agent can apply them with `gh api` once authorized.

1. **[YOU D8]** In Settings > Environments, create `preview` and `production`. On each, add the secret `MIGRATION_DATABASE_URL`: the migration role's Supabase **Session pooler** string (`postgresql://postgres.<project-ref>:<url-encoded password>@aws-0-<region>.pooler.supabase.com:5432/postgres`). Direct connections are IPv6-only without the IPv4 add-on, and GitHub-hosted runners are IPv4-only. Preview and production hold different credentials. On `production`, add yourself as a required reviewer and allow deployments from the default branch only. The workflow never prints the value, but GitHub masks only the exact string, so an error message may show the host. Environments for private repositories need GitHub Pro or Team, and some environment features are limited for private repositories ([GitHub: environments](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments)); without required reviewers, the click on "Run workflow" is the only gate: restrict who has write access.
2. **[YOU D9]** Create a branch ruleset for the default branch (Settings > Rulesets, under "Code, planning, and automation" > New ruleset > New branch ruleset, with the rule "Require status checks before merging"; [GitHub: rulesets](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/creating-rulesets-for-a-repository)) requiring these checks, which are the job names in `ci.yml`: `API — lint, types, tests, audit`, `API — Supabase Postgres image`, `Web — lint, types, tests, build, e2e`, `Deployment configuration — vercel build, no deployment`, `Secret scan`.
3. **[YOU D10]** Turn on secret scanning push protection ([GitHub: push protection](https://docs.github.com/en/code-security/secret-scanning/enabling-secret-scanning-features/enabling-push-protection-for-your-repository): Settings > Advanced Security) and private vulnerability reporting ([GitHub: private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/working-with-repository-security-advisories/configuring-private-vulnerability-reporting-for-a-repository)). `SECURITY.md` names private vulnerability reporting as a good reporting channel, once it is enabled.
4. **[YOU D11]** Decide on CodeQL. "If you want to use code scanning on private repositories, you need a GitHub Code Security license" ([GitHub: code scanning](https://docs.github.com/en/code-security/code-scanning/introduction-to-code-scanning/about-code-scanning)). Without it the CodeQL workflow (`.github/workflows/codeql.yml`, checks `Analyze (python)`, `Analyze (javascript-typescript)`, `Analyze (actions)`) cannot report in a private repository (NOT VERIFIED here): buy the license, or remove the workflow from your copy. Do not make its checks required until they pass. The Secret scan job needs no license: it runs the pinned gitleaks command line.

Optional: a Vercel Deployment Check on the production project that waits for the "migrate (production)" run. It is not built: a dispatched workflow reports no commit status by itself.

## Hosted database and migrations

1. Create one Supabase project for Preview and one for Production, in the same region ([setup.md](setup.md#supabase-optional-hosted-database)).
2. **Apply migrations** with the GitHub workflow **Database migrate** (Actions > Database migrate > Run workflow; choose `preview` or `production`, the GitHub Environment whose `MIGRATION_DATABASE_URL` names the database). It runs `pnpm db:migrate --allow-remote`. It is manual only and runs only from the default branch; for production it waits for the reviewer you set. Run it after the code that needs the migrations is merged and before that code is promoted. Never apply an unmerged branch's migrations to a shared database (once a later version is recorded, every earlier-numbered migration is refused), and never migrate in the Vercel build step. Needs authorization A3.
3. **Create the runtime login.** From a machine with the Session pooler string: `MIGRATION_DATABASE_URL='<session pooler string>' pnpm db:api-login --allow-remote`, typing a new password (16 or more characters) twice. It prints the `DATABASE_URL` to use: the Transaction pooler string (port `6543`, not the `5432` you connected with) with the user `app_login.<project-ref>`. Set it in the Vercel scope of that environment and redeploy. Never put the `postgres` URL in Vercel: in Preview and Production the API refuses it.
4. Order for a first environment: migrate, then `db:api-login`, then `DATABASE_URL`, then deploy. After restoring a dump into a new server, run `db:api-login` again: a dump holds no role passwords.

Migrations follow expand, deploy, contract ([operations.md](operations.md#database)).

## Preview deployments

- **Stable URL.** Each branch has a stable URL, exposed as `VERCEL_BRANCH_URL` ([Vercel: system environment variables](https://vercel.com/docs/environment-variables/system-environment-variables)); each deployment also has its own `VERCEL_URL`. On a Preview deployment of a preview application the API and the web proxy trust both for sign-in automatically. They are not used for anything else.
- **`APP_BASE_URL`** is one value for the Preview scope. Set it to the stable URL of the branch you test on. The API's CORS origin and Stripe's redirect URLs use it, so test billing on the deployment at that URL.
- **Clerk keys.** Previews on `*.vercel.app` use the Clerk development instance: a `*.vercel.app` domain cannot host a Clerk production instance, and live keys are refused outside production.
- **[YOU D12]** Create the webhook endpoints of each deployed environment, starting with Preview: a Clerk endpoint (five events) and a Stripe event destination (payload **Snapshot**, 14 events), each pointing at that environment's own URL. Put each endpoint's own signing secret in `CLERK_WEBHOOK_SIGNING_SECRET` and `STRIPE_WEBHOOK_SECRET` of that scope, and redeploy. Production's endpoints are created in [Production](#production). The events and settings: [integrations.md](integrations.md).
- **Webhooks to a protected preview.** Vercel's bypass for tools that cannot set headers is a query parameter: `https://<stable-url>/api/v1/webhooks/stripe?x-vercel-protection-bypass=<secret>` ([Vercel: Protection Bypass for Automation](https://vercel.com/docs/deployment-protection/methods-to-bypass-deployment-protection/protection-bypass-automation), which names Stripe as an example). The secret then lives in the Stripe and Clerk dashboards: treat it as a credential and regenerate it if it leaks (regenerating invalidates earlier deployments; redeploy and update the URLs). The API does not log or report query strings. NOT VERIFIED: that Stripe and Clerk accept an endpoint URL with a query string.
- **Crons never run on previews** (Vercel calls the production deployment). To check a job route on a preview, set `CRON_SECRET` in the Preview scope and call it with `Authorization: Bearer <CRON_SECRET>`: that checks the route, not the scheduler.
- **Callback URLs**

  | Provider | URL |
  |---|---|
  | Clerk webhook | `<stable-url>/api/v1/webhooks/clerk`, with the bypass parameter if protected. Events and secret: [integrations.md](integrations.md#clerk) |
  | Stripe webhook | `<stable-url>/api/v1/webhooks/stripe`, payload **Snapshot**, with the bypass parameter if protected |
  | Stripe redirects | Built from `APP_BASE_URL` ([integrations.md](integrations.md#callback-url-summary)) |
  | Clerk sign-in and sign-up paths | The app sets `/sign-in` and `/sign-up` in code. The Clerk dashboard `Paths` setting only affects links Clerk sends (invitations, email links) |

## Verify the first preview

Run these on the first preview deployment you authorize (A2), with sandbox credentials. Record the results as `sandbox` evidence under `dev-docs/evidence/` (spec §20).

1. **Does the proxy run inside the service?** This is the 2-minute check. Signed out, request a protected page:

   ```bash
   curl -sI https://<preview-host>/dashboard
   ```

   - `307`: the Next.js proxy ran inside the Services web service. On a Clerk development instance (a preview uses one), a request with no cookie is first sent to Clerk's handshake (`location: https://<clerk-frontend-api>/v1/client/handshake?...`); with a `__clerk_db_jwt` cookie (`-H 'Cookie: __clerk_db_jwt=x'`; the account-free end-to-end tests send such a cookie) it goes to `location: /sign-in?redirect_url=...`. Either way the redirect came from the proxy.
   - `200`: the proxy did not run; only the page gate in the browser protects pages (it redirects once the page loads). The API authorizes every operation either way, so this affects redirects only. Vercel documents that middleware is not supported inside a service; a local build shows the proxy emitted as a Node.js function, and which holds on the platform is NOT VERIFIED until now.
   - `401` or an HTML page from Vercel: Deployment Protection answered. Add `-H "x-vercel-protection-bypass: <secret>"`.
2. **Sign in** on the preview URL and open `/dashboard`. Staying there means `VERCEL_URL` reached the web build and the derived origins match. A loop between `/sign-in` and the page means they do not: add the URL to `CLERK_AUTHORIZED_PARTIES` and redeploy.
3. **API routing.** `curl -s https://<preview-host>/api/v1/health` answers `{"status":"ok"}` and `/api/v1/ready` answers `{"status":"ready","checks":{"database":"ok"}}`. This confirms the platform sends `/api/v1/*` to the API ahead of the catch-all.
4. **The `/integrations` page.** **[YOU D16]** First put your Clerk user ID in `DIAGNOSTICS_OPERATOR_IDS` in the Preview scope and redeploy: nobody is an operator outside local development until you do. Find the ID in the Clerk Dashboard on the Users page ([Clerk: user profile](https://clerk.com/docs/guides/dashboard/user-profile)); it starts with `user_`. Then sign in, open `/integrations` and select **Run all server checks**. The PostgreSQL row must be Passed with the `app_login` role.
5. **Webhooks.** Send a test event from the Stripe destination and check the delivery shows `2xx`; confirm the Clerk endpoint the same way. A raw body must reach the API unchanged, or signatures fail.
6. **Telemetry.** Run the PostHog and Sentry checks on `/integrations` and confirm each event arrives in its dashboard. Then start a checkout on an otherwise idle deployment and confirm `checkout_started` arrives in PostHog. Whether the instance stays alive long enough after the response for queued events to be sent is NOT VERIFIED on Vercel.
7. **Migrations.** The Database migrate run for `preview` succeeded.
8. **Region and limits.** Compare the function's region with the configuration, and record cold start, duration and bundle size against Vercel's limits (the local bundle is 74.5 MiB; the documented limit is 500 MB).
9. **Production only, later:** the four crons register from the root file and call the routes on a production deployment; after-response work completes; Instant Rollback restores web, API and cron definitions together.

## Production

Go-live is a sequence of steps that need your accounts. Do them in this order; the DNS lead time (up to 48 hours) means starting step 1 early.

### Clerk production instance

**[YOU D13]** A production instance needs a domain you own ([Clerk: deploy to production](https://clerk.com/docs/guides/development/deployment/production), [Clerk: Vercel](https://clerk.com/docs/guides/development/deployment/vercel)).

1. Add the domain to the Vercel project and set `APP_BASE_URL` (Production) to its `https` origin.
2. In the Clerk Dashboard, create the production instance from the development instance's settings (Clerk copies most of them). Check afterwards that Organizations is enabled with **Membership optional** ([setup.md](setup.md#clerk)). Clerk also documents a guided command, `npx clerk@latest deploy` (NOT VERIFIED here).
3. Add exactly the DNS records the Domains page shows. Propagation can take up to 48 hours. On Cloudflare, set the record for the Frontend API subdomain to "DNS only" so it is not proxied. When every record verifies, a **Deploy certificates** button appears: select it. To check propagation from a terminal, query a record with `dig +short CNAME <name>` (the Frontend API record is usually `clerk.<your-domain>`; the Domains page shows the exact names).
4. Re-enter what does not copy over: "SSO connections, Integrations, and Paths settings do not copy over". Set Paths so sign-in and sign-up live on your application domain at `/sign-in` and `/sign-up`. Create your own OAuth client ID and secret for each social provider you enable ("In production, these are not secure and you will need to provide your own").
5. Create the production webhook endpoint: `https://<your-domain>/api/v1/webhooks/clerk`, with the five events in [integrations.md](integrations.md#clerk), and put its signing secret in `CLERK_WEBHOOK_SIGNING_SECRET` (Production). Without it, membership revocation falls back to the 300 s bound.
6. Set in the Production scope: `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` (`pk_live_`), `CLERK_SECRET_KEY` (`sk_live_`), `CLERK_AUTHORIZED_PARTIES` (every production origin), `CLERK_WEBHOOK_SIGNING_SECRET`, and `CLERK_JWT_KEY` empty or the production instance's PEM (a development PEM fails every token). Previews keep the development keys. Redeploy: the publishable key, `CLERK_JWT_KEY` and the CSP are fixed at build time. Never write production keys into a repository `.env`.
7. Sign up in production, and put that user ID in `DIAGNOSTICS_OPERATOR_IDS` (user IDs differ per instance).

### Stripe live mode

**[YOU D14]** Stripe keeps test-mode and live-mode objects apart ([Stripe: keys](https://docs.stripe.com/keys#test-live-modes)). Repeat in live mode what you did in the sandbox: create the Pro product with a monthly and a yearly recurring price in one currency in the Dashboard (`pnpm setup:stripe` refuses live keys); save the Customer Portal configuration and turn off bank debits and bank transfers under Settings > Payment methods; set the Retries rule ([setup.md](setup.md#stripe), S5 and S6); create the Snapshot event destination for the production URL with the 14 events ([integrations.md](integrations.md#stripe)); then set the live key, the two live price IDs, the ID of the live product (`STRIPE_PRODUCT_PRO`; copying a sandbox product to live mode makes a separate product with its own ID, [Stripe: manage products and prices](https://docs.stripe.com/products-prices/manage-prices)) and that destination's secret in the Production scope with `BILLING_MODE=live`. An optional custom domain: `STRIPE_CUSTOM_DOMAIN`. Run a real, small payment and refund it before launch.

### PostHog and Sentry production

**[YOU D15]** Create a PostHog production project and Sentry projects for production (or Sentry environments), set their keys in the Production scope with the modes `live`, and apply the IP settings ([setup.md](setup.md#posthog)). Optional source maps: the three Sentry build variables.

### Live verification

After go-live, with the owner's authorization (A5): set `DIAGNOSTICS_ENABLED=true` temporarily, run the Clerk and Stripe checks on `/integrations` and the membership-removal procedure ([integrations.md](integrations.md#checking-an-integration)), turn diagnostics off again, and record the result as `live` evidence.

## Rollback

- **Code and crons.** Use Vercel's Instant Rollback to the previous deployment ([Vercel: Instant Rollback](https://vercel.com/docs/instant-rollback)). It restores the cron definitions of that deployment and keeps the project's "Disable Cron Jobs" setting. Under Services the web service, the API and the crons are one deployment and revert together (NOT VERIFIED on the platform). Because migrations are backward compatible, the previous release runs against the current schema.
- **Schema.** There is no automatic down-migration in shared environments. Fix forward with a new migration.
- **Variables.** A variable change applies to new deployments only, so reverting one means changing it back and redeploying.

## Authorizations

Only the owner can give these; an agent never does them on its own initiative.

1. **[YOU A1]** Push to a remote repository, and any other change to remote repository state (settings, rulesets).
2. **[YOU A2]** The first preview deployment, with sandbox credentials. Then run [Verify the first preview](#verify-the-first-preview).
3. **[YOU A3]** Commands that change a provider account or a hosted database: `pnpm setup:stripe`, `pnpm check:setup --send-test-events`, `pnpm test:stripe-sandbox`, `pnpm db:migrate --allow-remote`, `pnpm db:api-login --allow-remote`, and the Database migrate workflow.
4. **[YOU A4]** A production release: deploying or promoting to production, and approving the production migration run.
5. **[YOU A5]** Live verification in production ([above](#live-verification)).

## Before you launch

The template leaves these decisions to each adopter ([ADR-0002](../dev-docs/adr/0002-organization-entitlements-contacts-adopter-decisions.md)). **[YOU D17]** Make and record them.

1. Security and conduct contacts: `pnpm init-template` asks for them, and CI fails while placeholders remain.
2. Data retention, deletion, export, and analytics consent: [data-inventory.md](data-inventory.md#adopter-decision-checklist).
3. Backup objectives, plus a timed hosted restore test: [operations.md](operations.md#backup-and-restore).
4. Performance targets, from your own preview baseline: [perf/run-plan-template.md](../perf/run-plan-template.md).
5. Sandbox verification of every integration you use: [integrations.md](integrations.md#status-of-each-integration).
6. The plan (Hobby is non-commercial), the Clerk production instance, and Stripe live mode (above).
