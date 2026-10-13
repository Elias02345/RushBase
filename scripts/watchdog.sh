#!/usr/bin/env bash
# Rushbase watchdog: run every 60 s by rushbase-watchdog.timer. Restarts the service after
# N consecutive failed health checks (after a repair pass) and logs structured JSON lines to
# /var/log/rushbase/watchdog.log.
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/prereq.sh
source "${SCRIPT_DIR}/lib/prereq.sh"

require_root
conf_load
mkdir -p "${RB_LOG_DIR}" "${RB_STATE_DIR}"
WD_LOG="${RB_LOG_DIR}/watchdog.log"
WD_STATE="${RB_STATE_DIR}/watchdog.state"

# wlog LEVEL EVENT MESSAGE
wlog() {
  printf '{"ts":"%s","level":"%s","event":"%s","message":"%s","failures":%s,"service":"%s"}\n' \
    "$(now_iso)" "$1" "$2" "$(json_escape "$3")" "${failures}" "$(svc_name)" >>"${WD_LOG}"
}

failures=0
if [[ -r "${WD_STATE}" ]]; then
  failures="$(sed -n 's/^failures=\([0-9]*\)$/\1/p' "${WD_STATE}" | head -n1)"
  failures="${failures:-0}"
fi
save_failures() { printf 'failures=%s\n' "${failures}" >"${WD_STATE}"; }

threshold="$(env_get RUSHBASE_WATCHDOG_THRESHOLD)"
if [[ ! "${threshold}" =~ ^[0-9]+$ ]] || (( threshold < 1 )); then
  threshold=3
fi

if update_lock_held; then
  wlog info skipped "update in progress"
  exit 0
fi

if health_ok; then
  if (( failures > 0 )); then
    wlog info recovered "health check passing again after ${failures} failure(s)"
  fi
  failures=0
  save_failures
  exit 0
fi

failures=$((failures + 1))
wlog warn health_failed "health check failed (${failures}/${threshold}) at $(health_url)"
if (( failures < threshold )); then
  save_failures
  exit 0
fi

wlog error repair_started "threshold reached; running repair pass (prerequisites, units, permissions)"
if ! ( repair_prerequisites ) >>"${WD_LOG}.repair" 2>&1; then
  wlog warn repair_incomplete "repair pass reported problems; see ${WD_LOG}.repair"
fi
wlog error restarting "restarting $(svc_name)"
svc_restart || wlog error restart_failed "systemctl restart failed"
if wait_healthy 60; then
  failures=0
  wlog info recovered "service healthy after restart"
else
  tail_lines="$(journalctl -u "$(svc_name)" -n 20 --no-pager 2>/dev/null | tail -n 20 | tr '\n' '|' || true)"
  wlog error escalated "still unhealthy after restart; operator attention needed. Last journal lines: ${tail_lines}"
  # Back off: the next restart attempt happens after another full threshold of failures.
  failures=0
fi
save_failures
