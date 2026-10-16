#!/usr/bin/env bash
# Real-stack browser test: the web UI against a real server, a real database and the real worker.
#
#   make e2e-real            # needs `make dev-up` (postgres) and the Playwright browser
#
# Creates the database `rushbase_e2e_web` and a throw-away library below dev/data/e2e-real/,
# seeds it with the synthetic test media, starts the server on :18083 and the Vite dev server on
# :15175, runs the `e2e-real` Playwright project and removes everything again.
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/dev-env.sh
source "${SCRIPT_DIR}/dev-env.sh"
cd "${RUSHBASE_ROOT}"

log() { echo "[INFO] $*"; }
fail() { echo "[FATAL] $*" >&2; exit 1; }

ENV_FILE="dev/data/dev.env"
[[ -f "${ENV_FILE}" ]] || fail "${ENV_FILE} missing; run 'make dev-up' first"
set -a
# shellcheck disable=SC1090
source "${ENV_FILE}"
set +a

DB_NAME="rushbase_e2e_web"
PG_CONTAINER="${RUSHBASE_E2E_PG_CONTAINER:-rushbase-dev-postgres-1}"
SERVER_PORT=18083
WEB_PORT=15175
WORK="${RUSHBASE_ROOT}/dev/data/e2e-real"
LIBRARY="${WORK}/library"
WORKER_DATA="${WORK}/worker-data"

psql_admin() { docker exec "${PG_CONTAINER}" psql -U rushbase -d postgres -v ON_ERROR_STOP=1 -qc "$1"; }

SERVER_PID=""
cleanup() {
  [[ -n "${SERVER_PID}" ]] && kill "${SERVER_PID}" 2>/dev/null || true
  [[ -n "${SERVER_PID}" ]] && wait "${SERVER_PID}" 2>/dev/null || true
  psql_admin "DROP DATABASE IF EXISTS ${DB_NAME} WITH (FORCE)" >/dev/null 2>&1 || true
  if [[ "${KEEP_E2E_DATA:-0}" != "1" ]]; then rm -rf "${WORK}"; fi
}
trap cleanup EXIT INT TERM

for port in "${SERVER_PORT}" "${WEB_PORT}"; do
  if (exec 3<>"/dev/tcp/127.0.0.1/${port}") 2>/dev/null; then fail "port ${port} is already in use"; fi
done

log "preparing database ${DB_NAME} and library ${LIBRARY}"
rm -rf "${WORK}"
mkdir -p "${LIBRARY}" "${WORKER_DATA}"
psql_admin "DROP DATABASE IF EXISTS ${DB_NAME} WITH (FORCE)"
psql_admin "CREATE DATABASE ${DB_NAME}"
E2E_DB_URL="$(python3 - <<'PY'
import os
from urllib.parse import urlsplit, urlunsplit
parts = urlsplit(os.environ["RUSHBASE_DATABASE_URL"])
print(urlunsplit(parts._replace(path="/rushbase_e2e_web")))
PY
)"

export RUSHBASE_DATABASE_URL="${E2E_DB_URL}"
export RUSHBASE_LIBRARY_ROOT="${LIBRARY}"
export RUSHBASE_PORT="${SERVER_PORT}"
export RUSHBASE_SCAN_INTERVAL_SECONDS=2
export RUSHBASE_STABLE_SCANS=1
export RUSHBASE_STABLE_SECONDS=0
export RUSHBASE_MAINTENANCE_INTERVAL_SECONDS=2

log "generating test media"
uv run python dev/testmedia/generate.py --library "${LIBRARY}" >/dev/null
uv run rushbase-server migrate >/dev/null

log "starting the server on :${SERVER_PORT}"
uv run rushbase-server >"${WORK}/server.log" 2>&1 &
SERVER_PID=$!
for _ in $(seq 1 60); do
  curl -fsS "http://127.0.0.1:${SERVER_PORT}/health" >/dev/null 2>&1 && break
  sleep 1
done
curl -fsS "http://127.0.0.1:${SERVER_PORT}/health" >/dev/null || { cat "${WORK}/server.log" >&2; fail "server did not start"; }

export RUSHBASE_E2E_SERVER_URL="http://127.0.0.1:${SERVER_PORT}"
export RUSHBASE_E2E_WEB_PORT="${WEB_PORT}"
export RUSHBASE_E2E_WORKER_DATA="${WORKER_DATA}"
export RUSHBASE_E2E_LIBRARY="${LIBRARY}"
export RUSHBASE_E2E_ROOT="${RUSHBASE_ROOT}"

log "running the Playwright project e2e-real"
pnpm --filter @rushbase/web exec playwright test -c playwright.real.config.ts "$@"
