#!/usr/bin/env bash
# Runs INSIDE the sandbox container (see scripts/sandbox-test.sh). Never run this on a host:
# it installs packages, creates users and registers systemd units.
set -Eeuo pipefail

if [[ ! -f /.dockerenv && -z "${container:-}" ]]; then
  echo "[FATAL] guest-test.sh must run inside the sandbox container." >&2
  exit 1
fi

SRC=/src
WORK=/tmp/work
ORIGIN=/srv/origin.git
APP=/opt/rushbase
PORT=8080
export GIT_AUTHOR_NAME=sandbox GIT_AUTHOR_EMAIL=sandbox@example.invalid
export GIT_COMMITTER_NAME=sandbox GIT_COMMITTER_EMAIL=sandbox@example.invalid
export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=init.defaultBranch GIT_CONFIG_VALUE_0=main

PASSED=(); FAILED=()
CURRENT=""

phase() { CURRENT="$1"; echo; echo "━━━ PHASE: $1 ━━━"; }
pass()  { echo "  PASS  $1"; PASSED+=("${CURRENT}: $1"); }
flunk() { echo "  FAIL  $1" >&2; FAILED+=("${CURRENT}: $1"); }
# assert DESCRIPTION CMD...
assert() {
  local desc="$1"
  shift
  if "$@" >/dev/null 2>&1; then pass "${desc}"; else flunk "${desc}"; fi
}
# assert_eq DESCRIPTION EXPECTED ACTUAL
assert_eq() {
  if [[ "$2" == "$3" ]]; then pass "$1"; else flunk "$1 (expected '$2', got '$3')"; fi
}

psql_app() { runuser -u postgres -- psql -d rushbase -Atqc "$1"; }
health_json() { curl -fsS --max-time 5 "http://127.0.0.1:${PORT}/health"; }
head_commit() { git -C "${APP}" rev-parse HEAD; }
json_field() { python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])' "$1" "$2"; }
env_value() { grep -m1 "^$1=" "${APP}/.env" | cut -d= -f2-; }

push_commit() { # MESSAGE [-S]  : commit everything in WORK and push to the origin
  git -C "${WORK}" add -A
  git -C "${WORK}" commit -q "${@:2}" -m "$1"
  git -C "${WORK}" push -q --force "${ORIGIN}" main
}

install_args=(--mode bare --yes --repo "${ORIGIN}" --branch main --library /srv/library)
if [[ "${RUSHBASE_SANDBOX_WITH_WEB:-0}" != "1" ]]; then
  install_args+=(--no-web)
fi

# ----------------------------------------------------------------------------------------------
phase "setup"
mkdir -p "${WORK}" /srv
cp -a "${SRC}/." "${WORK}/"
git -C "${WORK}" init -q
git -C "${WORK}" add -A
git -C "${WORK}" commit -q -m "sandbox baseline"
git clone -q --bare "${WORK}" "${ORIGIN}"
git -C "${WORK}" remote add origin "${ORIGIN}"
BASE_COMMIT="$(git -C "${WORK}" rev-parse HEAD)"
assert "local origin repository created" test -d "${ORIGIN}"

# ----------------------------------------------------------------------------------------------
phase "install"
if bash "${WORK}/install.sh" "${install_args[@]}"; then pass "install.sh exits 0"; else flunk "install.sh exits 0"; fi
assert "checkout is at the baseline commit" test "$(head_commit)" = "${BASE_COMMIT}"
assert_eq "health reports database=true" true "$(health_json | python3 -c 'import json,sys; print(str(json.load(sys.stdin)["database"]).lower())')"
assert "rushbase-server.service active" systemctl is-active --quiet rushbase-server
assert "rushbase-server.service enabled" systemctl is-enabled --quiet rushbase-server
assert "watchdog timer active" systemctl is-active --quiet rushbase-watchdog.timer
assert "updater timer active" systemctl is-active --quiet rushbase-updater.timer
assert "updater handler active" systemctl is-active --quiet rushbase-updater-handler
assert ".env mode is 640" test "$(stat -c %a "${APP}/.env")" = "640"
SECRET="$(env_value RUSHBASE_SECRET_KEY)"
assert "secret key generated (>= 32 chars)" test "${#SECRET}" -ge 32
assert "server binds 127.0.0.1 only" bash -c "ss -ltnH | grep -q '127.0.0.1:${PORT}' && ! ss -ltnH | grep -qE '(0.0.0.0|\\*|\\[::\\]):${PORT}'"
# The installed head revision is whatever the repository ships; the sandbox migrations build on it.
BASE_REV="$(psql_app 'SELECT version_num FROM alembic_version')"
assert "alembic revision recorded after install (${BASE_REV})" test -n "${BASE_REV}"
assert "install.conf written" grep -q '^MODE=bare' /var/lib/rushbase/install.conf

# ----------------------------------------------------------------------------------------------
phase "idempotency"
if bash "${WORK}/install.sh" "${install_args[@]}"; then pass "second install.sh run exits 0"; else flunk "second install.sh run exits 0"; fi
assert_eq "secret key preserved" "${SECRET}" "$(env_value RUSHBASE_SECRET_KEY)"
assert "server still healthy" health_json

# ----------------------------------------------------------------------------------------------
phase "updater-socket"
assert "socket exists" test -S /run/rushbase/updater.sock
STATUS_REPLY="$(bash "${APP}/scripts/updater-handler.sh" request status || true)"
assert_eq "status request answers ok=true" True "$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["ok"])' "${STATUS_REPLY}" 2>/dev/null || echo error)"
assert_eq "bad request is rejected" False "$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["ok"])' "$(printf 'update ../x\n' | bash "${APP}/scripts/updater-handler.sh" serve)")"

# ----------------------------------------------------------------------------------------------
phase "update-signature-required"
# Update 1: a new migration plus a data row, commit signed with a throw-away SSH key.
ssh-keygen -q -t ed25519 -N '' -f /tmp/signing_key
printf '* %s\n' "$(cut -d' ' -f1,2 /tmp/signing_key.pub)" >"${APP}/deploy/allowed_signers"
cat >"${WORK}/server/src/rushbase_server/migrations/versions/9001_sandbox_probe.py" <<'PY'
"""Sandbox migration: adds a probe table (idempotent)."""

from collections.abc import Sequence

from alembic import op

revision: str = "sandbox1"
down_revision: str | None = "@BASE_REV@"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE TABLE IF NOT EXISTS sandbox_probe (id serial PRIMARY KEY, note text)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS sandbox_probe")
PY
sed -i "s/@BASE_REV@/${BASE_REV}/" "${WORK}/server/src/rushbase_server/migrations/versions/9001_sandbox_probe.py"
git -C "${WORK}" config gpg.format ssh
git -C "${WORK}" config user.signingkey /tmp/signing_key.pub
git -C "${WORK}" config gpg.ssh.allowedSignersFile "${APP}/deploy/allowed_signers"
push_commit "sandbox: add probe migration (unsigned)"
COMMIT_UPDATE1_UNSIGNED="$(git -C "${WORK}" rev-parse HEAD)"
env_set_line() { sed -i "s|^$1=.*|$1=$2|" "${APP}/.env"; }
env_set_line RUSHBASE_UPDATE_REQUIRE_SIGNED true
set +e
RUSHBASE_HEALTH_TIMEOUT=30 bash "${APP}/update.sh" >/tmp/update-unsigned.log 2>&1
rc=$?
set -e
assert_eq "unsigned update is refused (exit 1)" 1 "${rc}"
assert "installed commit unchanged" test "$(head_commit)" = "${BASE_COMMIT}"
assert_eq "status.json shows failed" failed "$(json_field /var/lib/rushbase/update-state/status.json state)"
assert_eq "status.json phase is verify" verify "$(json_field /var/lib/rushbase/update-state/status.json phase)"

# ----------------------------------------------------------------------------------------------
phase "update-notify-only"
env_set_line RUSHBASE_UPDATE_REQUIRE_SIGNED false
if bash "${APP}/scripts/updater-service.sh" once; then pass "updater-service.sh once exits 0"; else flunk "updater-service.sh once exits 0"; fi
assert_eq "availability recorded" True "$(json_field /var/lib/rushbase/update-state/available.json available)"
assert "notify-only did not apply the update" test "$(head_commit)" = "${BASE_COMMIT}"

# ----------------------------------------------------------------------------------------------
phase "update-signed"
git -C "${WORK}" commit -q --amend -S -m "sandbox: add probe migration (signed)"
git -C "${WORK}" push -q --force "${ORIGIN}" main
COMMIT_UPDATE1="$(git -C "${WORK}" rev-parse HEAD)"
assert "amended commit differs" test "${COMMIT_UPDATE1}" != "${COMMIT_UPDATE1_UNSIGNED}"
env_set_line RUSHBASE_UPDATE_REQUIRE_SIGNED true
if RUSHBASE_HEALTH_TIMEOUT=60 bash "${APP}/update.sh"; then pass "signed update applies (exit 0)"; else flunk "signed update applies (exit 0)"; fi
assert "checkout moved to the new commit" test "$(head_commit)" = "${COMMIT_UPDATE1}"
assert_eq "sandbox migration applied" sandbox1 "$(psql_app 'SELECT version_num FROM alembic_version')"
assert_eq "health ok after update" true "$(health_json | python3 -c 'import json,sys; print(str(json.load(sys.stdin)["database"]).lower())')"
assert_eq "status.json state is success" success "$(json_field /var/lib/rushbase/update-state/status.json state)"
assert_eq "status.json progress is 100" 100 "$(json_field /var/lib/rushbase/update-state/status.json progress)"
assert_eq "status.json records the commit" "${COMMIT_UPDATE1}" "$(json_field /var/lib/rushbase/update-state/status.json to_commit)"
assert "backup directory with dump exists" bash -c 'ls /var/lib/rushbase/backups/*/db.dump'
assert "update.log has content" test -s /var/lib/rushbase/update-state/update.log
assert_eq "secret key preserved across update" "${SECRET}" "$(env_value RUSHBASE_SECRET_KEY)"
psql_app "INSERT INTO sandbox_probe (note) VALUES ('before-broken-update')" >/dev/null
BACKUP_AFTER_UPDATE1_COUNT="$(psql_app 'SELECT count(*) FROM sandbox_probe')"

# ----------------------------------------------------------------------------------------------
phase "rollback-on-broken-update"
cat >"${WORK}/server/src/rushbase_server/migrations/versions/9002_sandbox_bad.py" <<'PY'
"""Sandbox migration that ships with a release which cannot start."""

from collections.abc import Sequence

from alembic import op

revision: str = "sandbox2"
down_revision: str | None = "sandbox1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE TABLE IF NOT EXISTS sandbox_bad (id serial PRIMARY KEY)")
    op.execute("DELETE FROM sandbox_probe")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS sandbox_bad")
PY
# Break start-up: the server process exits before serving (the migration CLI keeps working).
sed -i '/^def serve(/a\    raise SystemExit("sandbox: simulated broken release")' \
  "${WORK}/server/src/rushbase_server/main.py"
grep -q 'simulated broken release' "${WORK}/server/src/rushbase_server/main.py" \
  || flunk "could not inject the simulated breakage into main.py"
git -C "${WORK}" add -A
git -C "${WORK}" commit -q -S -m "sandbox: broken release"
git -C "${WORK}" push -q --force "${ORIGIN}" main
COMMIT_BROKEN="$(git -C "${WORK}" rev-parse HEAD)"
set +e
RUSHBASE_HEALTH_TIMEOUT=25 bash "${APP}/update.sh" >/tmp/update-broken.log 2>&1
rc=$?
set -e
tail -n 25 /tmp/update-broken.log || true
assert_eq "broken update exits 2 (rolled back)" 2 "${rc}"
assert "checkout restored to the pre-update commit" test "$(head_commit)" = "${COMMIT_UPDATE1}"
assert_eq "server healthy again" true "$(health_json | python3 -c 'import json,sys; print(str(json.load(sys.stdin)["database"]).lower())')"
assert_eq "alembic revision restored to sandbox1" sandbox1 "$(psql_app 'SELECT version_num FROM alembic_version')"
assert_eq "table from the broken migration is gone" "" "$(psql_app "SELECT to_regclass('public.sandbox_bad')")"
assert_eq "data deleted by the broken migration is back" "${BACKUP_AFTER_UPDATE1_COUNT}" "$(psql_app 'SELECT count(*) FROM sandbox_probe')"
assert_eq "status.json state is rolled_back" rolled_back "$(json_field /var/lib/rushbase/update-state/status.json state)"
assert_eq "status.json records the failed target" "${COMMIT_BROKEN}" "$(json_field /var/lib/rushbase/update-state/status.json to_commit)"
assert_eq ".env restored unchanged" "${SECRET}" "$(env_value RUSHBASE_SECRET_KEY)"
assert "update.log mentions the rollback" grep -qi 'rolling back' /var/lib/rushbase/update-state/update.log

# ----------------------------------------------------------------------------------------------
phase "watchdog"
env_set_line RUSHBASE_UPDATE_REQUIRE_SIGNED false
systemctl stop rushbase-watchdog.timer
rm -f /var/lib/rushbase/watchdog.state /var/log/rushbase/watchdog.log
systemctl stop rushbase-server
assert "server is down" bash -c "! curl -fsS --max-time 3 http://127.0.0.1:${PORT}/health"
bash "${APP}/scripts/watchdog.sh" || true
bash "${APP}/scripts/watchdog.sh" || true
assert "no restart before the threshold" bash -c "! systemctl is-active --quiet rushbase-server"
bash "${APP}/scripts/watchdog.sh" || true
assert "watchdog restarted the service after 3 failures" systemctl is-active --quiet rushbase-server
assert "service healthy after watchdog restart" health_json
assert "watchdog log is JSON lines" python3 -c '
import json
lines = [json.loads(l) for l in open("/var/log/rushbase/watchdog.log")]
events = {l["event"] for l in lines}
assert {"health_failed", "restarting", "recovered"} <= events, events
'
systemctl start rushbase-watchdog.timer

# ----------------------------------------------------------------------------------------------
phase "smb-config"
export RUSHBASE_FSTAB=/tmp/fstab.test RUSHBASE_SMB_CRED=/tmp/smb.cred RB_SMB_SKIP_MOUNT=1
smb_run() {
  bash -c 'source "$1/scripts/lib/prereq.sh"; source "$1/scripts/lib/smb.sh"; smb_configure //nas.invalid/media /tmp/mnt-media user "p@ss word" WORKGROUP' _ "${APP}" >/dev/null
}
smb_run
smb_run
assert "credentials file mode is 600" test "$(stat -c %a /tmp/smb.cred)" = "600"
assert "fstab contains exactly one managed block" test "$(grep -c 'rushbase smb (managed' /tmp/fstab.test)" = "1"
assert "mount options follow ADR-0005" grep -q 'vers=3.1.1,cache=strict,actimeo=1' /tmp/fstab.test
assert "mount options exclude cache=loose" bash -c '! grep -q cache=loose /tmp/fstab.test'
unset RUSHBASE_FSTAB RUSHBASE_SMB_CRED RB_SMB_SKIP_MOUNT

# ----------------------------------------------------------------------------------------------
phase "manual-revert"
OLDEST_BACKUP="$(find /var/lib/rushbase/backups -mindepth 1 -maxdepth 1 -type d | sort | head -n1)"
if bash "${APP}/revert-update.sh" --yes --backup "${OLDEST_BACKUP}"; then pass "revert-update.sh exits 0"; else flunk "revert-update.sh exits 0"; fi
assert "checkout restored to the baseline commit" test "$(head_commit)" = "${BASE_COMMIT}"
assert_eq "alembic revision back to the installed head" "${BASE_REV}" "$(psql_app 'SELECT version_num FROM alembic_version')"
assert "server healthy after manual revert" health_json

# ----------------------------------------------------------------------------------------------
phase "doctor"
mkdir -p /srv/library/.rushbase
chown rushbase:rushbase /srv/library/.rushbase
set +e
bash "${APP}/doctor.sh" --json >/tmp/doctor.json 2>/tmp/doctor.err
rc=$?
set -e
assert "doctor --json is valid JSON" python3 -m json.tool /tmp/doctor.json
python3 -c '
import json
d = json.load(open("/tmp/doctor.json"))
for c in d["checks"]:
    print("  doctor %-6s %-26s %s" % (c["status"], c["name"], c["detail"]))
print("  doctor summary:", d["summary"])
'
assert_eq "doctor exit status is 0 (no errors)" 0 "${rc}"
assert "doctor found the library read/write test" python3 -c '
import json
d = json.load(open("/tmp/doctor.json"))
assert any(c["name"] == "library-rw" and c["status"] == "ok" for c in d["checks"])
'
bash "${APP}/doctor.sh" >/tmp/doctor.txt 2>&1 || true
assert "doctor text output has [OK] lines and a summary" grep -q '^.*\[OK\]' /tmp/doctor.txt

# ----------------------------------------------------------------------------------------------
echo
echo "━━━ SUMMARY ━━━"
echo "passed: ${#PASSED[@]}   failed: ${#FAILED[@]}"
if (( ${#FAILED[@]} > 0 )); then
  printf '  FAILED  %s\n' "${FAILED[@]}"
  exit 1
fi
