#!/usr/bin/env sh
# Audits exactly the versions pinned in apps/api/uv.lock (runtime and dev).
set -eu
cd "$(dirname "$0")/../apps/api"
req="$(mktemp)"
trap 'rm -f "$req"' EXIT
uv export --frozen --no-emit-project --no-hashes --no-header > "$req"
uv run --no-sync pip-audit --requirement "$req" --no-deps --disable-pip --strict --progress-spinner off
