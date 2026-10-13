# Operations

Operational guidance for adopters. Items that depend on unresolved decisions are marked; do not treat them as verified.

## Environments

| Environment | Database | Integration modes | Notes |
|---|---|---|---|
| `local` | Docker PostgreSQL (`pnpm db:up`) or a non-production Supabase project | `mock` or `sandbox` | Developer keys only |
| `test` | Ephemeral database created by the test suite | `mock` | No `.env` is read |
| `preview` | Isolated non-production database with synthetic data | `sandbox` | Preview-scoped keys |
| `production` | Production database | `live` only | Startup rejects `mock` and `sandbox` |

Startup validation enforces key/mode consistency: live keys outside production, or test keys in production, prevent the API from starting.

## Deployment

> **DEC-001 is open.** Running the Python API on Vercel has not been verified. Until the proof in spec §3.3 is completed and recorded, do not rely on these steps for production.

The intended topology (ADR-0001):

- The Next.js app is deployed on Vercel.
- The Python API is served by Vercel's Python runtime, via `apps/api/index.py`, which exports an ASGI app.
- The browser reaches the API through the web app's `/api/v1/*` rewrite. `API_ORIGIN` is read at **build time**.

Before a first deployment:

1. Complete the DEC-001 proof in a preview environment and record the findings.
2. Configure environment variables per environment in the hosting provider. Never copy production values elsewhere.
3. Apply migrations (below) **before** deploying code that depends on them.
4. Add platform rate-limit rules for coarse per-IP limits (DEC-011). Application-level limits use PostgreSQL counters.

Deploying, promoting, or changing hosting and Git settings are remote actions. They require explicit authorization from the repository owner.

## Migrations

- Migrations live in `supabase/migrations/`.
- They are ordered, checksummed, and immutable once applied. The runner refuses edited migrations.
- Run them with `pnpm db:migrate`. For a non-local database, add `--allow-remote` and use a direct connection, and only with authorization.
- Use **expand → deploy → contract** for changes that the previous release must survive: add new columns or tables first, deploy code that uses them, and remove old structures in a later release.
- A destructive change (drop, rename, type narrowing, or data rewrite) requires:
  - review;
  - a verified backup;
  - owner authorization before it runs against any shared environment.

## Rollback

- **Code**: redeploy the previous release. Because migrations are backward compatible, the previous release runs against the current schema.
- **Schema**: there is no automatic down-migration in shared environments. Fix forward with a new migration.

## Health and readiness

- `GET /api/v1/health`: liveness only, with no dependency calls.
- `GET /api/v1/ready`: checks the database within the statement timeout and returns `503` with `{"status":"not_ready"}` when it is unavailable. It does not reveal configuration.

## Provider outages

| Provider down | Behavior |
|---|---|
| Clerk | New sign-ins fail. Requests with valid tokens continue while cached keys are valid, then fail closed (`503`). Stale organization memberships cannot be re-verified, so organization requests fail closed (`503`). |
| Stripe | Checkout and portal return `503`. Existing entitlements (from synchronized records) are unaffected. Stripe retries webhooks; run reconciliation afterwards. |
| Database | Readiness fails. Data requests return `503`. No partial writes. |
| PostHog / Sentry | No user-visible impact. Exports fail silently and are logged. |
| LLM provider | Only the connectivity check reports failure. |

## Routine tasks

| Task | Command | Frequency |
|---|---|---|
| Billing reconciliation (dry run) | `pnpm billing:reconcile` | After incidents or missed webhooks |
| Apply reconciliation | `pnpm billing:reconcile --apply` | When a dry run shows divergences you have reviewed |
| Prune rate-limit counters | `cd apps/api && uv run python -m app.cli prune-rate-limits` | Daily (scheduling is not yet configured) |
| Dependency audit | `pnpm security:audit` | Weekly and before releases |

## Secret rotation

Rotate immediately after any suspected exposure, and periodically otherwise.

1. **Clerk**: create a new secret key, deploy it, then revoke the old key. For webhooks, roll the endpoint's signing secret.
2. **Stripe**: roll the API key, preferably a restricted key with the minimum permissions. Roll each webhook endpoint's signing secret separately, per environment.
3. **Database**: rotate the database password in Supabase, update `DATABASE_URL` everywhere, then restart.
4. **LLM / Sentry auth token / PostHog**: rotate the key at the provider and update the environment.

After rotating, confirm the old secret no longer works and record the rotation, without values, in your incident log.

## Incident response

1. **Contain**: revoke or rotate exposed credentials, and disable affected features through configuration.
2. **Assess**: use request IDs (the `X-Request-ID` header, also present in logs and Sentry) to trace requests. Logs never contain tokens or request bodies.
3. **Recover**: deploy fixes forward, run billing reconciliation if billing was affected, and verify with the readiness endpoint.
4. **Review**: write a short report and update the threat model (spec §13.2) and tests.

Vulnerability reports from outside the team follow [SECURITY.md](../SECURITY.md).

## Backup and restore

> **DEC-005 is open.** Recovery objectives (RPO and RTO), backup frequency, and the restore procedure have not been approved or tested. Until they are, make no recovery-readiness claims.

Required before production: confirm the backup capabilities of your Supabase plan, approve the objectives, and perform a timed restore into an isolated project with synthetic data. Record the results in `dev-docs/evidence/`.

## Data retention and deletion

> **DEC-004 is open.** Account deletion, data export, and retention periods have not been defined.

Current behavior:

- `user.deleted` from Clerk revokes all of the user's organization memberships. Application data and billing records are retained.
- Webhook ledgers store event IDs and types only, not payloads.
- Telemetry retention follows each provider's project settings.

## Python runtime limitations

To be filled in from the DEC-001 proof: supported Python versions, maximum duration, payload limits, cold starts, connection behavior, and bundle size.
