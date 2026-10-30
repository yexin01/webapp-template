#!/usr/bin/env bash
# DEC-001 local proof (spec §3.3). LOCAL ONLY: no Vercel project is linked or
# created, nothing is deployed, telemetry is off, and no provider is
# contacted. Package registries (npm, PyPI, Docker images) are, by the build.
# What each step shows, and what it cannot show, is in README.md.
#
#   proofs/dec-001/run.sh                   # full run, about 5 to 10 minutes
#   PROOF_COLD_RUNS=3 proofs/dec-001/run.sh # quicker
#
# The proof tests the committed HEAD: after the export, every script, config,
# and API file comes from the export, never from the working tree.
#
# Needs Docker Desktop: the pooler container reaches the local PostgreSQL
# (published on 127.0.0.1) through host.docker.internal. See README.md for Linux.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
TMP_BASE="${TMPDIR:-/tmp}"
WORK="${PROOF_WORK:-${TMP_BASE%/}/dec-001-proof}"
MARKER=".dec-001-proof-workdir"
RESULTS="$WORK/results"
DEV_PORT="${PROOF_DEV_PORT:-3910}"
RUNTIME_PORT="${PROOF_RUNTIME_PORT:-8920}"
POOLER_PORT="${PROOF_POOLER_PORT:-64329}"
COLD_RUNS="${PROOF_COLD_RUNS:-10}"
USERS="${PROOF_USERS:-40}"
DB_NAME=app_proof
# The runtime logs in as this role (DEC-008), not as the owner: APP_ENV=preview refuses an owner. Local-only values.
LOGIN_ROLE=dec001_login
export PROOF_LOGIN_PASSWORD="dec001-proof-login-password"
# Amazon Linux 2023 with Python 3.12 on x86_64: the closest public image to the platform's runtime.
RUNTIME_IMAGE="public.ecr.aws/lambda/python:3.12.2026.09.23.13-x86_64"
POOLER_IMAGE="edoburu/pgbouncer:v1.24.1-p1"
NETWORK=dec001-proof
POOLER=dec001-pooler
RUNTIME=dec001-runtime

export VERCEL_TELEMETRY_DISABLED=1 NEXT_TELEMETRY_DISABLED=1 NO_UPDATE_NOTIFIER=1
# The CLI reads a token and project link from the environment whatever -Q says.
unset VERCEL_TOKEN VERCEL_ORG_ID VERCEL_PROJECT_ID

PROOF_DIR=""  # the exported copy of this folder, set after the export
py() { uv run --project "$REPO/apps/api" --frozen --quiet python "$PROOF_DIR/proof.py" "$@"; }
log() { printf '\n== %s\n' "$*"; }
regex_escape() { printf '%s' "$1" | sed 's/[][\\.*^$+?(){}|]/\\&/g'; }
env_value() { sed -n "s/^$1=//p" "$WORK/repo/.env.example"; }
port_in_use() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }
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
  # pkill matches a regular expression, so the path is escaped.
  pkill -f "$(regex_escape "$WORK/repo/")" 2>/dev/null || true
}
cleanup() {
  stop_dev
  docker rm -f "$RUNTIME" "$POOLER" >/dev/null 2>&1 || true
  docker network rm "$NETWORK" >/dev/null 2>&1 || true
  if [[ -n "$PROOF_DIR" && -n "${ADMIN_URL:-}" ]]; then
    py db --admin-url "$ADMIN_URL" --name "$DB_NAME" --login-role "$LOGIN_ROLE" --drop >/dev/null 2>&1 || true
  fi
  # Generated test-only credentials are not kept; the results are.
  rm -rf "$WORK/keys" "$WORK/repo/.env"
}

for tool in docker vercel uv git node pnpm curl; do
  command -v "$tool" >/dev/null || { echo "missing tool: $tool" >&2; exit 2; }
done
# Only ever delete a work directory this script created.
if [[ -e "$WORK" && ! -f "$WORK/$MARKER" ]]; then
  echo "refusing to use $WORK: it exists and was not created by this script (no $MARKER)" >&2
  exit 2
fi
for port in "$DEV_PORT" "$RUNTIME_PORT" "$POOLER_PORT"; do
  if port_in_use "$port"; then echo "port $port is in use; set PROOF_DEV_PORT, PROOF_RUNTIME_PORT, or PROOF_POOLER_PORT" >&2; exit 2; fi
done
# Leftovers from an interrupted run are this proof's own, by name.
docker rm -f "$RUNTIME" "$POOLER" >/dev/null 2>&1 || true
docker network rm "$NETWORK" >/dev/null 2>&1 || true
trap cleanup EXIT

log "1. Export HEAD to $WORK/repo"
rm -rf "$WORK"
mkdir -p "$WORK/repo" "$WORK/no-credentials" "$RESULTS"
touch "$WORK/$MARKER"
git -C "$REPO" archive HEAD | tar -x -C "$WORK/repo"
PROOF_DIR="$WORK/repo/proofs/dec-001"
# The proof builds the root vercel.json that ships, exactly as committed.
[[ -f "$WORK/repo/vercel.json" ]] || { echo "HEAD has no root vercel.json: commit it first" >&2; exit 2; }
mkdir -p "$WORK/repo/.vercel"
# Local project settings, so `vercel build` never pulls settings from an account.
cat > "$WORK/repo/.vercel/project.json" <<'JSON'
{"projectId":"prj_local_proof","orgId":"team_local_proof","settings":{"framework":"services","rootDirectory":null,"installCommand":null,"buildCommand":null,"outputDirectory":null,"devCommand":null,"nodeVersion":"24.x"}}
JSON
# The local database from `pnpm db:up`, as .env.example describes it (init-template keeps it current).
ADMIN_URL="$(env_value DATABASE_URL)"
DB_SERVER="${ADMIN_URL%/*}"            # postgresql://user:password@host:port
DB_PORT="${DB_SERVER##*:}"
# One instance may hold both of its pools: request (app_api) plus worker (app_worker) connections.
MAX_DB_CLIENTS=$(( $(env_value DATABASE_POOL_MAX_SIZE) + $(env_value DATABASE_WORKER_POOL_MAX_SIZE) ))
cat > "$RESULTS/meta.json" <<JSON
{
  "date": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "commit": "$(git -C "$REPO" rev-parse --short HEAD)",
  "working_tree_changes_ignored": $(git -C "$REPO" status --porcelain | wc -l | tr -d ' '),
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
py db --admin-url "$ADMIN_URL" --name "$DB_NAME" --login-role "$LOGIN_ROLE"

log "3. vercel build (no Vercel credentials; installs from npm and PyPI)"
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
  --max-db-clients "$MAX_DB_CLIENTS" --proof-json "$WORK/keys/proof.json" --results "$RESULTS" \
  --database "$DB_NAME" --admin-url "$ADMIN_URL"
stop_dev

log "5. PgBouncer in transaction mode (stand-in for a transaction-mode pooler)"
docker network create "$NETWORK" >/dev/null
# PgBouncer's resolver (c-ares) does not resolve Docker Desktop's host name, so pass the address.
HOST_GATEWAY="$(docker run --rm --entrypoint getent "$POOLER_IMAGE" hosts host.docker.internal | awk 'NR==1 {print $1}' || true)"
if [[ -z "$HOST_GATEWAY" ]]; then
  echo "host.docker.internal does not resolve inside containers: this proof needs Docker Desktop (see README.md)" >&2
  exit 2
fi
docker run -d --name "$POOLER" --network "$NETWORK" -p "127.0.0.1:$POOLER_PORT:5432" \
  -e DB_HOST="$HOST_GATEWAY" -e DB_PORT="$DB_PORT" -e DB_USER="$LOGIN_ROLE" -e DB_PASSWORD="$PROOF_LOGIN_PASSWORD" \
  -e AUTH_TYPE=scram-sha-256 -e POOL_MODE=transaction -e DEFAULT_POOL_SIZE=4 -e MAX_CLIENT_CONN=500 \
  -e MAX_PREPARED_STATEMENTS=0 -e ADMIN_USERS="$LOGIN_ROLE" "$POOLER_IMAGE" >/dev/null
LOGIN_AUTH="$LOGIN_ROLE:$PROOF_LOGIN_PASSWORD"
py session --pooler-url "postgresql://$LOGIN_AUTH@127.0.0.1:$POOLER_PORT/$DB_NAME" --results "$RESULTS"

log "6. Packaged function (linux/amd64, Vercel's runtime shim) through the pooler"
# sslmode=disable: a remote host gets sslmode=require by default, and this local PgBouncer stands in for Supavisor without TLS.
POOLED_URL="postgresql://$LOGIN_AUTH@$POOLER:5432/$DB_NAME?sslmode=disable"
py runtime --name "$RUNTIME" --port "$RUNTIME_PORT" --env-file "$WORK/repo/.env" --database-url "$POOLED_URL" \
  --task "$WORK/task" --results "$RESULTS" --image "$RUNTIME_IMAGE" --network "$NETWORK"
py probe --base-url "http://127.0.0.1:$RUNTIME_PORT" --label packaged-runtime --users "$USERS" --vercel-ids \
  --max-db-clients "$MAX_DB_CLIENTS" --proof-json "$WORK/keys/proof.json" --results "$RESULTS" \
  --database "$DB_NAME" --admin-url "$ADMIN_URL" \
  --pooler-admin-url "postgresql://$LOGIN_AUTH@127.0.0.1:$POOLER_PORT/pgbouncer"
py outage --base-url "http://127.0.0.1:$RUNTIME_PORT" --proof-json "$WORK/keys/proof.json" --vercel-ids \
  --env-file "$WORK/repo/.env" --pooler "$POOLER" --results "$RESULTS"
docker logs "$RUNTIME" > "$RESULTS/runtime-container.log" 2>&1 || true
docker rm -f "$RUNTIME" >/dev/null
# Labeled requests sent to the packaged runtime: the probe's isolation step
# sends 8 per user, its other checks 19, and the outage step 26.
py ipc --results "$RESULTS" --min-requests "$(( 8 * USERS + 45 ))"

log "7. Cold starts ($COLD_RUNS runs each)"
py coldstart --runs "$COLD_RUNS" --env-file "$WORK/repo/.env" --database-url "$POOLED_URL" \
  --native-database-url "$DB_SERVER/$DB_NAME" \
  --task "$WORK/task" --results "$RESULTS" --image "$RUNTIME_IMAGE" --network "$NETWORK"

log "8. Report"
py report --results "$RESULTS"
echo "Results: $RESULTS/summary.md"
