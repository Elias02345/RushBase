#!/usr/bin/env bash
# Rollback logic shared by update.sh (automatic) and revert-update.sh (manual).
# Requires prereq.sh to be sourced and the install configuration to be loaded.

# backup_meta BACKUP_DIR KEY: read a field from the backup's meta.json.
backup_meta() {
  python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])' \
    "$1/meta.json" "$2"
}

# lock_files_changed OLD NEW: true when dependency manifests differ between two commits.
lock_files_changed() {
  ! git_app diff --quiet "$1" "$2" -- uv.lock pyproject.toml server/pyproject.toml core/pyproject.toml \
    worker/pyproject.toml .python-version pnpm-lock.yaml web/package.json 2>/dev/null
}

# perform_rollback BACKUP_DIR TARGET_COMMIT TARGET_BRANCH REASON [FAILED_COMMIT]
# Stops the service, restores code, .env and database, restarts and verifies health.
# Returns 0 when the previous state is healthy again, 1 otherwise.
perform_rollback() {
  local backup="$1" commit="$2" branch="$3" reason="$4" failed="${5:-}"
  warn "Rolling back to $(short "${commit}") (${branch}): ${reason}"
  write_status running rollback 92 "Rolling back: ${reason}"

  svc_stop || warn "Could not stop the service cleanly; continuing."
  git_app checkout -f -B "${branch}" "${commit}" >/dev/null 2>&1 \
    || { err "Could not reset the code to ${commit}."; return 1; }
  if [[ -f "${backup}/env.backup" ]]; then
    cat "${backup}/env.backup" >"$(env_file)"
    fix_permissions
  else
    warn "No .env backup found in ${backup}; keeping the current .env."
  fi
  install_units || warn "Could not re-render the systemd units."

  if [[ -n "${failed}" ]] && lock_files_changed "${commit}" "${failed}"; then
    log "Dependency manifests differ from the failed update; restoring the environment."
    if [[ "${MODE}" == "bare" ]]; then
      uv_sync || warn "uv sync failed during rollback."
      web_build || warn "web build failed during rollback."
    fi
  fi

  if [[ "${MODE}" == "docker" ]]; then
    compose up -d postgres >/dev/null 2>&1 || true
    local waited=0
    until compose_pg pg_isready -U rushbase -q >/dev/null 2>&1; do
      waited=$((waited + 2))
      if (( waited > 90 )); then
        err "Database container did not become ready for the restore."
        return 1
      fi
      sleep 2
    done
  fi

  if [[ -s "${backup}/db.dump" ]]; then
    log "Restoring the database from ${backup}/db.dump"
    db_restore "${backup}/db.dump" || { err "Database restore failed."; return 1; }
  else
    err "No database dump in ${backup}."
    return 1
  fi

  systemctl daemon-reload
  svc_start || { journal_dump; return 1; }
  if wait_healthy "${RUSHBASE_HEALTH_TIMEOUT:-90}"; then
    ok "Rollback complete: $(short "$(git_app rev-parse HEAD)") is healthy again."
    return 0
  fi
  journal_dump
  return 1
}
