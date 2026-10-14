#!/usr/bin/env bash
# DEC-001 local proof (spec §3.3). LOCAL ONLY: no Vercel project is linked,
# nothing is deployed, telemetry is off, and no provider is contacted.
# What each step shows, and what it cannot show, is in README.md.
#
#   proofs/dec-001/run.sh                   # full run, about 10 minutes
#   PROOF_COLD_RUNS=3 proofs/dec-001/run.sh # quicker
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
TMP_BASE="${TMPDIR:-/tmp}"
WORK="${PROOF_WORK:-${TMP_BASE%/}/dec-001-proof}"
RESULTS="$WORK/results"
DEV_PORT="${PROOF_DEV_PORT:-3910}"
RUNTIME_PORT="${PROOF_RUNTIME_PORT:-8920}"
POOLER_PORT="${PROOF_POOLER_PORT:-64329}"
COLD_RUNS="${PROOF_COLD_RUNS:-10}"
USERS="${PROOF_USERS:-40}"
DB_NAME=app_proof
# The local database from `pnpm db:up`, as .env.example describes it (init-template keeps it current).
ADMIN_URL="$(sed -n 's/^DATABASE_URL=//p' "$REPO/.env.example")"
DB_SERVER="${ADMIN_URL%/*}"            # postgresql://user:password@host:port
DB_PORT="${DB_SERVER##*:}"
# Amazon Linux 2023 with Python 3.12 on x86_64: the closest public image to the platform's runtime.
RUNTIME_IMAGE="public.ecr.aws/lambda/python:3.12.2026.09.23.13-x86_64"
POOLER_IMAGE="edoburu/pgbouncer:v1.24.1-p1"
NETWORK=dec001-proof
POOLER=dec001-pooler
RUNTIME=dec001-runtime

export VERCEL_TELEMETRY_DISABLED=1 NEXT_TELEMETRY_DISABLED=1

py() { uv run --project "$REPO/apps/api" --frozen --quiet python "$HERE/proof.py" "$@"; }
log() { printf '\n== %s\n' "$*"; }
wait_http() {
  local url="$1" limit="$2" pid="${3:-}" start=$SECONDS
  until curl -fsS -o /dev/null "$url" 2>/dev/null; do
    if [[ -n "$pid" ]] && ! kill -0 "$pid" 2>/dev/null; then echo "server exited before $url answered" >&2; return 1; fi
    if (( SECONDS - start > limit )); then echo "timed out waiting for $url" >&2; return 1; fi
    sleep 1
  done
}

DEV_PID=""
stop_dev() {
  if [[ -n "$DEV_PID" ]]; then
    kill "$DEV_PID" 2>/dev/null || true
    wait "$DEV_PID" 2>/dev/null || true
    DEV_PID=""
  fi
  # vercel dev's children (next dev, the Python server) run from the export.
  pkill -f "$WORK/repo" 2>/dev/null || true
}
cleanup() {
  stop_dev
  docker rm -f "$RUNTIME" "$POOLER" >/dev/null 2>&1 || true
  docker network rm "$NETWORK" >/dev/null 2>&1 || true
}
trap cleanup EXIT

for tool in docker vercel uv git node pnpm curl; do
  command -v "$tool" >/dev/null || { echo "missing tool: $tool" >&2; exit 2; }
done

log "1. Export HEAD to $WORK/repo"
rm -rf "$WORK"
mkdir -p "$WORK/repo" "$WORK/no-credentials" "$RESULTS"
git -C "$REPO" archive HEAD | tar -x -C "$WORK/repo"
cp "$HERE/vercel.json" "$WORK/repo/vercel.json"
mkdir -p "$WORK/repo/.vercel"
# Local project settings, so `vercel build` never pulls settings from an account.
cat > "$WORK/repo/.vercel/project.json" <<'JSON'
{"projectId":"prj_local_proof","orgId":"team_local_proof","settings":{"framework":"services","rootDirectory":null,"installCommand":null,"buildCommand":null,"outputDirectory":null,"devCommand":null,"nodeVersion":"24.x"}}
JSON
cat > "$RESULTS/meta.json" <<JSON
{
  "date": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "commit": "$(git -C "$REPO" rev-parse --short HEAD)",
  "uncommitted_paths": $(git -C "$REPO" status --porcelain | wc -l | tr -d ' '),
  "host": "$(uname -sm)",
  "vercel_cli": "$(vercel --version 2>/dev/null | tail -1)",
  "docker": "$(docker version --format '{{.Server.Version}}')",
  "runtime_image": "$RUNTIME_IMAGE",
  "pooler_image": "$POOLER_IMAGE"
}
JSON

log "2. Proof-only environment and database"
py env --out "$WORK/repo/.env" --keys "$WORK/keys" --port "$DEV_PORT" \
  --database-url "$DB_SERVER/$DB_NAME"
(cd "$REPO" && pnpm --silent db:up)
py db --admin-url "$ADMIN_URL" --name "$DB_NAME"

log "3. vercel build (offline, no credentials)"
(cd "$WORK/repo" && vercel build -Q "$WORK/no-credentials" --non-interactive) > "$RESULTS/vercel-build.log" 2>&1
py bundle --root "$WORK/repo" --output "$WORK/repo/.vercel/output" --task "$WORK/task" --results "$RESULTS"
(cd "$WORK/repo" && node scripts/check-bundle-secrets.mjs) | tee "$RESULTS/bundle-secrets.log"

log "4. vercel dev -L: Services routing on this machine"
# A project file makes the CLI treat the folder as linked and try to fetch the project.
mv "$WORK/repo/.vercel/project.json" "$WORK/repo/.vercel/project.build.json"
(cd "$WORK/repo" && exec vercel dev -L --listen "127.0.0.1:$DEV_PORT" -Q "$WORK/no-credentials" --non-interactive) \
  > "$RESULTS/vercel-dev.log" 2>&1 &
DEV_PID=$!
wait_http "http://127.0.0.1:$DEV_PORT/api/v1/health" 300 "$DEV_PID"
py probe --base-url "http://127.0.0.1:$DEV_PORT" --label vercel-dev --web --users "$USERS" \
  --proof-json "$WORK/keys/proof.json" --results "$RESULTS" --database "$DB_NAME" --admin-url "$ADMIN_URL"
stop_dev

log "5. PgBouncer in transaction mode (stand-in for a transaction-mode pooler)"
docker network create "$NETWORK" >/dev/null
# PgBouncer's resolver (c-ares) does not resolve Docker Desktop's host name, so pass the address.
HOST_GATEWAY="$(docker run --rm --entrypoint getent "$POOLER_IMAGE" hosts host.docker.internal | awk 'NR==1 {print $1}')"
docker run -d --name "$POOLER" --network "$NETWORK" -p "127.0.0.1:$POOLER_PORT:5432" \
  -e DB_HOST="$HOST_GATEWAY" -e DB_PORT="$DB_PORT" -e DB_USER=postgres -e DB_PASSWORD=postgres \
  -e AUTH_TYPE=scram-sha-256 -e POOL_MODE=transaction -e DEFAULT_POOL_SIZE=4 -e MAX_CLIENT_CONN=500 \
  -e MAX_PREPARED_STATEMENTS=0 -e ADMIN_USERS=postgres "$POOLER_IMAGE" >/dev/null
py session --pooler-url "postgresql://postgres:postgres@127.0.0.1:$POOLER_PORT/$DB_NAME" --results "$RESULTS"

log "6. Packaged function (linux/amd64, Vercel's runtime shim) through the pooler"
POOLED_URL="postgresql://postgres:postgres@$POOLER:5432/$DB_NAME"
py runtime --name "$RUNTIME" --port "$RUNTIME_PORT" --env-file "$WORK/repo/.env" --database-url "$POOLED_URL" \
  --task "$WORK/task" --results "$RESULTS" --image "$RUNTIME_IMAGE" --network "$NETWORK"
py probe --base-url "http://127.0.0.1:$RUNTIME_PORT" --label packaged-runtime --users "$USERS" \
  --proof-json "$WORK/keys/proof.json" --results "$RESULTS" --database "$DB_NAME" --admin-url "$ADMIN_URL" \
  --pooler-admin-url "postgresql://postgres:postgres@127.0.0.1:$POOLER_PORT/pgbouncer"
py outage --base-url "http://127.0.0.1:$RUNTIME_PORT" --proof-json "$WORK/keys/proof.json" \
  --pooler "$POOLER" --results "$RESULTS"
docker logs "$RUNTIME" > "$RESULTS/runtime-container.log" 2>&1 || true
docker rm -f "$RUNTIME" >/dev/null

log "7. Cold starts ($COLD_RUNS runs each)"
py coldstart --runs "$COLD_RUNS" --env-file "$WORK/repo/.env" --database-url "$POOLED_URL" \
  --native-database-url "$DB_SERVER/$DB_NAME" \
  --task "$WORK/task" --results "$RESULTS" --image "$RUNTIME_IMAGE" --network "$NETWORK"

log "8. Report"
py report --results "$RESULTS"
echo "Results: $RESULTS/summary.md"
