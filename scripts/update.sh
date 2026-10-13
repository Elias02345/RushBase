#!/usr/bin/env bash
# Rushbase updater. Flow: check -> backup -> pull -> deps -> migrate -> restart -> health ->
# automatic rollback on failure. Progress is written to /var/lib/rushbase/update-state/.
# Exit codes: 0 updated/up to date, 1 failed before any change, 2 failed and rolled back,
# 3 failed and rollback failed.
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# The update replaces the repository (including this script) with `git reset --hard`. Run from
# a private copy so bash never reads a file that changes underneath it.
if [[ -z "${RUSHBASE_UPDATE_STAGED:-}" ]]; then
  stage="$(mktemp -d /tmp/rushbase-update.XXXXXX)"
  cp -a "${SCRIPT_DIR}" "${stage}/scripts"
  export RUSHBASE_UPDATE_STAGED=1 RUSHBASE_STAGE_DIR="${stage}"
  exec bash "${stage}/scripts/update.sh" "$@"
fi
trap 'rm -rf "${RUSHBASE_STAGE_DIR:-/nonexistent}"' EXIT

# shellcheck source=scripts/lib/prereq.sh
source "${SCRIPT_DIR}/lib/prereq.sh"
# shellcheck source=scripts/lib/rollback.sh
source "${SCRIPT_DIR}/lib/rollback.sh"

usage() {
  cat <<'EOF'
Usage: sudo bash update.sh [--branch main|dev] [--check] [--force] [--backup-only]

  --branch TRAIN   Update train to follow (default: the installed train)
  --check          Only report whether an update is available
  --force          Allow non-fast-forward updates (for example switching from dev to main)
  --backup-only    Only create a backup (database dump + .env) and exit
EOF
}

TARGET=""; CHECK_ONLY=0; FORCE=0; BACKUP_ONLY=0
while (( $# > 0 )); do
  case "$1" in
    --branch)       TARGET="${2:?--branch needs a value}"; shift 2 ;;
    --check)        CHECK_ONLY=1; shift ;;
    --force)        FORCE=1; shift ;;
    --backup-only)  BACKUP_ONLY=1; shift ;;
    --yes|-y)       shift ;;
    -h|--help)      usage; exit 0 ;;
    *) usage >&2; fail "Unknown option: $1" ;;
  esac
done

require_root
conf_load
[[ -r "${RB_CONF}" ]] || fail "Rushbase is not installed (${RB_CONF} not found). Run install.sh first."
[[ -d "${APP_DIR}/.git" ]] || fail "${APP_DIR} is not a git checkout."
TARGET="${TARGET:-${BRANCH}}"
[[ "${TARGET}" == "main" || "${TARGET}" == "dev" ]] || fail "--branch must be 'main' or 'dev'."

acquire_update_lock
mkdir -p "${RB_UPDATE_STATE_DIR}"
if [[ -z "${RUSHBASE_UPDATE_LOG_ACTIVE:-}" ]]; then
  export RUSHBASE_UPDATE_LOG_ACTIVE=1
  exec > >(tee -a "${RB_UPDATE_LOG}") 2>&1
fi
echo "=== rushbase update $(now_iso) (train: ${TARGET}) ==="

setup_tool_env
UPD_STARTED="$(now_iso)"
UPD_BRANCH="${TARGET}"
PRE_COMMIT="$(git_app rev-parse HEAD)"
PRE_BRANCH="$(git_app rev-parse --abbrev-ref HEAD)"
UPD_FROM="${PRE_COMMIT}"
UPD_TO="${PRE_COMMIT}"

# Status updates are skipped in --check mode so that a plain check never clobbers the last
# update outcome shown in the web UI.
wstatus() {
  if (( CHECK_ONLY == 0 )); then
    write_status "$@"
  fi
}

abort_before_change() {
  err "$1"
  wstatus failed "$2" 0 "$1"
  exit 1
}

if (( BACKUP_ONLY == 1 )); then
  backup_dir="$(make_backup)" || fail "Backup failed."
  prune_backups
  ok "Backup written to ${backup_dir}"
  exit 0
fi

# --- 1. Check ------------------------------------------------------------------------------
wstatus running check 5 "Fetching origin/${TARGET}"
log "Checking for updates on train '${TARGET}'"
if ! retry 3 5 git_app fetch --quiet --tags origin "+refs/heads/${TARGET}:refs/remotes/origin/${TARGET}"; then
  abort_before_change "git fetch failed (network or remote unavailable)." check
fi
NEW_COMMIT="$(git_app rev-parse "refs/remotes/origin/${TARGET}^{commit}")" \
  || abort_before_change "Remote branch ${TARGET} not found." check
UPD_TO="${NEW_COMMIT}"
behind="$(git_app rev-list --count "${PRE_COMMIT}..${NEW_COMMIT}")"

if [[ "${PRE_COMMIT}" == "${NEW_COMMIT}" && "${PRE_BRANCH}" == "${TARGET}" ]]; then
  write_available false 0 "${PRE_COMMIT}" "${NEW_COMMIT}" "${TARGET}"
  wstatus up_to_date check 100 "Already up to date at $(short "${PRE_COMMIT}")"
  ok "Already up to date at $(short "${PRE_COMMIT}") (${TARGET})"
  exit 0
fi

log "Update available: $(short "${PRE_COMMIT}") -> $(short "${NEW_COMMIT}") (${behind} new commit(s))"
git_app log --oneline --no-decorate -n 20 "${PRE_COMMIT}..${NEW_COMMIT}" | sed 's/^/    /' || true
write_available true "${behind}" "${PRE_COMMIT}" "${NEW_COMMIT}" "${TARGET}"
if (( CHECK_ONLY == 1 )); then
  exit 0
fi

if ! git_app merge-base --is-ancestor "${PRE_COMMIT}" "${NEW_COMMIT}" && (( FORCE == 0 )); then
  abort_before_change "origin/${TARGET} is not a fast-forward of the installed commit. Re-run with --force to accept (for example when switching trains)." check
fi
if ! verify_signature "refs/remotes/origin/${TARGET}"; then
  abort_before_change "Signature verification failed; the update was not applied." verify
fi

# --- 2. Backup -----------------------------------------------------------------------------
wstatus running backup 20 "Backing up database and .env"
log "Creating backup"
if ! BACKUP_DIR="$(make_backup)"; then
  abort_before_change "Backup failed; aborting before any change." backup
fi
ok "Backup: ${BACKUP_DIR}"
if [[ -n "${RUSHBASE_BACKUP_HOOK:-$(env_get RUSHBASE_BACKUP_HOOK)}" ]]; then
  hook="${RUSHBASE_BACKUP_HOOK:-$(env_get RUSHBASE_BACKUP_HOOK)}"
  log "Running backup hook ${hook} (timeouts are warnings)"
  timeout 300 "${hook}" "${BACKUP_DIR}" || warn "Backup hook failed or timed out; continuing."
fi

# --- 3-7. Apply (explicit error handling; any failure triggers the rollback) ---------------
apply_update() {
  wstatus running pull 35 "Pulling ${TARGET}"
  log "Applying $(short "${NEW_COMMIT}")"
  if [[ "${PRE_BRANCH}" == "${TARGET}" ]]; then
    git_app reset --hard --quiet "origin/${TARGET}" || return 1
  else
    git_app checkout -f --quiet -B "${TARGET}" "origin/${TARGET}" || return 1
  fi
  chmod +x "${APP_DIR}"/scripts/*.sh 2>/dev/null || true

  wstatus running deps 50 "Refreshing units and dependencies"
  install_units || return 1
  if [[ "${MODE}" == "bare" ]]; then
    if lock_files_changed "${PRE_COMMIT}" "${NEW_COMMIT}" || [[ ! -x "${APP_DIR}/.venv/bin/rushbase-server" ]]; then
      log "Dependency manifests changed; syncing the Python environment"
      uv_sync || return 1
    else
      log "Dependency manifests unchanged; skipping uv sync"
    fi
    if [[ "${WEB}" == "1" ]]; then
      if ! git_app diff --quiet "${PRE_COMMIT}" "${NEW_COMMIT}" -- pnpm-lock.yaml web packages \
        || [[ ! -d "${APP_DIR}/web/dist" ]]; then
        log "Building the web UI"
        web_build || return 1
      fi
    fi
  else
    log "Building the server image"
    retry 3 20 compose build || return 1
  fi

  wstatus running migrate 65 "Running database migrations"
  log "Applying database migrations"
  run_migrations || return 1

  wstatus running restart 80 "Restarting $(svc_name)"
  systemctl daemon-reload
  svc_restart || return 1

  wstatus running health 90 "Waiting for /health"
  log "Waiting for $(health_url)"
  wait_healthy "${RUSHBASE_HEALTH_TIMEOUT:-90}" || { err "Health check failed."; return 1; }
  return 0
}

if apply_update; then
  conf_set BRANCH "${TARGET}"
  prune_backups
  systemctl try-restart rushbase-updater-handler.service >/dev/null 2>&1 || true
  version="$(git_app describe --tags --always 2>/dev/null || short "${NEW_COMMIT}")"
  write_available false 0 "${NEW_COMMIT}" "${NEW_COMMIT}" "${TARGET}"
  wstatus success "done" 100 "Updated to ${version} ($(short "${NEW_COMMIT}"))"
  ok "Updated to ${version} (commit $(short "${NEW_COMMIT}"), train ${TARGET})"
  exit 0
fi

# --- Rollback --------------------------------------------------------------------------------
journal_dump
if perform_rollback "${BACKUP_DIR}" "${PRE_COMMIT}" "${PRE_BRANCH}" "update to $(short "${NEW_COMMIT}") failed" "${NEW_COMMIT}"; then
  wstatus rolled_back rollback 100 "Update to $(short "${NEW_COMMIT}") failed; rolled back to $(short "${PRE_COMMIT}")"
  err "Update failed and was rolled back to $(short "${PRE_COMMIT}")."
  exit 2
fi
wstatus rollback_failed rollback 100 "Update failed and the rollback also failed. Run doctor.sh and see ${RB_UPDATE_LOG}"
err "Update failed AND the rollback failed. Backup: ${BACKUP_DIR}. Try: sudo bash revert-update.sh"
exit 3
