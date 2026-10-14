# DEC-001 proof: Python API on Vercel (local part)

Disposable proof for [DEC-001](../../dev-docs/engineering-implementation-plan.md#dec-001--python-backend-on-vercel). It packages the real template with the Vercel CLI and exercises the packaged function **on this machine only**. Results are recorded in [`dev-docs/evidence/dec-001-vercel-python.md`](../../dev-docs/evidence/dec-001-vercel-python.md).

Nothing here is template code. Delete this folder once DEC-001 is resolved.

## Safety

- No Vercel project is linked, created, or deployed. `vercel build` and `vercel dev -L` run with an empty global configuration (`-Q`), so they hold no credentials. Vercel and Next.js telemetry are disabled for the run.
- Every credential is generated for the run and is test-only: an RSA key pair for session tokens, a Stripe webhook signing secret, and a cron secret. They live in the work directory, never in the repository.
- No provider is contacted. Billing and LLM are mocked, and the Clerk Backend API URL points at a closed loopback port.
- The proof uses its own database (`app_proof`) on the local PostgreSQL from `pnpm db:up`, and its own Docker network and containers (`dec001-*`). They are removed at the end.

## Run

```bash
pnpm db:up
proofs/dec-001/run.sh                     # about 10 minutes
PROOF_COLD_RUNS=3 proofs/dec-001/run.sh   # quicker
```

Requires Docker, the Vercel CLI, uv, pnpm, and Node. Results go to `$TMPDIR/dec-001-proof/results/` (`summary.md` plus raw JSON); set `PROOF_WORK` to choose another directory.

## What each step shows

| Step | What runs | What it shows | What it cannot show |
|---|---|---|---|
| 1. Export | `git archive HEAD` plus the candidate [`vercel.json`](vercel.json) (Vercel Services: `web` and `api` in one project) | The proof tests committed code | — |
| 3. Build | `vercel build`, offline | The monorepo builds as one Services project. The Python function installs from `uv.lock` with Linux x86_64 wheels. Bundle size, contents, routes, and crons. Server-only values absent from the browser bundle | Build behavior on Vercel's build machines |
| 4. Local Services | `vercel dev -L` | `/api/v1/*` reaches the API and everything else reaches Next.js. Raw-body webhooks, cron route, auth rejections, and concurrent isolation through the local Services router | The production router, CDN, or Firewall |
| 5. Pooler | PgBouncer 1.24 in **transaction** mode, prepared statements off | Transaction-local settings do not follow a server connection to the next client. A session-level control does leak, which shows the check works | Supabase's own pooler (Supavisor) |
| 6. Packaged runtime | The packaged function in `public.ecr.aws/lambda/python` (Amazon Linux 2023, x86_64), started by Vercel's own runtime shim (`vc__handler__python` → `vercel_runtime`) through [`emulator.py`](emulator.py), a stand-in for the platform's IPC host | The bundle is self-contained and starts from a read-only `/var/task`. One instance serves concurrent requests without cross-request leakage. Pool bounds through the pooler. Behavior when the database hangs or goes down, and recovery | Fluid Compute scheduling, instance freezing, platform limits (duration, payload, memory), and real network latency |
| 7. Cold starts | 10 fresh starts of the packaged function (x86_64 emulated on this host), plus 10 of the source tree natively | The runtime's own `initDuration` breakdown and time to first response. A **relative** baseline only | Platform cold starts (bytecode caching, pre-warming, region) |

## What still needs an authorized preview deployment

These items stay `NOT VERIFIED` until the owner authorizes a preview deployment with sandbox credentials:

- cold starts and limits observed on the platform (duration, 4.5 MB payload, memory);
- behavior through the production router: raw body, headers, and preview URLs;
- Supabase pooler connection counts and latency;
- genuine Stripe sandbox webhook deliveries and retries;
- Vercel Cron invoking the job routes;
- after-response outbox dispatch when the platform freezes the instance (deviation D-002).

With authorization, the same build output can be deployed to a preview environment, and `proof.py probe` can be pointed at the preview URL.
