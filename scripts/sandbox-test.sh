#!/usr/bin/env bash
# Runs the operations layer (installer, updater, rollback, watchdog, doctor) end to end inside
# a throw-away systemd container. Nothing touches the host: no host ports, no host systemd, no
# files outside the container. Requires only Docker.
#
#   scripts/sandbox-test.sh [--keep] [--with-web] [--no-build]
#
#   --keep      leave the container running afterwards (docker exec -it rushbase-sandbox bash)
#   --with-web  also install Node/pnpm and build the web UI (slower)
#   --no-build  reuse an existing test image
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

if [[ -t 1 ]]; then
  C_CYAN=$'\033[36m'; C_GREEN=$'\033[32m'; C_RED=$'\033[31m'; C_RESET=$'\033[0m'
else
  C_CYAN=''; C_GREEN=''; C_RED=''; C_RESET=''
fi
log()  { echo "${C_CYAN}[INFO]${C_RESET} $*"; }
ok()   { echo "${C_GREEN}[OK]${C_RESET} $*"; }
fail() { echo "${C_RED}[FATAL]${C_RESET} $*" >&2; exit 1; }

NAME="rushbase-sandbox"
IMAGE="rushbase-sandbox-systemd:local"
KEEP=0; WITH_WEB=0; BUILD=1
while (( $# > 0 )); do
  case "$1" in
    --keep)     KEEP=1; shift ;;
    --with-web) WITH_WEB=1; shift ;;
    --no-build) BUILD=0; shift ;;
    -h|--help)  sed -n '2,12p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) fail "Unknown option: $1" ;;
  esac
done

command -v docker >/dev/null 2>&1 || fail "Docker is required."

cleanup() {
  if (( KEEP == 1 )); then
    log "Keeping container ${NAME}. Remove it with: docker rm -f ${NAME}"
  else
    docker rm -f "${NAME}" >/dev/null 2>&1 || true
  fi
}

# Only ever touch a container with exactly this name AND our label.
if docker inspect "${NAME}" >/dev/null 2>&1; then
  if [[ "$(docker inspect -f '{{ index .Config.Labels "rushbase.sandbox" }}' "${NAME}")" == "1" ]]; then
    log "Removing stale sandbox container ${NAME}"
    docker rm -f "${NAME}" >/dev/null
  else
    fail "A container named ${NAME} exists and was not created by this script; refusing to touch it."
  fi
fi

if (( BUILD == 1 )) || ! docker image inspect "${IMAGE}" >/dev/null 2>&1; then
  log "Building ${IMAGE}"
  docker build -q -t "${IMAGE}" -f "${REPO_ROOT}/deploy/test/Dockerfile.systemd" "${REPO_ROOT}/deploy/test" >/dev/null
fi

trap cleanup EXIT

log "Starting systemd container ${NAME}"
docker run -d --name "${NAME}" --label rushbase.sandbox=1 \
  --cgroupns=host -v /sys/fs/cgroup:/sys/fs/cgroup:rw \
  --tmpfs /run --tmpfs /run/lock --tmpfs /tmp \
  "${IMAGE}" >/dev/null

log "Waiting for systemd to boot"
for _ in $(seq 1 60); do
  state="$(docker exec "${NAME}" systemctl is-system-running 2>/dev/null || true)"
  if [[ "${state}" == "running" || "${state}" == "degraded" ]]; then
    break
  fi
  sleep 1
done
[[ "${state:-}" == "running" || "${state:-}" == "degraded" ]] \
  || fail "systemd did not start in the container (state: ${state:-unknown}). Is cgroup v2 available?"
ok "systemd is up (${state})"

log "Copying the working tree (tracked + untracked, ignoring .gitignore) into the container"
docker exec "${NAME}" mkdir -p /src
(cd "${REPO_ROOT}" && git ls-files -z --cached --others --exclude-standard --deduplicate) \
  | (cd "${REPO_ROOT}" && tar --null --ignore-failed-read -T - -cf - 2>/dev/null) \
  | docker exec -i "${NAME}" tar -xf - -C /src

rc=0
docker exec -e RUSHBASE_SANDBOX_WITH_WEB="${WITH_WEB}" "${NAME}" bash /src/deploy/test/guest-test.sh || rc=$?

if (( rc != 0 )); then
  echo "${C_RED}[FAIL]${C_RESET} sandbox test failed (exit ${rc})" >&2
  if (( KEEP == 0 )); then
    log "Tip: re-run with --keep to inspect the container."
  fi
  exit "${rc}"
fi
ok "sandbox test passed"
