#!/usr/bin/env bash
# Rushbase one-shot installer for a fresh Debian/Ubuntu machine. Idempotent: re-running keeps
# existing secrets, configuration and data. See docs/operations.md.
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/prereq.sh
source "${SCRIPT_DIR}/lib/prereq.sh"
# shellcheck source=scripts/lib/smb.sh
source "${SCRIPT_DIR}/lib/smb.sh"

usage() {
  cat <<'EOF'
Usage: sudo bash install.sh [options]

  --mode docker|bare        Deployment mode (default: docker if Docker is present, else bare)
  --network bridge|host     Docker network mode; use host for cgNAT/tunnel setups (default: bridge)
  --branch main|dev         Update train (default: main)
  --repo URL                Git remote to clone (default: origin of this checkout, else GitHub)
  --app-dir DIR             Install directory (default: /opt/rushbase)
  --library PATH            Library root (local directory or SMB mount point)
  --port N                  Server port on the host (default: 8080)
  --bind ADDR               Bind address (default: 127.0.0.1; keep it behind a proxy or tunnel)
  --db-url URL              Use an external PostgreSQL (bare mode); skips the local database
  --with-worker             Also install the headless worker unit (bare mode)
  --no-web                  Skip Node.js/pnpm and the web UI build (bare mode)
  --no-media-tools          Do not install ffmpeg/exiftool
  --auto-update             Apply updates automatically (default: only notify)
  --smb //HOST/SHARE        Mount the library from an SMB share (never done by default)
  --smb-user USER           SMB user (default: prompt)
  --smb-password-file FILE  File containing the SMB password (or env RUSHBASE_SMB_PASSWORD)
  --smb-domain DOMAIN       SMB domain/workgroup
  --smb-options OPTS        Extra cifs mount options
  --units-only              Only re-render and reload the systemd units, then exit
  --no-start                Install but do not start services
  --yes, -y                 Non-interactive: accept defaults for everything not given as a flag
  -h, --help                Show this help
EOF
}

ASSUME_YES=0
OPT_MODE=""; OPT_NETWORK=""; OPT_BRANCH=""; OPT_REPO=""; OPT_APP_DIR=""
OPT_LIBRARY=""; OPT_PORT=""; OPT_BIND=""; OPT_DB_URL=""
OPT_WORKER=""; OPT_WEB=""; OPT_AUTO_UPDATE=0; OPT_NO_START=0; OPT_UNITS_ONLY=0
OPT_SMB=""; OPT_SMB_USER=""; OPT_SMB_PWFILE=""; OPT_SMB_DOMAIN=""; OPT_SMB_OPTS=""

while (( $# > 0 )); do
  case "$1" in
    --mode)               OPT_MODE="${2:?--mode needs a value}"; shift 2 ;;
    --network)            OPT_NETWORK="${2:?--network needs a value}"; shift 2 ;;
    --branch)             OPT_BRANCH="${2:?--branch needs a value}"; shift 2 ;;
    --repo)               OPT_REPO="${2:?--repo needs a value}"; shift 2 ;;
    --app-dir)            OPT_APP_DIR="${2:?--app-dir needs a value}"; shift 2 ;;
    --library)            OPT_LIBRARY="${2:?--library needs a value}"; shift 2 ;;
    --port)               OPT_PORT="${2:?--port needs a value}"; shift 2 ;;
    --bind)               OPT_BIND="${2:?--bind needs a value}"; shift 2 ;;
    --db-url)             OPT_DB_URL="${2:?--db-url needs a value}"; shift 2 ;;
    --with-worker)        OPT_WORKER=1; shift ;;
    --no-web)             OPT_WEB=0; shift ;;
    --no-media-tools)     export RUSHBASE_MEDIA_TOOLS=0; shift ;;
    --auto-update)        OPT_AUTO_UPDATE=1; shift ;;
    --smb)                OPT_SMB="${2:?--smb needs a value}"; shift 2 ;;
    --smb-user)           OPT_SMB_USER="${2:?--smb-user needs a value}"; shift 2 ;;
    --smb-password-file)  OPT_SMB_PWFILE="${2:?--smb-password-file needs a value}"; shift 2 ;;
    --smb-domain)         OPT_SMB_DOMAIN="${2:?--smb-domain needs a value}"; shift 2 ;;
    --smb-options)        OPT_SMB_OPTS="${2:?--smb-options needs a value}"; shift 2 ;;
    --units-only)         OPT_UNITS_ONLY=1; shift ;;
    --no-start)           OPT_NO_START=1; shift ;;
    --yes|-y)             ASSUME_YES=1; shift ;;
    -h|--help)            usage; exit 0 ;;
    *) usage >&2; fail "Unknown option: $1" ;;
  esac
done

require_root
require_supported_os

# ask VAR PROMPT DEFAULT: interactive question; falls back to the default without a TTY or with --yes.
ask() {
  local var="$1" prompt="$2" def="$3" reply
  if (( ASSUME_YES == 1 )) || [[ ! -t 0 ]]; then
    printf -v "${var}" '%s' "${def}"
    return 0
  fi
  read -r -p "${prompt} [${def}]: " reply
  printf -v "${var}" '%s' "${reply:-${def}}"
}

# --- Resolve configuration: flags > persisted install.conf > defaults ----------------------
APP_DIR_DEFAULT="${APP_DIR}"
conf_load
[[ -n "${OPT_APP_DIR}" ]] && APP_DIR="${OPT_APP_DIR}"
[[ -z "${OPT_APP_DIR}" && -z "${APP_DIR}" ]] && APP_DIR="${APP_DIR_DEFAULT}"
[[ "${APP_DIR}" =~ ^/[A-Za-z0-9._/-]+$ ]] || fail "--app-dir must be an absolute path (letters, digits, . _ / -)."

if [[ -n "${OPT_MODE}" ]]; then
  MODE="${OPT_MODE}"
elif [[ ! -r "${RB_CONF}" ]]; then
  default_mode=bare
  if command_exists docker && docker compose version >/dev/null 2>&1; then
    default_mode=docker
  fi
  ask MODE "Deployment mode (docker|bare)" "${default_mode}"
fi
[[ "${MODE}" == "docker" || "${MODE}" == "bare" ]] || fail "--mode must be 'docker' or 'bare'."

if [[ -n "${OPT_NETWORK}" ]]; then
  NETWORK="${OPT_NETWORK}"
elif [[ "${MODE}" == "docker" && ! -r "${RB_CONF}" ]]; then
  ask NETWORK "Docker network mode (bridge|host; host for cgNAT/tunnel setups)" "bridge"
fi
[[ "${NETWORK}" == "bridge" || "${NETWORK}" == "host" ]] || fail "--network must be 'bridge' or 'host'."

[[ -n "${OPT_BRANCH}" ]] && BRANCH="${OPT_BRANCH}"
[[ "${BRANCH}" == "main" || "${BRANCH}" == "dev" ]] || fail "--branch must be 'main' or 'dev'."

if [[ -n "${OPT_REPO}" ]]; then
  REPO_URL="${OPT_REPO}"
elif [[ ! -r "${RB_CONF}" ]]; then
  local_origin="$(git -C "${SCRIPT_DIR}" remote get-url origin 2>/dev/null || true)"
  REPO_URL="${local_origin:-${RB_DEFAULT_REPO}}"
fi

[[ -n "${OPT_WORKER}" ]] && WORKER="${OPT_WORKER}"
[[ -n "${OPT_WEB}" ]] && WEB="${OPT_WEB}"
if [[ "${MODE}" == "docker" ]]; then
  DB_MODE="docker"
  WEB="0"
  WORKER="0"
  if [[ "${NETWORK}" == "host" ]]; then
    COMPOSE_FILE="docker-compose.hostnet.yml"
  else
    COMPOSE_FILE="docker-compose.yml"
  fi
elif [[ -n "${OPT_DB_URL}" ]]; then
  DB_MODE="external"
elif [[ "${DB_MODE}" == "docker" ]]; then
  DB_MODE="local"
fi

if (( OPT_UNITS_ONLY == 1 )); then
  [[ -d "${APP_DIR}/deploy/systemd" ]] || fail "No installation found in ${APP_DIR}."
  ensure_service_user
  install_units --enable
  systemctl restart rushbase-updater.timer 2>/dev/null || true
  ok "systemd units re-rendered."
  exit 0
fi

banner "Rushbase installer"
log "Mode: ${MODE}$([[ "${MODE}" == docker ]] && echo " (${NETWORK} network)"), train: ${BRANCH}, directory: ${APP_DIR}"

# --- Prerequisites ---------------------------------------------------------------------------
banner "Prerequisites"
install_system_packages
ensure_service_user
ensure_dirs
setup_tool_env

# --- Application code ------------------------------------------------------------------------
banner "Application code"
if [[ -d "${APP_DIR}/.git" ]]; then
  ok "Existing checkout found in ${APP_DIR}; leaving the code untouched (use update.sh to update)."
else
  if [[ -d "${APP_DIR}" && -n "$(ls -A "${APP_DIR}" 2>/dev/null)" ]]; then
    fail "${APP_DIR} exists and is not an empty git checkout."
  fi
  log "Cloning ${REPO_URL} (${BRANCH}) into ${APP_DIR}"
  mkdir -p "$(dirname "${APP_DIR}")"
  retry 3 5 git clone --quiet --branch "${BRANCH}" "${REPO_URL}" "${APP_DIR}"
fi
current_branch="$(git_app rev-parse --abbrev-ref HEAD)"
if [[ "${current_branch}" != "${BRANCH}" ]]; then
  warn "Checked out branch is '${current_branch}', requested '${BRANCH}'. Use 'update.sh --branch ${BRANCH}' to switch trains."
  BRANCH="${current_branch}"
fi
[[ -d "${APP_DIR}/deploy/systemd" ]] || fail "${APP_DIR}/deploy/systemd is missing; this checkout has no operations layer."

# --- .env ----------------------------------------------------------------------------------------
banner "Configuration"
envf="$(env_file)"
if [[ ! -f "${envf}" ]]; then
  log "Creating ${envf} from .env.example"
  install -m 0640 "${APP_DIR}/.env.example" "${envf}"
else
  ok "Keeping existing ${envf}"
fi
chmod 640 "${envf}"

env_set RUSHBASE_SECRET_KEY "$(existing_or_generated_secret RUSHBASE_SECRET_KEY 48)"
[[ -n "${OPT_PORT}" ]] && env_set RUSHBASE_PORT "${OPT_PORT}"
[[ -n "${OPT_BIND}" ]] && env_set RUSHBASE_BIND "${OPT_BIND}"
env_set_default RUSHBASE_PORT 8080
env_set_default RUSHBASE_BIND 127.0.0.1
env_set_default RUSHBASE_UPDATE_AUTO false
env_set_default RUSHBASE_UPDATE_INTERVAL 10min
env_set_default RUSHBASE_UPDATE_REQUIRE_SIGNED false
env_set_default RUSHBASE_WATCHDOG_THRESHOLD 3
env_set_default RUSHBASE_BACKUP_KEEP 10
if (( OPT_AUTO_UPDATE == 1 )); then
  env_set RUSHBASE_UPDATE_AUTO true
fi
if [[ "${MODE}" == "bare" ]]; then
  env_set_default RUSHBASE_HOST "$(env_get RUSHBASE_BIND)"
  env_set RUSHBASE_HOST "$(env_get RUSHBASE_BIND)"
else
  env_set RUSHBASE_DB_PASSWORD "$(existing_or_generated_secret RUSHBASE_DB_PASSWORD 24)"
  env_set_default RUSHBASE_DB_PORT 15432
fi

# Library location.
smb_mount=""
if [[ -n "${OPT_SMB}" ]]; then
  smb_mount="${OPT_LIBRARY:-/mnt/rushbase}"
  OPT_LIBRARY="${smb_mount}"
fi
library="${OPT_LIBRARY:-$(env_get RUSHBASE_LIBRARY_ROOT)}"
[[ -z "${library}" ]] && library="$(env_get RUSHBASE_LIBRARY_HOST_PATH)"
if [[ -z "${library}" ]]; then
  ask library "Library root (local directory or mount point of the media share)" "/srv/rushbase-library"
fi
[[ "${library}" =~ ^/[A-Za-z0-9._/-]+$ ]] || fail "The library path must be absolute (letters, digits, . _ / -)."
if [[ "${MODE}" == "docker" ]]; then
  env_set RUSHBASE_LIBRARY_HOST_PATH "${library}"
  env_set RUSHBASE_LIBRARY_ROOT /library
else
  env_set RUSHBASE_LIBRARY_ROOT "${library}"
fi
env_set_default RUSHBASE_SERVER_URL "http://127.0.0.1:$(server_port)"

# --- SMB mount (opt-in) ----------------------------------------------------------------------
if [[ -n "${OPT_SMB}" ]]; then
  banner "SMB library mount"
  apt_install cifs-utils
  smb_user="${OPT_SMB_USER}"
  if [[ -z "${smb_user}" ]]; then
    ask smb_user "SMB user" "rushbase"
  fi
  smb_password="${RUSHBASE_SMB_PASSWORD:-}"
  if [[ -n "${OPT_SMB_PWFILE}" ]]; then
    smb_password="$(head -n1 "${OPT_SMB_PWFILE}")"
  fi
  if [[ -z "${smb_password}" ]]; then
    if [[ -t 0 && ${ASSUME_YES} -eq 0 ]]; then
      read -r -s -p "SMB password for ${smb_user}: " smb_password
      echo
    else
      fail "SMB password missing: use --smb-password-file or RUSHBASE_SMB_PASSWORD."
    fi
  fi
  if [[ "${MODE}" == "docker" ]]; then
    export RB_SMB_UID=10001 RB_SMB_GID=10001
  fi
  smb_configure "${OPT_SMB}" "${smb_mount}" "${smb_user}" "${smb_password}" "${OPT_SMB_DOMAIN}" "${OPT_SMB_OPTS}"
  unset smb_password
elif [[ ! -d "${library}" ]]; then
  log "Creating library directory ${library}"
  mkdir -p "${library}"
  if [[ "${MODE}" == "docker" ]]; then
    chown 10001:10001 "${library}"
  else
    chown "${RB_USER}:${RB_USER}" "${library}"
  fi
fi

# --- Database (bare mode) --------------------------------------------------------------------
provision_local_db() {
  banner "PostgreSQL"
  systemctl enable --now postgresql >/dev/null 2>&1 || true
  local waited=0
  until runuser -u postgres -- pg_isready -q; do
    waited=$((waited + 1))
    (( waited > 30 )) && fail "PostgreSQL did not become ready."
    sleep 1
  done
  local url port pw
  url="$(env_get RUSHBASE_DATABASE_URL)"
  if [[ -z "${url}" ]]; then
    port="$(runuser -u postgres -- psql -Atqc 'SHOW port')"
    pw="$(gen_secret 24)"
    if runuser -u postgres -- psql -Atqc "SELECT 1 FROM pg_roles WHERE rolname='rushbase'" | grep -q 1; then
      echo "ALTER ROLE rushbase LOGIN PASSWORD :'pw';" | runuser -u postgres -- psql -v ON_ERROR_STOP=1 -v pw="${pw}" -q
    else
      echo "CREATE ROLE rushbase LOGIN PASSWORD :'pw';" | runuser -u postgres -- psql -v ON_ERROR_STOP=1 -v pw="${pw}" -q
    fi
    env_set RUSHBASE_DATABASE_URL "postgresql+psycopg://rushbase:${pw}@127.0.0.1:${port}/rushbase"
    ok "Created database role 'rushbase'"
  fi
  if ! runuser -u postgres -- psql -Atqc "SELECT 1 FROM pg_database WHERE datname='rushbase'" | grep -q 1; then
    runuser -u postgres -- psql -v ON_ERROR_STOP=1 -qc 'CREATE DATABASE rushbase OWNER rushbase'
    ok "Created database 'rushbase'"
  fi
  runuser -u postgres -- psql -d rushbase -v ON_ERROR_STOP=1 -qc 'CREATE EXTENSION IF NOT EXISTS vector'
  runuser -u postgres -- psql -d rushbase -v ON_ERROR_STOP=1 -qc 'CREATE EXTENSION IF NOT EXISTS pg_trgm'
  pg_url="$(db_url_plain)"
  psql "${pg_url}" -Atqc 'SELECT 1' >/dev/null \
    || fail "Cannot connect to the database with RUSHBASE_DATABASE_URL from ${envf}."
  ok "PostgreSQL ready (pgvector and pg_trgm enabled)"
}

if [[ "${MODE}" == "bare" ]]; then
  if [[ "${DB_MODE}" == "external" ]]; then
    env_set RUSHBASE_DATABASE_URL "${OPT_DB_URL}"
    ok "Using the external database from --db-url"
  else
    provision_local_db
  fi
fi

conf_save_all
conf_set INSTALLED_AT "$(now_iso)"
fix_permissions

# --- Dependencies and build ------------------------------------------------------------------
if [[ "${MODE}" == "bare" ]]; then
  banner "Python environment"
  uv_sync
  ok "Python environment ready in ${APP_DIR}/.venv"
  if [[ "${WEB}" == "1" ]]; then
    banner "Web UI build"
    web_build
    ok "Web UI built"
  fi
fi

# --- systemd, logrotate ------------------------------------------------------------------------
banner "Services"
cat >/etc/logrotate.d/rushbase <<EOF
${RB_LOG_DIR}/*.log ${RB_UPDATE_LOG} {
    weekly
    rotate 8
    compress
    missingok
    notifempty
    copytruncate
}
EOF
install_units --enable
if [[ "${MODE}" == "docker" ]]; then
  chmod +x "${APP_DIR}/scripts/compose-up.sh"
fi
chmod +x "${APP_DIR}"/scripts/*.sh

if (( OPT_NO_START == 1 )); then
  warn "--no-start given: services are installed and enabled but not started."
else
  if [[ "${MODE}" == "docker" ]]; then
    systemctl restart rushbase-compose.service
    log "Waiting for the container stack (first start builds the server image)..."
    waited=0
    until compose_pg pg_isready -U rushbase -q >/dev/null 2>&1; do
      waited=$((waited + 3))
      if (( waited > 600 )); then
        journal_dump rushbase-compose
        fail "The database container did not become ready."
      fi
      sleep 3
    done
    banner "Database migrations"
    run_migrations
  else
    banner "Database migrations"
    run_migrations
    systemctl restart rushbase-server.service
  fi
  systemctl enable --now rushbase-watchdog.timer rushbase-updater.timer rushbase-updater-handler.service >/dev/null 2>&1
  systemctl restart rushbase-updater-handler.service

  if [[ "${WORKER}" == "1" ]]; then
    if "${APP_DIR}/.venv/bin/rushbase" worker --help >/dev/null 2>&1; then
      systemctl enable --now rushbase-worker.service
    else
      warn "The 'rushbase worker' command does not exist in this version; rushbase-worker.service is installed but not enabled."
    fi
  fi

  banner "Health check"
  if wait_healthy 120; then
    ok "Server healthy: $(curl -fsS "$(health_url)")"
  else
    journal_dump
    fail "The server did not become healthy. See the journal output above."
  fi
fi

# --- Summary ----------------------------------------------------------------------------------
port="$(server_port)"
bind="$(env_get RUSHBASE_BIND)"
banner "Rushbase is installed"
cat <<EOF
  Web UI:        http://127.0.0.1:${port}   (bound to ${bind:-127.0.0.1}; put Caddy/Nginx or a tunnel in front)
  Health:        $(health_url)
  Mode:          ${MODE}$([[ "${MODE}" == docker ]] && echo ", ${NETWORK} network")
  Update train:  ${BRANCH}   (auto-update: $(env_get RUSHBASE_UPDATE_AUTO), poll interval: $(update_interval))
  Library root:  ${library}
  Config:        ${envf}   (secrets were generated; keep this file safe)
  Backups:       ${RB_BACKUP_DIR}
  Update state:  ${RB_STATUS_FILE}

  Reverse proxy / tunnel examples: ${APP_DIR}/deploy/{caddy,nginx,cloudflared}/
  Check the installation:          sudo bash ${APP_DIR}/doctor.sh
  Worker machines: install the rushbase CLI there, mount the same share and verify with
    rushbase doctor --library <mount> --server-url <this server's URL>
  (Worker enrollment with pairing codes is not part of this version; no code is printed.)
EOF
