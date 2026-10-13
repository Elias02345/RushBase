#!/usr/bin/env bash
# Rushbase doctor: checks prerequisites, services, health, library mount and backups.
# Usage: sudo bash doctor.sh [--repair] [--json]
# Exit status: 0 when no check failed (warnings allowed), 1 otherwise.
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/prereq.sh
source "${SCRIPT_DIR}/lib/prereq.sh"

REPAIR=0; JSON=0
while (( $# > 0 )); do
  case "$1" in
    --repair)  REPAIR=1; shift ;;
    --json)    JSON=1; shift ;;
    -h|--help) echo "Usage: sudo bash doctor.sh [--repair] [--json]"; exit 0 ;;
    *) fail "Unknown option: $1" ;;
  esac
done

require_root

N_OK=0; N_WARN=0; N_ERROR=0
ITEMS=()

# check STATUS NAME DETAIL   (STATUS: ok|warn|error)
check() {
  local status="$1" name="$2" detail="$3" tag color
  case "${status}" in
    ok)    N_OK=$((N_OK + 1));       tag="[OK]";    color="${C_GREEN}" ;;
    warn)  N_WARN=$((N_WARN + 1));   tag="[WARN]";  color="${C_YELLOW}" ;;
    error) N_ERROR=$((N_ERROR + 1)); tag="[ERROR]"; color="${C_RED}" ;;
  esac
  ITEMS+=("{\"status\":\"${status}\",\"name\":\"$(json_escape "${name}")\",\"detail\":\"$(json_escape "${detail}")\"}")
  if (( JSON == 0 )); then
    echo "${color}${tag}${C_RESET} ${name}: ${detail}"
  fi
}

if (( REPAIR == 1 )); then
  if (( JSON == 1 )); then
    exec 3>&1 1>&2
  fi
  banner "Repair pass"
  conf_load
  repair_prerequisites || warn "Repair pass finished with problems."
  if (( JSON == 1 )); then
    exec 1>&3 3>&-
  fi
fi

conf_load
INSTALLED=0
[[ -r "${RB_CONF}" ]] && INSTALLED=1

# --- OS and installation ---------------------------------------------------------------------
detect_os
case "${OS_ID}" in
  debian|ubuntu) check ok os "${OS_PRETTY}" ;;
  *) check warn os "${OS_PRETTY} is not an officially supported distribution (Debian/Ubuntu)" ;;
esac
if (( INSTALLED == 1 )); then
  check ok install "mode=${MODE}$([[ "${MODE}" == docker ]] && echo "/${NETWORK}"), train=${BRANCH}, dir=${APP_DIR}"
else
  check error install "${RB_CONF} not found; run install.sh first"
fi

# --- Binaries ----------------------------------------------------------------------------------
tool_check() { # NAME LEVEL_IF_MISSING
  if command_exists "$1"; then
    check ok "binary:$1" "$(command -v "$1")"
  else
    check "$2" "binary:$1" "not found in PATH"
  fi
}
media_level=error
[[ "${MODE}" == "docker" ]] && media_level=warn
tool_check ffmpeg "${media_level}"
tool_check ffprobe "${media_level}"
tool_check exiftool "${media_level}"
if [[ "${MODE}" == "bare" ]]; then
  tool_check pg_dump error
  tool_check uv error
  if [[ "${WEB}" == "1" ]]; then
    tool_check node error
  else
    tool_check node warn
  fi
fi

# --- Docker ----------------------------------------------------------------------------------
if [[ "${MODE}" == "docker" ]]; then
  if ! command_exists docker; then
    check error docker "docker not installed"
  elif ! docker info >/dev/null 2>&1; then
    check error docker "docker daemon not reachable"
  else
    check ok docker "$(docker --version)"
    if docker compose version >/dev/null 2>&1; then
      check ok docker-compose "$(docker compose version --short 2>/dev/null || echo plugin)"
      running="$(compose ps --status running -q 2>/dev/null | wc -l)"
      if (( running >= 2 )); then
        check ok containers "${running} container(s) running in project rushbase"
      else
        check error containers "expected postgres and server running, found ${running}"
      fi
    else
      check error docker-compose "Compose plugin missing"
    fi
  fi
fi

# --- systemd units ---------------------------------------------------------------------------
unit_check() { # UNIT LEVEL
  local unit="$1" level="$2" active enabled
  active="$(systemctl is-active "${unit}" 2>/dev/null || true)"
  enabled="$(systemctl is-enabled "${unit}" 2>/dev/null || true)"
  if [[ "${active}" == "active" ]]; then
    if [[ "${enabled}" == "enabled" ]]; then
      check ok "unit:${unit}" "active, enabled"
    else
      check warn "unit:${unit}" "active but not enabled (will not survive a reboot)"
    fi
  else
    check "${level}" "unit:${unit}" "state=${active:-unknown}, enabled=${enabled:-unknown}"
  fi
}
if (( INSTALLED == 1 )); then
  unit_check "$(svc_name).service" error
  unit_check rushbase-watchdog.timer error
  unit_check rushbase-updater.timer warn
  unit_check rushbase-updater-handler.service warn
  if [[ "${WORKER}" == "1" ]]; then
    unit_check rushbase-worker.service warn
  fi
  if [[ "${DB_MODE}" == "local" ]]; then
    unit_check postgresql.service error
  fi
fi

# --- Configuration -----------------------------------------------------------------------------
if (( INSTALLED == 1 )); then
  envf="$(env_file)"
  if [[ ! -f "${envf}" ]]; then
    check error env "${envf} missing"
  else
    mode_bits="$(stat -c '%a' "${envf}")"
    if [[ "${mode_bits}" =~ ^(6|4|0)[04]0$ ]]; then
      check ok env "${envf} (mode ${mode_bits})"
    else
      check warn env "${envf} has mode ${mode_bits}; expected 640 (fix: doctor.sh --repair)"
    fi
    secret="$(env_get RUSHBASE_SECRET_KEY)"
    if (( ${#secret} >= 32 )); then
      check ok secret-key "RUSHBASE_SECRET_KEY is set (${#secret} chars)"
    else
      check error secret-key "RUSHBASE_SECRET_KEY missing or shorter than 32 characters"
    fi
    if [[ "${MODE}" == "bare" && -z "$(env_get RUSHBASE_DATABASE_URL)" ]]; then
      check error database-url "RUSHBASE_DATABASE_URL is empty"
    fi
  fi
fi

# --- Health ------------------------------------------------------------------------------------
if health_out="$(curl -fsS --max-time 5 "$(health_url)" 2>&1)"; then
  check ok health "$(health_url) -> ${health_out}"
else
  check error health "$(health_url) not reachable"
fi
if [[ "${DB_MODE}" == "local" ]]; then
  if runuser -u postgres -- pg_isready -q 2>/dev/null; then
    check ok postgres "accepting connections"
  else
    check error postgres "not accepting connections"
  fi
fi

# as_service_user CMD...: run a command as the account the server uses to access the library.
as_service_user() {
  if [[ "${MODE}" == "docker" ]]; then
    setpriv --reuid=10001 --regid=10001 --clear-groups "$@"
  else
    runuser -u "${RB_USER}" -- "$@"
  fi
}

# --- Library -----------------------------------------------------------------------------------
lib=""
if (( INSTALLED == 1 )); then
  lib="$(env_get RUSHBASE_LIBRARY_HOST_PATH)"
  if [[ -z "${lib}" ]]; then
    lib="$(env_get RUSHBASE_LIBRARY_ROOT)"
  fi
fi
if [[ -z "${lib}" ]]; then
  check warn library "no library root configured"
elif [[ ! -d "${lib}" ]]; then
  check error library "${lib} does not exist (mount missing?)"
else
  check ok library "${lib} exists"
  if [[ -f "${lib}/.rushbase/library.json" ]]; then
    check ok library-json "${lib}/.rushbase/library.json present"
  else
    check warn library-json "${lib}/.rushbase/library.json not found (library not initialised yet)"
  fi
  if [[ -d "${lib}/.rushbase" ]]; then
    probe="${lib}/.rushbase/.doctor-rw-test.$$"
    probe_user="${RB_USER}"
    if [[ "${MODE}" == "docker" ]]; then
      probe_user="uid 10001"
    fi
    # shellcheck disable=SC2016  # $1 must expand in the inner shell
    if as_service_user sh -c 'echo ok >"$1" && cat "$1" >/dev/null && rm -f "$1"' _ "${probe}" 2>/dev/null; then
      check ok library-rw "read/write test in ${lib}/.rushbase passed as ${probe_user}"
    else
      rm -f "${probe}" 2>/dev/null || true
      check error library-rw "cannot write to ${lib}/.rushbase as ${probe_user}"
    fi
  else
    check warn library-rw "${lib}/.rushbase missing; read/write test skipped"
  fi
  fstype="$(findmnt -no FSTYPE -T "${lib}" 2>/dev/null || true)"
  if [[ "${fstype}" == "cifs" ]]; then
    opts="$(findmnt -no OPTIONS -T "${lib}" 2>/dev/null || true)"
    problems=()
    [[ ",${opts}," == *",cache=loose,"* ]] && problems+=("cache=loose (use cache=strict)")
    vers="$(grep -o 'vers=[0-9.]*' <<<"${opts}" | head -n1 | cut -d= -f2 || true)"
    if [[ -n "${vers}" && "${vers%%.*}" -lt 3 ]]; then
      problems+=("vers=${vers} (use vers=3.1.1)")
    fi
    act="$(grep -o 'actimeo=[0-9]*' <<<"${opts}" | head -n1 | cut -d= -f2 || true)"
    if [[ -n "${act}" && "${act}" -gt 5 ]]; then
      problems+=("actimeo=${act} (use <= 5)")
    fi
    if (( ${#problems[@]} > 0 )); then
      check warn cifs-options "$(IFS=';'; echo "${problems[*]}")"
    else
      check ok cifs-options "${opts}"
    fi
  fi
fi

# --- Disk space --------------------------------------------------------------------------------
disk_check() { # PATH LABEL
  local path="$1" label="$2" avail_kb pct
  [[ -d "${path}" ]] || return 0
  avail_kb="$(df -Pk "${path}" | awk 'NR==2 {print $4}')"
  pct="$(df -Pk "${path}" | awk 'NR==2 {gsub("%","",$5); print 100-$5}')"
  if (( pct < 3 || avail_kb < 1048576 )); then
    check error "disk:${label}" "${pct}% free ($((avail_kb / 1024)) MiB) on ${path}"
  elif (( pct < 10 || avail_kb < 5242880 )); then
    check warn "disk:${label}" "${pct}% free ($((avail_kb / 1024)) MiB) on ${path}"
  else
    check ok "disk:${label}" "${pct}% free ($((avail_kb / 1024)) MiB) on ${path}"
  fi
}
disk_check "${RB_STATE_DIR}" state
[[ -n "${lib}" ]] && disk_check "${lib}" library

# --- Backups and update state --------------------------------------------------------------
latest="$(latest_backup)"
if [[ -z "${latest}" ]]; then
  check warn backup "no backups yet (created on every update; run update.sh --backup-only for one now)"
else
  age_days=$(( ( $(date +%s) - $(stat -c %Y "${latest}") ) / 86400 ))
  if (( age_days > 30 )); then
    check warn backup "newest backup is ${age_days} days old (${latest})"
  else
    check ok backup "newest backup is ${age_days} day(s) old (${latest})"
  fi
fi
if [[ -r "${RB_STATUS_FILE}" ]]; then
  last_state="$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d["state"], d.get("message",""))' "${RB_STATUS_FILE}" 2>/dev/null || echo unknown)"
  case "${last_state%% *}" in
    rolled_back|failed) check warn last-update "${last_state}" ;;
    rollback_failed)    check error last-update "${last_state}" ;;
    *)                  check ok last-update "${last_state}" ;;
  esac
fi
if [[ -r "${RB_AVAILABLE_FILE}" ]] && grep -q '"available": true' "${RB_AVAILABLE_FILE}"; then
  check warn update-available "a newer version is available; run: sudo bash ${APP_DIR}/update.sh"
fi

# --- Summary -----------------------------------------------------------------------------------
if (( JSON == 1 )); then
  joined="$(IFS=,; echo "${ITEMS[*]}")"
  printf '{"ok":%s,"summary":{"ok":%s,"warn":%s,"error":%s},"checks":[%s]}\n' \
    "$([[ ${N_ERROR} -eq 0 ]] && echo true || echo false)" "${N_OK}" "${N_WARN}" "${N_ERROR}" "${joined}"
else
  echo
  echo "${C_BOLD}Summary:${C_RESET} ${N_OK} ok, ${N_WARN} warning(s), ${N_ERROR} error(s)"
  if (( N_ERROR > 0 && REPAIR == 0 )); then
    echo "Try: sudo bash doctor.sh --repair"
  fi
fi
(( N_ERROR == 0 ))
