#!/usr/bin/env bash
# Shared functions for the Rushbase operations scripts (install, update, revert, doctor,
# watchdog, updater). Source this file; it has no side effects besides defining variables.
# shellcheck disable=SC2034  # many variables are consumed by the scripts that source this file

if [[ -n "${RB_PREREQ_LOADED:-}" ]]; then
  return 0
fi
RB_PREREQ_LOADED=1

# --- Output helpers (English, colours only on a TTY) --------------------------------------
if [[ -t 1 ]]; then
  C_CYAN=$'\033[36m'; C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'
  C_RED=$'\033[31m'; C_BOLD=$'\033[1m'; C_RESET=$'\033[0m'
else
  C_CYAN=''; C_GREEN=''; C_YELLOW=''; C_RED=''; C_BOLD=''; C_RESET=''
fi

log()    { echo "${C_CYAN}[INFO]${C_RESET} $*"; }
ok()     { echo "${C_GREEN}[OK]${C_RESET} $*"; }
warn()   { echo "${C_YELLOW}[WARN]${C_RESET} $*" >&2; }
err()    { echo "${C_RED}[ERROR]${C_RESET} $*" >&2; }
fail()   { echo "${C_RED}[FATAL]${C_RESET} $*" >&2; exit 1; }
banner() { echo "${C_BOLD}${C_CYAN}━━━ $* ━━━${C_RESET}"; }

# --- Paths and defaults (all overridable through the environment) -------------------------
RB_USER="${RUSHBASE_USER:-rushbase}"
RB_STATE_DIR="${RUSHBASE_STATE_DIR:-/var/lib/rushbase}"
RB_UPDATE_STATE_DIR="${RB_STATE_DIR}/update-state"
RB_BACKUP_DIR="${RB_STATE_DIR}/backups"
RB_LOG_DIR="${RUSHBASE_LOG_DIR:-/var/log/rushbase}"
RB_RUN_DIR="${RUSHBASE_RUN_DIR:-/run/rushbase}"
RB_CONF="${RB_STATE_DIR}/install.conf"
RB_UNIT_DIR="${RUSHBASE_SYSTEMD_DIR:-/etc/systemd/system}"
RB_UPDATE_LOG="${RB_UPDATE_STATE_DIR}/update.log"
RB_STATUS_FILE="${RB_UPDATE_STATE_DIR}/status.json"
RB_AVAILABLE_FILE="${RB_UPDATE_STATE_DIR}/available.json"
RB_LOCK_FILE="${RB_RUN_DIR}/update.lock"
RB_SOCKET="${RB_RUN_DIR}/updater.sock"
RB_DEFAULT_REPO="https://github.com/Elias02345/RushBase.git"
RB_UV_VERSION="${RUSHBASE_UV_VERSION:-0.12.17}"
RB_UV_RELEASE_BASE="https://github.com/astral-sh/uv/releases/download"
RB_NODE_MAJOR="${RUSHBASE_NODE_MAJOR:-24}"
RB_PNPM_VERSION="${RUSHBASE_PNPM_VERSION:-9.15.9}"
RB_PG_MAJOR=16

# Install configuration, persisted by install.sh (see conf_load).
MODE="bare"          # bare | docker
NETWORK="bridge"     # bridge | host (docker mode)
BRANCH="main"
REPO_URL="${RB_DEFAULT_REPO}"
APP_DIR="${RUSHBASE_APP_DIR:-/opt/rushbase}"
DB_MODE="local"      # local | external | docker
WEB="1"
WORKER="0"
COMPOSE_FILE="docker-compose.yml"

# --- Generic helpers ---------------------------------------------------------------------
command_exists() { command -v "$1" >/dev/null 2>&1; }

require_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    fail "This script must run as root (try: sudo bash $0)."
  fi
}

now_iso() { date -u +%Y-%m-%dT%H:%M:%SZ; }

json_escape() {
  local s="$1"
  s="${s//\\/\\\\}"
  s="${s//\"/\\\"}"
  s="${s//$'\n'/\\n}"
  s="${s//$'\r'/}"
  s="${s//$'\t'/\\t}"
  printf '%s' "${s}"
}

# retry <attempts> <delay-seconds> <command...>
retry() {
  local attempts="$1" delay="$2" n=1
  shift 2
  until "$@"; do
    if (( n >= attempts )); then
      return 1
    fi
    warn "Command failed (attempt ${n}/${attempts}), retrying in ${delay}s: $*"
    n=$((n + 1))
    sleep "${delay}"
  done
}

apt_get_once() {
  DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=120 -o Dpkg::Options::=--force-confold "$@"
}

# apt-get with a retry loop (3 attempts, 5 s delay) and a dpkg lock timeout.
apt_retry() { retry 3 5 apt_get_once "$@"; }

RB_APT_UPDATED=0
apt_update_once() {
  if (( RB_APT_UPDATED == 0 )); then
    apt_retry update -qq
    RB_APT_UPDATED=1
  fi
}

pkg_installed() { dpkg-query -W -f='${Status}' "$1" 2>/dev/null | grep -q '^install ok installed$'; }

# apt_install <package...>: installs only what is missing.
apt_install() {
  local missing=() p
  for p in "$@"; do
    pkg_installed "${p}" || missing+=("${p}")
  done
  if (( ${#missing[@]} == 0 )); then
    return 0
  fi
  log "Installing packages: ${missing[*]}"
  apt_update_once
  apt_retry install -y -qq --no-install-recommends "${missing[@]}"
}

detect_os() {
  OS_ID="unknown"; OS_CODENAME=""; OS_PRETTY="unknown"
  if [[ -r /etc/os-release ]]; then
    # shellcheck disable=SC1091
    OS_ID="$(. /etc/os-release && echo "${ID:-unknown}")"
    # shellcheck disable=SC1091
    OS_CODENAME="$(. /etc/os-release && echo "${VERSION_CODENAME:-}")"
    # shellcheck disable=SC1091
    OS_PRETTY="$(. /etc/os-release && echo "${PRETTY_NAME:-unknown}")"
  fi
}

require_supported_os() {
  detect_os
  case "${OS_ID}" in
    debian|ubuntu) ;;
    *) fail "Unsupported OS '${OS_PRETTY}'. Rushbase supports Debian 12+ and Ubuntu 22.04+." ;;
  esac
  command_exists systemctl || fail "systemd is required (systemctl not found)."
}

# --- Persisted install configuration -------------------------------------------------------
conf_load() {
  if [[ -r "${RB_CONF}" ]]; then
    # shellcheck disable=SC1090
    source "${RB_CONF}"
  fi
}

# conf_set KEY VALUE: add or replace a key in install.conf (values are shell-quoted).
conf_set() {
  local key="$1" value="$2" tmp
  mkdir -p "${RB_STATE_DIR}"
  touch "${RB_CONF}"
  tmp="$(mktemp "${RB_CONF}.XXXXXX")"
  grep -v "^${key}=" "${RB_CONF}" >"${tmp}" || true
  printf '%s=%q\n' "${key}" "${value}" >>"${tmp}"
  cat "${tmp}" >"${RB_CONF}"
  rm -f "${tmp}"
  chmod 644 "${RB_CONF}"
}

conf_save_all() {
  conf_set MODE "${MODE}"
  conf_set NETWORK "${NETWORK}"
  conf_set BRANCH "${BRANCH}"
  conf_set REPO_URL "${REPO_URL}"
  conf_set APP_DIR "${APP_DIR}"
  conf_set DB_MODE "${DB_MODE}"
  conf_set WEB "${WEB}"
  conf_set WORKER "${WORKER}"
  conf_set COMPOSE_FILE "${COMPOSE_FILE}"
}

# --- .env handling -------------------------------------------------------------------------
env_file() { printf '%s/.env' "${APP_DIR}"; }

# env_get KEY [FILE]
env_get() {
  local key="$1" file="${2:-$(env_file)}"
  [[ -r "${file}" ]] || return 0
  grep -m1 "^${key}=" "${file}" 2>/dev/null | cut -d= -f2- || true
}

# env_set KEY VALUE [FILE]: set or append KEY=VALUE, keeping file mode and ownership.
env_set() {
  local key="$1" value="$2" file="${3:-$(env_file)}" tmp
  tmp="$(mktemp "${file}.XXXXXX")"
  KEY="${key}" VAL="${value}" awk '
    BEGIN { k = ENVIRON["KEY"]; v = ENVIRON["VAL"]; done = 0 }
    !done && index($0, k "=") == 1 { print k "=" v; done = 1; next }
    { print }
    END { if (!done) print k "=" v }
  ' "${file}" >"${tmp}"
  cat "${tmp}" >"${file}"
  rm -f "${tmp}"
}

# env_set_default KEY VALUE: only writes when the key is currently empty (preserves values).
env_set_default() {
  local key="$1" value="$2"
  if [[ -z "$(env_get "${key}")" ]]; then
    env_set "${key}" "${value}"
  fi
}

gen_secret() { python3 -c "import secrets; print(secrets.token_urlsafe(${1:-48}))"; }

# existing_or_generated_secret KEY [BYTES]: keep the value from .env or generate a new one.
existing_or_generated_secret() {
  local key="$1" existing
  existing="$(env_get "${key}")"
  if [[ -n "${existing}" ]]; then
    printf '%s' "${existing}"
  else
    gen_secret "${2:-48}"
  fi
}

# --- Users, directories, permissions ------------------------------------------------------
ensure_service_user() {
  if ! id -u "${RB_USER}" >/dev/null 2>&1; then
    log "Creating system user '${RB_USER}'"
    useradd --system --home-dir "${RB_STATE_DIR}" --no-create-home --shell /usr/sbin/nologin "${RB_USER}"
  fi
}

ensure_dirs() {
  install -d -m 0755 "${RB_STATE_DIR}" "${RB_UPDATE_STATE_DIR}" "${RB_LOG_DIR}" "${RB_RUN_DIR}"
  install -d -m 0700 "${RB_BACKUP_DIR}"
  install -d -m 0755 "${RB_STATE_DIR}/python"
  install -d -m 0755 /var/cache/rushbase
}

# fix_permissions: idempotent; never touches the library, the database or the originals.
fix_permissions() {
  ensure_dirs
  local envf
  envf="$(env_file)"
  if [[ -f "${envf}" ]]; then
    if id -u "${RB_USER}" >/dev/null 2>&1; then
      chown root:"${RB_USER}" "${envf}"
    fi
    chmod 640 "${envf}"
  fi
  if [[ -f /etc/rushbase/smb.credentials ]]; then
    chown root:root /etc/rushbase/smb.credentials
    chmod 600 /etc/rushbase/smb.credentials
  fi
  chmod 700 "${RB_BACKUP_DIR}"
  chmod 644 "${RB_CONF}" 2>/dev/null || true
  if id -u "${RB_USER}" >/dev/null 2>&1; then
    chown "root:${RB_USER}" "${RB_RUN_DIR}"
    chmod 0750 "${RB_RUN_DIR}"
  fi
}

# --- Tool environment (uv / pnpm) ---------------------------------------------------------
setup_tool_env() {
  export UV_CACHE_DIR=/var/cache/rushbase/uv
  export UV_PYTHON_INSTALL_DIR="${RB_STATE_DIR}/python"
  export UV_LINK_MODE=copy
  export UV_NO_PROGRESS=1
  export npm_config_cache=/var/cache/rushbase/npm
  umask 022
}

# --- Tool installation ---------------------------------------------------------------------
download() { curl -fsSL --retry 3 --retry-delay 5 --connect-timeout 20 -o "$2" "$1"; }

# SHA-256 of the pinned uv release archives (version 0.12.17). Overriding RUSHBASE_UV_VERSION
# requires RUSHBASE_UV_SHA256 for the archive of this machine's architecture.
RB_UV_PINNED_VERSION="0.12.17"
RB_UV_SHA256_X86_64="fa82fd8dde8e8eefdecada6aa0889666556cfceb690d06e0c3bca49eb3070a63"
RB_UV_SHA256_AARCH64="d636d1b678e9e7f367ecb22b46bd1cabbed234d6bc3b4d96365d2b507f72f86c"

install_uv() {
  if command_exists uv; then
    return 0
  fi
  local arch expected tmp archive
  case "$(uname -m)" in
    x86_64) arch=x86_64; expected="${RB_UV_SHA256_X86_64}" ;;
    aarch64|arm64) arch=aarch64; expected="${RB_UV_SHA256_AARCH64}" ;;
    *) fail "Unsupported CPU architecture for uv: $(uname -m)" ;;
  esac
  if [[ "${RB_UV_VERSION}" != "${RB_UV_PINNED_VERSION}" ]]; then
    expected="${RUSHBASE_UV_SHA256:-}"
    [[ -n "${expected}" ]] || fail "RUSHBASE_UV_VERSION=${RB_UV_VERSION} needs RUSHBASE_UV_SHA256 (archive checksum)."
  fi
  log "Installing uv ${RB_UV_VERSION} (checksum verified)"
  tmp="$(mktemp -d)"
  archive="uv-${arch}-unknown-linux-gnu.tar.gz"
  download "${RB_UV_RELEASE_BASE}/${RB_UV_VERSION}/${archive}" "${tmp}/${archive}"
  printf '%s  %s\n' "${expected}" "${archive}" | (cd "${tmp}" && sha256sum -c --quiet -) \
    || { rm -rf "${tmp}"; fail "uv checksum mismatch."; }
  tar -xzf "${tmp}/${archive}" -C "${tmp}" --strip-components=1
  install -m 0755 "${tmp}/uv" "${tmp}/uvx" /usr/local/bin/
  rm -rf "${tmp}"
  command_exists uv || fail "uv installation failed."
}

install_node() {
  if command_exists node && [[ "$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)" -ge "${RB_NODE_MAJOR}" ]]; then
    return 0
  fi
  log "Installing Node.js ${RB_NODE_MAJOR}.x (checksum verified)"
  apt_install xz-utils
  local arch tmp base sums file
  case "$(uname -m)" in
    x86_64) arch=x64 ;;
    aarch64|arm64) arch=arm64 ;;
    *) fail "Unsupported CPU architecture for Node.js: $(uname -m)" ;;
  esac
  tmp="$(mktemp -d)"
  base="https://nodejs.org/dist/latest-v${RB_NODE_MAJOR}.x"
  download "${base}/SHASUMS256.txt" "${tmp}/SHASUMS256.txt"
  sums="$(grep " node-v[0-9.]*-linux-${arch}\.tar\.xz\$" "${tmp}/SHASUMS256.txt" | head -n1)"
  [[ -n "${sums}" ]] || fail "Could not find a Node.js ${RB_NODE_MAJOR}.x tarball for ${arch}."
  file="$(awk '{print $2}' <<<"${sums}")"
  download "${base}/${file}" "${tmp}/${file}"
  (cd "${tmp}" && printf '%s\n' "${sums}" | sha256sum -c --quiet -) || fail "Node.js checksum mismatch."
  rm -rf /usr/local/lib/nodejs
  mkdir -p /usr/local/lib/nodejs
  tar -xJf "${tmp}/${file}" -C /usr/local/lib/nodejs --strip-components=1
  ln -sf /usr/local/lib/nodejs/bin/node /usr/local/bin/node
  ln -sf /usr/local/lib/nodejs/bin/npm /usr/local/bin/npm
  ln -sf /usr/local/lib/nodejs/bin/npx /usr/local/bin/npx
  rm -rf "${tmp}"
}

install_pnpm() {
  if command_exists pnpm; then
    return 0
  fi
  install_node
  log "Installing pnpm ${RB_PNPM_VERSION}"
  retry 3 5 npm install -g --silent "pnpm@${RB_PNPM_VERSION}"
  ln -sf /usr/local/lib/nodejs/bin/pnpm /usr/local/bin/pnpm
}

# Adds the PGDG apt repository when the distribution does not ship PostgreSQL ${RB_PG_MAJOR}.
ensure_pgdg() {
  apt_install ca-certificates curl gnupg
  apt_update_once
  if apt-cache policy "postgresql-${RB_PG_MAJOR}" 2>/dev/null | grep -q 'Candidate: [0-9]'; then
    return 0
  fi
  detect_os
  log "Adding the PostgreSQL (PGDG) apt repository"
  install -d -m 0755 /usr/share/keyrings
  local tmp
  tmp="$(mktemp)"
  download https://www.postgresql.org/media/keys/ACCC4CF8.asc "${tmp}"
  gpg --dearmor --yes -o /usr/share/keyrings/postgresql-archive-keyring.gpg "${tmp}"
  rm -f "${tmp}"
  echo "deb [signed-by=/usr/share/keyrings/postgresql-archive-keyring.gpg] https://apt.postgresql.org/pub/repos/apt ${OS_CODENAME}-pgdg main" \
    >/etc/apt/sources.list.d/pgdg.list
  RB_APT_UPDATED=0
  apt_update_once
}

install_docker() {
  if command_exists docker && docker compose version >/dev/null 2>&1; then
    return 0
  fi
  detect_os
  log "Installing Docker Engine and the Compose plugin from the official Docker apt repository"
  apt_install ca-certificates curl gnupg
  install -d -m 0755 /etc/apt/keyrings
  download "https://download.docker.com/linux/${OS_ID}/gpg" /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/${OS_ID} ${OS_CODENAME} stable" \
    >/etc/apt/sources.list.d/docker.list
  RB_APT_UPDATED=0
  apt_update_once
  apt_install docker-ce docker-ce-cli containerd.io docker-compose-plugin
  systemctl enable --now docker >/dev/null 2>&1 || true
}

# install_system_packages: every package the current configuration needs (idempotent).
install_system_packages() {
  apt_install ca-certificates curl git gnupg python3 util-linux logrotate socat
  if [[ "${MODE}" == "bare" ]]; then
    apt_install xz-utils
    install_uv
    ensure_pgdg
    if [[ "${DB_MODE}" == "local" ]]; then
      apt_install "postgresql-${RB_PG_MAJOR}" "postgresql-${RB_PG_MAJOR}-pgvector"
    else
      apt_install "postgresql-client-${RB_PG_MAJOR}"
    fi
    if [[ "${WEB}" == "1" ]]; then
      install_pnpm
    fi
  else
    install_docker
  fi
  if [[ "${RUSHBASE_MEDIA_TOOLS:-1}" == "1" ]]; then
    apt_install ffmpeg libimage-exiftool-perl
  fi
}

# --- Compose helpers (docker mode) ---------------------------------------------------------
compose() {
  docker compose -p rushbase --project-directory "${APP_DIR}" \
    -f "${APP_DIR}/${COMPOSE_FILE}" --env-file "$(env_file)" "$@"
}

# db_port: PostgreSQL port inside the compose stack (host network mode avoids clashing with 5432).
db_port() {
  if [[ "${COMPOSE_FILE}" == *hostnet* ]]; then
    printf '%s' "$(env_get RUSHBASE_DB_PORT || true)"
  else
    printf '5432'
  fi
}

# compose_pg CMD...: run a PostgreSQL client tool inside the postgres container.
compose_pg() {
  compose exec -T -e "PGPORT=$(db_port)" postgres "$@"
}

# --- Service control -----------------------------------------------------------------------
svc_name() {
  if [[ "${MODE}" == "docker" ]]; then
    echo rushbase-compose
  else
    echo rushbase-server
  fi
}

svc_restart() { systemctl restart "$(svc_name).service"; }
svc_start()   { systemctl start "$(svc_name).service"; }
svc_stop()    { systemctl stop "$(svc_name).service"; }

journal_dump() {
  local unit="${1:-$(svc_name)}"
  echo "----- journalctl -u ${unit} -n 250 -----" >&2
  journalctl -u "${unit}" -n 250 --no-pager >&2 || true
}

server_port() {
  local p
  p="$(env_get RUSHBASE_PORT)"
  printf '%s' "${p:-8080}"
}

health_url() { printf 'http://127.0.0.1:%s/health' "$(server_port)"; }

health_ok() { curl -fsS --max-time 5 "$(health_url)" >/dev/null 2>&1; }

# wait_healthy [timeout-seconds]
wait_healthy() {
  local timeout="${1:-${RUSHBASE_HEALTH_TIMEOUT:-90}}" waited=0
  while (( waited < timeout )); do
    if health_ok; then
      return 0
    fi
    sleep 2
    waited=$((waited + 2))
  done
  return 1
}

# --- systemd units -------------------------------------------------------------------------
unit_templates() {
  local list=(rushbase-watchdog.service rushbase-watchdog.timer rushbase-updater.service
              rushbase-updater.timer rushbase-updater-handler.service)
  if [[ "${MODE}" == "docker" ]]; then
    list+=(rushbase-compose.service)
  else
    list+=(rushbase-server.service)
    if [[ "${WORKER}" == "1" ]]; then
      list+=(rushbase-worker.service)
    fi
  fi
  printf '%s\n' "${list[@]}"
}

update_interval() {
  local v
  v="$(env_get RUSHBASE_UPDATE_INTERVAL)"
  v="${v:-10min}"
  if [[ ! "${v}" =~ ^[0-9]+(s|sec|min|m|h|hr)?$ ]]; then
    warn "Invalid RUSHBASE_UPDATE_INTERVAL '${v}', using 10min."
    v=10min
  fi
  printf '%s' "${v}"
}

# render_unit TEMPLATE: print the unit with all @PLACEHOLDERS@ substituted.
render_unit() {
  local tpl="$1" lib mounts db_wants
  lib="$(env_get RUSHBASE_LIBRARY_ROOT)"
  if [[ -z "${lib}" ]]; then
    lib="$(env_get RUSHBASE_LIBRARY_HOST_PATH)"
  fi
  mounts=""
  if [[ -n "${lib}" && "${lib}" =~ ^/[A-Za-z0-9._/-]+$ ]]; then
    mounts="RequiresMountsFor=${lib}"
  fi
  db_wants=""
  if [[ "${DB_MODE}" == "local" ]]; then
    db_wants="postgresql.service"
  fi
  sed \
    -e "s|@APP_DIR@|${APP_DIR}|g" \
    -e "s|@USER@|${RB_USER}|g" \
    -e "s|@STATE_DIR@|${RB_STATE_DIR}|g" \
    -e "s|@LOG_DIR@|${RB_LOG_DIR}|g" \
    -e "s|@RUN_DIR@|${RB_RUN_DIR}|g" \
    -e "s|@UPDATE_INTERVAL@|$(update_interval)|g" \
    -e "s|@MOUNTS_FOR_LINE@|${mounts}|g" \
    -e "s|@DB_UNIT@|${db_wants}|g" \
    -e "s|@SERVER_SERVICE@|$(svc_name).service|g" \
    "${tpl}"
}

# install_units [--enable]: render templates, install changed ones, daemon-reload on change.
install_units() {
  local enable=0 changed=0 name tpl dest tmp
  if [[ "${1:-}" == "--enable" ]]; then
    enable=1
  fi
  if [[ ! "${APP_DIR}" =~ ^/[A-Za-z0-9._/-]+$ ]]; then
    fail "APP_DIR '${APP_DIR}' contains unsupported characters."
  fi
  while IFS= read -r name; do
    tpl="${APP_DIR}/deploy/systemd/${name}.in"
    dest="${RB_UNIT_DIR}/${name}"
    if [[ ! -f "${tpl}" ]]; then
      warn "Unit template missing: ${tpl}"
      continue
    fi
    tmp="$(mktemp)"
    render_unit "${tpl}" >"${tmp}"
    if grep -q '@[A-Z_]*@' "${tmp}"; then
      warn "Unit ${name} has unresolved placeholders; keeping the installed version."
      rm -f "${tmp}"
      continue
    fi
    if [[ ! -f "${dest}" ]] || ! cmp -s "${tmp}" "${dest}"; then
      install -m 0644 "${tmp}" "${dest}"
      changed=1
      log "Installed unit ${name}"
    fi
    rm -f "${tmp}"
  done < <(unit_templates)
  if (( changed == 1 )); then
    systemctl daemon-reload
  fi
  if (( enable == 1 )); then
    local unit
    while IFS= read -r unit; do
      # Oneshot services are driven by their timers; the worker unit is enabled separately.
      case "${unit}" in
        rushbase-worker.service|rushbase-updater.service|rushbase-watchdog.service) continue ;;
      esac
      systemctl enable "${unit}" >/dev/null 2>&1
    done < <(unit_templates)
  fi
}

# --- Update state (JSON files read by the web UI) ------------------------------------------
UPD_BRANCH=""; UPD_FROM=""; UPD_TO=""; UPD_STARTED=""

atomic_write() { # atomic_write FILE (content on stdin)
  local file="$1" tmp
  mkdir -p "$(dirname "${file}")"
  tmp="$(mktemp "${file}.XXXXXX")"
  cat >"${tmp}"
  chmod 644 "${tmp}"
  mv -f "${tmp}" "${file}"
}

# write_status STATE PHASE PROGRESS [MESSAGE]
# STATE: idle|running|success|failed|rolled_back|rollback_failed|up_to_date
write_status() {
  local state="$1" phase="$2" progress="$3" message="${4:-}"
  atomic_write "${RB_STATUS_FILE}" <<JSON
{
  "state": "${state}",
  "phase": "${phase}",
  "progress": ${progress},
  "branch": "$(json_escape "${UPD_BRANCH}")",
  "from_commit": "$(json_escape "${UPD_FROM}")",
  "to_commit": "$(json_escape "${UPD_TO}")",
  "message": "$(json_escape "${message}")",
  "started_at": "$(json_escape "${UPD_STARTED}")",
  "timestamp": "$(now_iso)"
}
JSON
}

# write_available AVAILABLE(true|false) BEHIND CURRENT LATEST BRANCH
write_available() {
  atomic_write "${RB_AVAILABLE_FILE}" <<JSON
{
  "available": $1,
  "behind": $2,
  "current_commit": "$(json_escape "$3")",
  "latest_commit": "$(json_escape "$4")",
  "branch": "$(json_escape "$5")",
  "checked_at": "$(now_iso)"
}
JSON
}

# acquire_update_lock: non-blocking exclusive lock on fd 9 for the life of the process.
acquire_update_lock() {
  mkdir -p "${RB_RUN_DIR}"
  exec 9>"${RB_LOCK_FILE}"
  flock -n 9 || fail "Another update or revert is already running."
}

update_lock_held() {
  mkdir -p "${RB_RUN_DIR}"
  ( exec 8>"${RB_LOCK_FILE}"; ! flock -n 8 )
}

# --- Git helpers ---------------------------------------------------------------------------
git_app() { git -C "${APP_DIR}" "$@"; }

short() { printf '%.9s' "$1"; }

# verify_signature REF: with RUSHBASE_UPDATE_REQUIRE_SIGNED=true the commit must carry a valid
# SSH signature from an allowed signer. The allowed-signers file is read from the currently
# installed tree (deploy/allowed_signers), never from the commit being verified.
verify_signature() {
  local ref="$1" require signers
  require="$(env_get RUSHBASE_UPDATE_REQUIRE_SIGNED)"
  require="${require:-${RUSHBASE_UPDATE_REQUIRE_SIGNED:-false}}"
  signers="${APP_DIR}/deploy/allowed_signers"
  if [[ "${require}" != "true" ]]; then
    warn "Signature verification is disabled (RUSHBASE_UPDATE_REQUIRE_SIGNED is not 'true'); applying unsigned updates."
    return 0
  fi
  if [[ ! -s "${signers}" ]]; then
    err "RUSHBASE_UPDATE_REQUIRE_SIGNED=true but ${signers} is missing or empty."
    return 1
  fi
  local out
  if out="$(git_app -c gpg.format=ssh -c "gpg.ssh.allowedSignersFile=${signers}" verify-commit "${ref}" 2>&1)"; then
    ok "Commit signature verified for $(short "$(git_app rev-parse "${ref}")")"
    # A release tag pointing at the commit must verify as well.
    local tag
    while IFS= read -r tag; do
      [[ -z "${tag}" ]] && continue
      if ! git_app -c gpg.format=ssh -c "gpg.ssh.allowedSignersFile=${signers}" verify-tag "${tag}" >/dev/null 2>&1; then
        err "Tag ${tag} points at ${ref} but its signature is invalid."
        return 1
      fi
    done < <(git_app tag --points-at "${ref}" 2>/dev/null)
    return 0
  fi
  err "Signature verification failed for ${ref}: ${out}"
  return 1
}

# --- Database backup / restore -------------------------------------------------------------
db_url_plain() {
  local url
  url="$(env_get RUSHBASE_DATABASE_URL)"
  printf '%s' "${url/postgresql+psycopg:/postgresql:}"
}

db_name() {
  if [[ "${DB_MODE}" == "docker" ]]; then
    echo rushbase
  else
    local url
    url="$(db_url_plain)"
    url="${url##*/}"
    printf '%s' "${url%%\?*}"
  fi
}

# db_dump FILE: custom-format dump of the application database.
db_dump() {
  local file="$1"
  case "${DB_MODE}" in
    local)    runuser -u postgres -- pg_dump -Fc "$(db_name)" >"${file}" ;;
    external) pg_dump -Fc "$(db_url_plain)" >"${file}" ;;
    docker)   compose_pg pg_dump -U rushbase -Fc rushbase >"${file}" ;;
  esac
  [[ -s "${file}" ]]
}

# db_restore FILE: replace the application database with the dump (service must be stopped).
db_restore() {
  local file="$1" name
  name="$(db_name)"
  # Refuse to drop anything unless the dump is a readable archive.
  local lister=(pg_restore --list)
  if [[ "${DB_MODE}" == "docker" ]]; then
    lister=(compose_pg pg_restore --list)
  fi
  if ! "${lister[@]}" <"${file}" >/dev/null 2>&1; then
    err "${file} is not a readable PostgreSQL archive; leaving the database untouched."
    return 1
  fi
  case "${DB_MODE}" in
    local)
      runuser -u postgres -- psql -v ON_ERROR_STOP=1 -qc "DROP DATABASE IF EXISTS \"${name}\" WITH (FORCE)"
      runuser -u postgres -- psql -v ON_ERROR_STOP=1 -qc "CREATE DATABASE \"${name}\" OWNER \"${RB_USER}\""
      # Restore as superuser (extensions need it); the dump's ownership statements hand the
      # objects back to the application role.
      runuser -u postgres -- pg_restore --exit-on-error -d "${name}" <"${file}"
      ;;
    external)
      pg_restore --clean --if-exists --no-owner -d "$(db_url_plain)" <"${file}"
      ;;
    docker)
      compose_pg psql -U rushbase -d postgres -v ON_ERROR_STOP=1 -qc "DROP DATABASE IF EXISTS rushbase WITH (FORCE)"
      compose_pg psql -U rushbase -d postgres -v ON_ERROR_STOP=1 -qc "CREATE DATABASE rushbase OWNER rushbase"
      compose_pg pg_restore -U rushbase --exit-on-error -d rushbase <"${file}"
      ;;
  esac
}

# make_backup: create a timestamped backup directory and print its path.
make_backup() {
  local ts dir commit
  ts="$(date -u +%Y%m%dT%H%M%SZ)"
  commit="$(git_app rev-parse HEAD 2>/dev/null || echo unknown)"
  dir="${RB_BACKUP_DIR}/${ts}-$(short "${commit}")"
  install -d -m 0700 "${dir}"
  if ! db_dump "${dir}/db.dump"; then
    rm -rf "${dir}"
    return 1
  fi
  cp -p "$(env_file)" "${dir}/env.backup"
  chmod 600 "${dir}/env.backup"
  cat >"${dir}/meta.json" <<JSON
{"created_at": "$(now_iso)", "commit": "$(json_escape "${commit}")", "branch": "$(json_escape "$(git_app rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)")", "db_mode": "${DB_MODE}"}
JSON
  printf '%s' "${dir}"
}

prune_backups() {
  local keep="${RUSHBASE_BACKUP_KEEP:-10}" dirs d i=0
  mapfile -t dirs < <(find "${RB_BACKUP_DIR}" -mindepth 1 -maxdepth 1 -type d | sort -r)
  for d in "${dirs[@]}"; do
    i=$((i + 1))
    if (( i > keep )); then
      rm -rf "${d}"
    fi
  done
}

latest_backup() {
  find "${RB_BACKUP_DIR}" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | sort | tail -n1
}

# --- Dependencies and migrations -----------------------------------------------------------
uv_sync() {
  setup_tool_env
  local args=(sync --frozen --no-dev --package rushbase-server)
  if [[ "${WORKER}" == "1" ]]; then
    args+=(--package rushbase-worker)
  fi
  (cd "${APP_DIR}" && uv "${args[@]}")
}

web_build() {
  if [[ "${WEB}" != "1" || ! -f "${APP_DIR}/web/package.json" ]]; then
    return 0
  fi
  setup_tool_env
  (cd "${APP_DIR}" && pnpm install --frozen-lockfile && pnpm --filter @rushbase/web build)
}

run_migrations() {
  if [[ "${MODE}" == "docker" ]]; then
    compose run --rm --no-deps server alembic -c /app/server/alembic.ini upgrade head
  else
    runuser -u "${RB_USER}" -- env -C "${APP_DIR}" "${APP_DIR}/.venv/bin/alembic" -c server/alembic.ini upgrade head
  fi
}

# --- Maintenance: prerequisites + units + permissions (watchdog, doctor --repair) ----------
repair_prerequisites() {
  require_supported_os
  conf_load
  ensure_service_user
  ensure_dirs
  install_system_packages || warn "Some prerequisites could not be installed (offline?)."
  if [[ -d "${APP_DIR}/deploy/systemd" ]]; then
    install_units
  fi
  fix_permissions
}
