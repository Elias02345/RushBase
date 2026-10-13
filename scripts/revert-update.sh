#!/usr/bin/env bash
# Manual rollback to the state captured by a backup (code commit, .env, database).
# Data written after the backup was taken is lost. See docs/operations.md.
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Run from a private copy: the rollback resets the repository this script lives in.
if [[ -z "${RUSHBASE_UPDATE_STAGED:-}" ]]; then
  stage="$(mktemp -d /tmp/rushbase-revert.XXXXXX)"
  cp -a "${SCRIPT_DIR}" "${stage}/scripts"
  export RUSHBASE_UPDATE_STAGED=1 RUSHBASE_STAGE_DIR="${stage}"
  exec bash "${stage}/scripts/revert-update.sh" "$@"
fi
trap 'rm -rf "${RUSHBASE_STAGE_DIR:-/nonexistent}"' EXIT

# shellcheck source=scripts/lib/prereq.sh
source "${SCRIPT_DIR}/lib/prereq.sh"
# shellcheck source=scripts/lib/rollback.sh
source "${SCRIPT_DIR}/lib/rollback.sh"

usage() {
  cat <<'EOF'
Usage: sudo bash revert-update.sh [--backup DIR] [--list] [--yes]

  --backup DIR   Backup directory to restore (default: the newest one)
  --list         List available backups and exit
  --yes, -y      Do not ask for confirmation
EOF
}

BACKUP=""; ASSUME_YES=0; LIST=0
while (( $# > 0 )); do
  case "$1" in
    --backup)  BACKUP="${2:?--backup needs a value}"; shift 2 ;;
    --list)    LIST=1; shift ;;
    --yes|-y)  ASSUME_YES=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; fail "Unknown option: $1" ;;
  esac
done

require_root
conf_load
[[ -r "${RB_CONF}" ]] || fail "Rushbase is not installed (${RB_CONF} not found)."

if (( LIST == 1 )); then
  find "${RB_BACKUP_DIR}" -mindepth 1 -maxdepth 1 -type d | sort
  exit 0
fi

BACKUP="${BACKUP:-$(latest_backup)}"
[[ -n "${BACKUP}" && -d "${BACKUP}" ]] || fail "No backup found in ${RB_BACKUP_DIR}."
[[ -s "${BACKUP}/db.dump" && -f "${BACKUP}/meta.json" ]] || fail "${BACKUP} is not a complete backup."

TARGET_COMMIT="$(backup_meta "${BACKUP}" commit)"
TARGET_BRANCH="$(backup_meta "${BACKUP}" branch)"
[[ "${TARGET_BRANCH}" == "HEAD" || "${TARGET_BRANCH}" == "unknown" ]] && TARGET_BRANCH="${BRANCH}"

banner "Rushbase manual rollback"
echo "  Backup:   ${BACKUP}"
echo "  Commit:   ${TARGET_COMMIT} (${TARGET_BRANCH})"
echo "  Current:  $(git_app rev-parse HEAD)"
warn "The database and .env are replaced by the backup; changes made since then are lost."
if (( ASSUME_YES == 0 )); then
  if [[ ! -t 0 ]]; then
    fail "Refusing to continue without confirmation; pass --yes."
  fi
  read -r -p "Type 'revert' to continue: " answer
  [[ "${answer}" == "revert" ]] || fail "Aborted."
fi

acquire_update_lock
mkdir -p "${RB_UPDATE_STATE_DIR}"
exec > >(tee -a "${RB_UPDATE_LOG}") 2>&1
echo "=== rushbase revert $(now_iso) ==="
setup_tool_env
UPD_STARTED="$(now_iso)"
UPD_BRANCH="${TARGET_BRANCH}"
UPD_FROM="$(git_app rev-parse HEAD)"
UPD_TO="${TARGET_COMMIT}"

if perform_rollback "${BACKUP}" "${TARGET_COMMIT}" "${TARGET_BRANCH}" "manual revert" "${UPD_FROM}"; then
  conf_set BRANCH "${TARGET_BRANCH}"
  write_status rolled_back rollback 100 "Manually reverted to $(short "${TARGET_COMMIT}")"
  ok "Reverted to $(short "${TARGET_COMMIT}")."
  exit 0
fi
write_status rollback_failed rollback 100 "Manual revert failed; see ${RB_UPDATE_LOG}"
fail "Revert failed. Backup kept in ${BACKUP}."
