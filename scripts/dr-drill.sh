#!/usr/bin/env bash
# Disaster-recovery drill: proves both recovery paths end to end, fully isolated.
#
#   scripts/dr-drill.sh            # needs `make dev-up` (PostgreSQL) or RUSHBASE_DATABASE_URL
#
# Server A builds a library, processes it with the CPU worker, takes human edits and a backup.
# Server loss is simulated (kill -9, database dropped). Path 1: server B on an empty database
# restores the newest backup from the share. Path 2: with the backups moved away, server C on
# another empty database runs `rebuild-from-sidecars`. Databases are named rushbase_dr_{a,b,c};
# the library lives in a temp directory below dev/data/dr-drill/. Everything is removed again
# (set KEEP_DR_DATA=1 to keep the directory).
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/dev-env.sh
source "${SCRIPT_DIR}/dev-env.sh"
cd "${RUSHBASE_ROOT}"

if [[ -t 1 ]]; then
  C_CYAN=$'\033[36m'; C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'
  C_RED=$'\033[31m'; C_BOLD=$'\033[1m'; C_RESET=$'\033[0m'
else
  C_CYAN=''; C_GREEN=''; C_YELLOW=''; C_RED=''; C_BOLD=''; C_RESET=''
fi
log()    { echo "${C_CYAN}[INFO]${C_RESET} $*"; }
ok()     { echo "${C_GREEN}[OK]${C_RESET} $*"; }
warn()   { echo "${C_YELLOW}[WARN]${C_RESET} $*" >&2; }
err()    { echo "${C_RED}[ERROR]${C_RESET} $*" >&2; }
fail()   { echo "${C_RED}[FATAL]${C_RESET} $*" >&2; exit 1; }
banner() { echo "${C_BOLD}${C_CYAN}━━━ $* ━━━${C_RESET}"; }

# --- configuration ------------------------------------------------------------------------------
if [[ -z "${RUSHBASE_DATABASE_URL:-}" ]]; then
  env_file="${RUSHBASE_DR_ENV_FILE:-}"
  if [[ -z "${env_file}" ]]; then
    dir="${RUSHBASE_ROOT}"
    while [[ "${dir}" != "/" ]]; do
      if [[ -f "${dir}/dev/data/dev.env" ]]; then env_file="${dir}/dev/data/dev.env"; break; fi
      dir="$(dirname "${dir}")"
    done
  fi
  [[ -f "${env_file}" ]] || fail "no RUSHBASE_DATABASE_URL and no dev/data/dev.env; run 'make dev-up' first"
  set -a
  # shellcheck disable=SC1090
  source "${env_file}"
  set +a
fi
[[ -n "${RUSHBASE_SECRET_KEY:-}" ]] || fail "RUSHBASE_SECRET_KEY is not set"

# Without a local pg_dump, use the dev PostgreSQL container (development only).
if ! command -v pg_dump >/dev/null 2>&1 && [[ -z "${RUSHBASE_PG_DOCKER_CONTAINER:-}" ]]; then
  export RUSHBASE_PG_DOCKER_CONTAINER="rushbase-dev-postgres-1"
fi

PORT="${RUSHBASE_DR_PORT:-18090}"
WORK="${RUSHBASE_ROOT}/dev/data/dr-drill"
LIBRARY="${WORK}/library"
WORKER_DATA="${WORK}/worker-data"
# Resolve the console script once so that SERVER_PID is the server itself (not a `uv run` wrapper).
SERVER_BIN="$(uv run python -c 'import shutil; print(shutil.which("rushbase-server"))')"
[[ -x "${SERVER_BIN}" ]] || fail "rushbase-server is not installed; run 'make setup'"
HELPER=(uv run python "${SCRIPT_DIR}/lib/dr_drill.py")
BASE_DB_URL="${RUSHBASE_DATABASE_URL}"
LEASE_SECONDS=20

export DR_BASE_URL="http://127.0.0.1:${PORT}"
export DR_USER="drill-admin"
DR_PASS="$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")"
DR_SETUP_TOKEN="$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")"
export DR_PASS DR_SETUP_TOKEN

RESULTS=()
SERVER_PID=""
DB_NAMES=(rushbase_dr_a rushbase_dr_b rushbase_dr_c)

db_url() {
  BASE_DB_URL="${BASE_DB_URL}" DB="$1" python3 - <<'PY'
import os
from urllib.parse import urlsplit, urlunsplit
parts = urlsplit(os.environ["BASE_DB_URL"])
print(urlunsplit(parts._replace(path="/" + os.environ["DB"])))
PY
}

check() { # check "<description>" <command...>
  local description="$1"; shift
  if "$@" >"${WORK}/check.out" 2>&1; then
    RESULTS+=("PASS  ${description}"); ok "${description}"
  else
    RESULTS+=("FAIL  ${description}"); err "${description}"; sed 's/^/    /' "${WORK}/check.out" >&2
  fi
}

stop_server() {
  if [[ -n "${SERVER_PID}" ]]; then
    kill "${SERVER_PID}" 2>/dev/null || true
    wait "${SERVER_PID}" 2>/dev/null || true
    SERVER_PID=""
  fi
}

cleanup() {
  local status=$?
  stop_server
  for name in "${DB_NAMES[@]}"; do
    RUSHBASE_DATABASE_URL="${BASE_DB_URL}" "${HELPER[@]}" db drop "${name}" >/dev/null 2>&1 || true
  done
  if [[ "${KEEP_DR_DATA:-0}" != "1" ]]; then rm -rf "${WORK}"; fi
  exit "${status}"
}
trap cleanup EXIT INT TERM

# Environment of the server process for one database.
server_env() { # server_env <db-name>
  export RUSHBASE_DATABASE_URL RUSHBASE_LIBRARY_ROOT RUSHBASE_PORT RUSHBASE_HOST
  RUSHBASE_DATABASE_URL="$(db_url "$1")"
  RUSHBASE_LIBRARY_ROOT="${LIBRARY}"
  RUSHBASE_PORT="${PORT}"
  RUSHBASE_HOST="127.0.0.1"
  export RUSHBASE_SETUP_TOKEN="${DR_SETUP_TOKEN}"
  export RUSHBASE_SCAN_INTERVAL_SECONDS=2 RUSHBASE_STABLE_SCANS=1 RUSHBASE_STABLE_SECONDS=0
  export RUSHBASE_MAINTENANCE_INTERVAL_SECONDS=2
  export RUSHBASE_SERVER_LEASE_SECONDS="${LEASE_SECONDS}" RUSHBASE_SERVER_LEASE_HEARTBEAT_SECONDS=5
}

start_server() { # start_server <db-name> <log-name> [extra args]
  local name="$1" logname="$2"; shift 2
  server_env "${name}"
  "${SERVER_BIN}" "$@" >"${WORK}/${logname}.log" 2>&1 &
  SERVER_PID=$!
  "${HELPER[@]}" wait-health 60 || { cat "${WORK}/${logname}.log" >&2; return 1; }
}

worker() {
  RUSHBASE_DATA_DIR="${WORKER_DATA}" RUSHBASE_LIBRARY_ROOT="${LIBRARY}" uv run rushbase "$@"
}

enroll_and_process() { # enroll_and_process <worker-name> [skip-enroll]
  if [[ "${2:-}" != "skip" ]]; then
    local token
    token="$("${HELPER[@]}" enroll-token)"
    rm -rf "${WORKER_DATA}"; mkdir -p "${WORKER_DATA}"
    worker enroll --server "${DR_BASE_URL}" --token "${token}" --name "$1" >/dev/null
  fi
  worker process --stages probe,hash,preview,shots --exit-when-empty >"${WORK}/worker.log" 2>&1
}


# --- preparation -----------------------------------------------------------------------------
banner "Preparation"
if (exec 3<>"/dev/tcp/127.0.0.1/${PORT}") 2>/dev/null; then fail "port ${PORT} is in use"; fi
rm -rf "${WORK}"
mkdir -p "${LIBRARY}" "${WORKER_DATA}" "${WORK}/heldback"
log "generating test media"
uv run python dev/testmedia/generate.py --library "${LIBRARY}" >/dev/null
# Hold one batch back: it arrives after the restore to prove the worker can continue.
held="$(find "${LIBRARY}/Input" -mindepth 1 -maxdepth 1 -type d | sort | tail -n 1)"
[[ -n "${held}" ]] || fail "generator produced no input batch"
mv "${held}" "${WORK}/heldback/"

# --- server A -----------------------------------------------------------------------------------
banner "Server A: build the library"
"${HELPER[@]}" db create rushbase_dr_a
server_env rushbase_dr_a
"${SERVER_BIN}" migrate >/dev/null
start_server rushbase_dr_a server-a
"${HELPER[@]}" setup
check "server A: setup finished" "${HELPER[@]}" snapshot "${WORK}/empty.json"
"${HELPER[@]}" wait-filed 4 120
enroll_and_process drill-worker
"${HELPER[@]}" wait-filed 4 30
"${HELPER[@]}" edit "${WORK}/edits.json"
sleep 3  # let sidecar writers settle
log "backup now"
"${SERVER_BIN}" backup now
"${HELPER[@]}" snapshot "${WORK}/snap-a.json" | sed 's/^/[A] /'
check "server A: asset count > 0" python3 -c "
import json,sys; sys.exit(0 if json.load(open('${WORK}/snap-a.json'))['asset_count'] > 0 else 1)"
check "server A: shots present" python3 -c "
import json,sys; sys.exit(0 if sum(a['shots'] for a in json.load(open('${WORK}/snap-a.json'))['assets']) > 0 else 1)"

banner "Simulated server loss (kill -9, database dropped)"
kill -9 "${SERVER_PID}" 2>/dev/null || true
wait "${SERVER_PID}" 2>/dev/null || true
SERVER_PID=""
"${HELPER[@]}" db drop rushbase_dr_a

# --- path 1: restore from backup ------------------------------------------------------------
banner "Path 1: server B restores the newest backup"
"${HELPER[@]}" db create rushbase_dr_b
server_env rushbase_dr_b
"${SERVER_BIN}" migrate >/dev/null
# The old lease is still valid (kill -9 never released it): a plain start must refuse.
lease_refused() {
  ! timeout 15 "${SERVER_BIN}" >"${WORK}/server-b-refused.log" 2>&1 && grep -qi "lease" "${WORK}/server-b-refused.log"
}
check "server B: refuses to start while the old server's lease is valid" lease_refused
log "waiting ${LEASE_SECONDS}s for the old lease to expire"
sleep "$((LEASE_SECONDS + 2))"
start_server rushbase_dr_b server-b
status_json="$("${HELPER[@]}" setup-status)"
check "server B: setup reports restore_available" python3 -c "
import json,sys; d=json.loads('''${status_json}'''); sys.exit(0 if d['restore_available'] and d['needs_setup'] else 1)"
newest="$("${HELPER[@]}" backup-names | head -n 1)"
check "server B: POST /setup/restore succeeds" "${HELPER[@]}" restore "${newest}"
"${HELPER[@]}" snapshot "${WORK}/snap-b.json" | sed 's/^/[B] /'
check "restore: assets, tags, human edits, needs list, search and users identical" \
  "${HELPER[@]}" compare "${WORK}/snap-a.json" "${WORK}/snap-b.json" backup
check "server B: took over the lease (library manifest holder is B, expiry in the future)" python3 -c "
import json,sys,datetime
m=json.load(open('${LIBRARY}/.rushbase/library.json')); l=m['server_lease']
exp=datetime.datetime.fromisoformat(l['expires_at'].replace('Z','+00:00'))
sys.exit(0 if exp > datetime.datetime.now(datetime.timezone.utc) else 1)"
log "new material arrives; the enrolled worker continues"
mv "${WORK}/heldback/"* "${LIBRARY}/Input/"
before_count="$(python3 -c "import json; print(json.load(open('${WORK}/snap-b.json'))['asset_count'])")"
"${HELPER[@]}" wait-filed "$((before_count + 1))" 120
enroll_and_process drill-worker skip
"${HELPER[@]}" wait-filed "$((before_count + 1))" 30
"${HELPER[@]}" snapshot "${WORK}/snap-b2.json" | sed 's/^/[B+] /'
check "worker continued with the restored server (old API key still valid)" python3 -c "
import json,sys; sys.exit(0 if json.load(open('${WORK}/snap-b2.json'))['asset_count'] > ${before_count} else 1)"
check "new assets have shots" python3 -c "
import json,sys; a=json.load(open('${WORK}/snap-b2.json'))['assets']; sys.exit(0 if all(x['shots'] > 0 for x in a if x['status']=='filed' and x['id'] not in {y['id'] for y in json.load(open('${WORK}/snap-b.json'))['assets']} and x['filename'].endswith(('.mp4','.mov','.mxf'))) else 1)"
"${SERVER_BIN}" backup now >/dev/null
stop_server
"${HELPER[@]}" db drop rushbase_dr_b

# --- path 2: rebuild from sidecars ----------------------------------------------------------
banner "Path 2: backups gone, server C rebuilds from sidecars"
mv "${LIBRARY}/.rushbase/backups" "${WORK}/backups-away"
"${HELPER[@]}" db create rushbase_dr_c
server_env rushbase_dr_c
"${SERVER_BIN}" migrate >/dev/null
check "rebuild-from-sidecars (dry run) reports no problems" "${SERVER_BIN}" rebuild-from-sidecars --dry-run
check "rebuild-from-sidecars finishes cleanly" "${SERVER_BIN}" rebuild-from-sidecars
start_server rushbase_dr_c server-c
status_json="$("${HELPER[@]}" setup-status)"
check "server C: no backups offered, setup needed (users are not in sidecars)" python3 -c "
import json,sys; d=json.loads('''${status_json}'''); sys.exit(0 if d['needs_setup'] and not d['restore_available'] else 1)"
check "server C: admin created again via setup" "${HELPER[@]}" setup
"${HELPER[@]}" snapshot "${WORK}/snap-c.json" | sed 's/^/[C] /'
check "rebuild: assets, files, shots, tags (incl. human source), rating, lock, search identical" \
  "${HELPER[@]}" compare "${WORK}/snap-b2.json" "${WORK}/snap-c.json" sidecars
check "rebuild: needs lists are NOT recovered (documented)" python3 -c "
import json,sys; sys.exit(0 if json.load(open('${WORK}/snap-c.json'))['needs'] == [] else 1)"
stop_server

# --- summary ------------------------------------------------------------------------------------
banner "Disaster-recovery drill summary"
failed=0
for line in "${RESULTS[@]}"; do
  echo "  ${line}"
  [[ "${line}" == FAIL* ]] && failed=$((failed + 1))
done
if (( failed > 0 )); then
  err "DR drill FAILED (${failed} of ${#RESULTS[@]} checks)"
  exit 1
fi
ok "DR drill PASSED (${#RESULTS[@]} checks)"
