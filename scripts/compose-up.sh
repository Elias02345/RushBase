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

case "${1:-start}" in
  check)
    compose config -q
    ;;
  start)
    compose config -q || { err "Invalid compose configuration (check ${APP_DIR}/.env)."; exit 1; }
    if ! retry 3 20 compose build; then
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
