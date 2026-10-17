#!/usr/bin/env bash
# Helper behind rushbase-compose.service (Docker mode). Never call `docker compose up`
# directly from the unit: this script validates the configuration, builds and starts with
# retries and dumps diagnostics on failure.
#   compose-up.sh start   build + start the stack, then follow its logs (service stays "active")
#   compose-up.sh stop    stop the stack
#   compose-up.sh check   validate the compose configuration only
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/prereq.sh
source "${SCRIPT_DIR}/lib/prereq.sh"

conf_load
cd "${APP_DIR}"

dump_diagnostics() {
  err "Docker Compose stack failed. Diagnostics follow."
  compose ps -a >&2 || true
  compose logs --no-color --tail 100 >&2 || true
  journal_dump rushbase-compose
}

# use_published_image: on a release tag checkout (and no RUSHBASE_SERVER_IMAGE in .env or the
# environment) run the GHCR image of that version. When it cannot be pulled, fall back to the
# local build, so a tag without published images or an offline registry still works.
use_published_image() {
  RUSHBASE_SERVER_IMAGE="${RUSHBASE_SERVER_IMAGE:-$(env_get RUSHBASE_SERVER_IMAGE || true)}"
  if [[ -z "${RUSHBASE_SERVER_IMAGE}" ]]; then
    local tag
    tag="$(git -C "${APP_DIR}" describe --exact-match --tags --match 'v*' 2>/dev/null || true)"
    if [[ -n "${tag}" ]]; then RUSHBASE_SERVER_IMAGE="ghcr.io/elias02345/rushbase-server:${tag#v}"; fi
  fi
  if [[ -n "${RUSHBASE_SERVER_IMAGE}" ]] && ! retry 3 20 compose pull server; then
    warn "Cannot pull ${RUSHBASE_SERVER_IMAGE}; building the server image locally."
    RUSHBASE_SERVER_IMAGE=""
  fi
  export RUSHBASE_SERVER_IMAGE
}

case "${1:-start}" in
  check)
    compose config -q
    ;;
  start)
    compose config -q || { err "Invalid compose configuration (check ${APP_DIR}/.env)."; exit 1; }
    use_published_image
    if [[ -z "${RUSHBASE_SERVER_IMAGE}" ]] && ! retry 3 20 compose build; then
      dump_diagnostics
      exit 1
    fi
    if ! retry 3 20 compose up -d --remove-orphans; then
      dump_diagnostics
      exit 1
    fi
    ok "Stack started; following logs."
    compose logs -f --tail 20
    ;;
  stop)
    compose stop
    ;;
  *)
    echo "Usage: compose-up.sh start|stop|check" >&2
    exit 1
    ;;
esac
