#!/usr/bin/env bash
# Periodic update check, started by rushbase-updater.timer (interval: RUSHBASE_UPDATE_INTERVAL,
# default 10min). Default policy is "notify only": availability is written to
# /var/lib/rushbase/update-state/available.json. With RUSHBASE_UPDATE_AUTO=true the update is
# applied automatically.
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/prereq.sh
source "${SCRIPT_DIR}/lib/prereq.sh"

case "${1:-once}" in
  once) ;;
  -h|--help) echo "Usage: updater-service.sh [once]"; exit 0 ;;
  *) fail "Unknown argument: $1" ;;
esac

require_root
conf_load

if update_lock_held; then
  log "An update is already running; skipping this check."
  exit 0
fi

# Checks go to the journal only; applied updates are logged to update.log by update.sh.
if ! RUSHBASE_UPDATE_LOG_ACTIVE=1 bash "${SCRIPT_DIR}/update.sh" --check --branch "${BRANCH}"; then
  warn "Update check failed (offline or remote unavailable)."
  exit 0
fi

available="$(python3 -c 'import json,sys; print(str(json.load(open(sys.argv[1])).get("available", False)).lower())' \
  "${RB_AVAILABLE_FILE}" 2>/dev/null || echo false)"
if [[ "${available}" != "true" ]]; then
  exit 0
fi

if [[ "$(env_get RUSHBASE_UPDATE_AUTO)" == "true" ]]; then
  log "Automatic updates are enabled; applying the update."
  exec bash "${SCRIPT_DIR}/update.sh" --branch "${BRANCH}"
fi
log "Update available (notify only). Apply it with: sudo bash ${APP_DIR}/update.sh"
