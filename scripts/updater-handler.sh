#!/usr/bin/env bash
# Local trigger for the updater, so the server backend can start and monitor updates.
#
#   updater-handler.sh listen          run the listener (rushbase-updater-handler.service)
#   updater-handler.sh serve           answer one request on stdin/stdout (used by socat)
#   updater-handler.sh request CMD...  client: send a request and print the JSON reply
#
# Protocol: one line "status" | "check" | "update [main|dev]" | "revert", one JSON line back.
# Transport: Unix socket /run/rushbase/updater.sock (socat); without socat a FIFO pair
# /run/rushbase/updater.fifo (requests) and updater.reply (replies, prefixed by the request id).
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/prereq.sh
source "${SCRIPT_DIR}/lib/prereq.sh"

conf_load
RB_FIFO="${RB_RUN_DIR}/updater.fifo"
RB_REPLY="${RB_RUN_DIR}/updater.reply"

# compact_json FILE: single-line JSON or "null".
compact_json() {
  python3 -c 'import json,sys
try:
    print(json.dumps(json.load(open(sys.argv[1])), separators=(",", ":")))
except Exception:
    print("null")' "$1"
}

running_json() {
  if update_lock_held; then echo true; else echo false; fi
}

# handle_request "cmd args": prints one JSON line.
handle_request() {
  local line="$1" cmd arg
  cmd="${line%% *}"
  arg=""
  [[ "${line}" == *" "* ]] && arg="${line#* }"
  case "${cmd}" in
    status)
      printf '{"ok":true,"running":%s,"update":%s,"available":%s}\n' \
        "$(running_json)" "$(compact_json "${RB_STATUS_FILE}")" "$(compact_json "${RB_AVAILABLE_FILE}")"
      ;;
    check)
      if update_lock_held; then
        printf '{"ok":false,"error":"update running"}\n'
      elif RUSHBASE_UPDATE_LOG_ACTIVE=1 timeout 120 bash "${APP_DIR}/scripts/update.sh" --check --branch "${BRANCH}" >/dev/null 2>&1; then
        printf '{"ok":true,"available":%s}\n' "$(compact_json "${RB_AVAILABLE_FILE}")"
      else
        printf '{"ok":false,"error":"check failed"}\n'
      fi
      ;;
    update)
      local branch="${arg:-${BRANCH}}"
      if [[ "${branch}" != "main" && "${branch}" != "dev" ]]; then
        printf '{"ok":false,"error":"invalid branch"}\n'
      elif update_lock_held; then
        printf '{"ok":false,"accepted":false,"error":"update already running"}\n'
      elif systemd-run --quiet --collect --unit=rushbase-update-run \
          "${APP_DIR}/scripts/update.sh" --branch "${branch}" >/dev/null 2>&1; then
        printf '{"ok":true,"accepted":true,"branch":"%s","log":"%s"}\n' "${branch}" "${RB_UPDATE_LOG}"
      else
        printf '{"ok":false,"accepted":false,"error":"could not start the update"}\n'
      fi
      ;;
    revert)
      if update_lock_held; then
        printf '{"ok":false,"accepted":false,"error":"update already running"}\n'
      elif systemd-run --quiet --collect --unit=rushbase-update-run \
          "${APP_DIR}/scripts/revert-update.sh" --yes >/dev/null 2>&1; then
        printf '{"ok":true,"accepted":true,"log":"%s"}\n' "${RB_UPDATE_LOG}"
      else
        printf '{"ok":false,"accepted":false,"error":"could not start the revert"}\n'
      fi
      ;;
    *)
      printf '{"ok":false,"error":"unknown command"}\n'
      ;;
  esac
}

valid_request() { [[ "$1" =~ ^[a-z]+(\ [a-z]+)?$ ]]; }

cmd_serve() {
  local line=""
  read -r -t 5 line || true
  if valid_request "${line}"; then
    handle_request "${line}"
  else
    printf '{"ok":false,"error":"bad request"}\n'
  fi
}

cmd_listen() {
  require_root
  mkdir -p "${RB_RUN_DIR}"
  if command_exists socat; then
    log "Listening on ${RB_SOCKET} (socat)"
    exec socat "UNIX-LISTEN:${RB_SOCKET},fork,mode=0660,user=root,group=${RB_USER},unlink-early" \
      "EXEC:${SCRIPT_DIR}/updater-handler.sh serve"
  fi
  warn "socat not found; using the FIFO fallback in ${RB_RUN_DIR}"
  rm -f "${RB_FIFO}" "${RB_REPLY}"
  mkfifo -m 0620 "${RB_FIFO}"
  chown "root:${RB_USER}" "${RB_FIFO}"
  local line id req resp
  while true; do
    if read -r line <"${RB_FIFO}"; then
      id="${line%% *}"
      req="${line#* }"
      if [[ "${id}" =~ ^[0-9]+$ ]] && valid_request "${req}"; then
        resp="$(handle_request "${req}")"
      else
        resp='{"ok":false,"error":"bad request"}'
      fi
      printf '%s %s\n' "${id}" "${resp}" | atomic_write "${RB_REPLY}"
      chmod 640 "${RB_REPLY}"
      chown "root:${RB_USER}" "${RB_REPLY}" 2>/dev/null || true
    fi
  done
}

cmd_request() {
  local req="$*"
  valid_request "${req}" || fail "Invalid request '${req}'."
  if [[ -S "${RB_SOCKET}" ]]; then
    python3 - "${RB_SOCKET}" "${req}" <<'PY'
import socket
import sys

s = socket.socket(socket.AF_UNIX)
s.settimeout(130)
s.connect(sys.argv[1])
s.sendall((sys.argv[2] + "\n").encode())
s.shutdown(socket.SHUT_WR)
print(s.makefile().read().strip())
PY
    return
  fi
  [[ -p "${RB_FIFO}" ]] || fail "The updater handler is not running (no socket or FIFO in ${RB_RUN_DIR})."
  local id="$$${RANDOM}" waited=0 reply=""
  printf '%s %s\n' "${id}" "${req}" >"${RB_FIFO}"
  while (( waited < 130 )); do
    if [[ -r "${RB_REPLY}" ]] && grep -q "^${id} " "${RB_REPLY}" 2>/dev/null; then
      reply="$(grep "^${id} " "${RB_REPLY}" | head -n1)"
      echo "${reply#* }"
      return 0
    fi
    sleep 1
    waited=$((waited + 1))
  done
  fail "Timed out waiting for the updater handler."
}

case "${1:-}" in
  listen)  cmd_listen ;;
  serve)   cmd_serve ;;
  request) shift; cmd_request "$@" ;;
  *) echo "Usage: updater-handler.sh listen|serve|request CMD" >&2; exit 1 ;;
esac
