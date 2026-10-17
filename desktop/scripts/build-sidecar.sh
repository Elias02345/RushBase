#!/usr/bin/env bash
# Build the worker CLI as a single-file PyInstaller executable and place it where Tauri expects
# an externalBin: desktop/src-tauri/binaries/rushbase-<target-triple>[.exe]
#
#   scripts/with-env.sh desktop/scripts/build-sidecar.sh          (from the repository root)
#
# Resolve's bridge is NOT frozen: it runs in Resolve's own interpreter (ADR-0002); only the
# script file travels as data. Needs uv and rustc (for the target triple).
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
# Git Bash on Windows yields /d/a/...; PyInstaller (a native Windows program) needs D:/a/...
if command -v cygpath >/dev/null 2>&1; then
  ROOT="$(cygpath -m "${ROOT}")"
fi
OUT_DIR="${ROOT}/desktop/src-tauri/binaries"
WORK_DIR="${ROOT}/desktop/.sidecar-build"

log() { echo "[INFO] $*"; }
fail() { echo "[FATAL] $*" >&2; exit 1; }

command -v uv >/dev/null || fail "uv is required"
TRIPLE="${TARGET_TRIPLE:-$(rustc -vV | sed -n 's/^host: //p')}"
[[ -n "${TRIPLE}" ]] || fail "cannot determine the target triple (set TARGET_TRIPLE)"
EXT=""
[[ "${TRIPLE}" == *windows* ]] && EXT=".exe"
SEP=":"
[[ "${TRIPLE}" == *windows* ]] && SEP=";"

mkdir -p "${OUT_DIR}" "${WORK_DIR}"
log "building the rushbase sidecar for ${TRIPLE}"
cd "${ROOT}"
uv run --package rushbase-worker --group sidecar pyinstaller \
  --noconfirm --clean --onefile --name rushbase \
  --distpath "${WORK_DIR}/dist" --workpath "${WORK_DIR}/build" --specpath "${WORK_DIR}" \
  --paths "${ROOT}/worker/src" --paths "${ROOT}/core/src" \
  --collect-data rushbase_worker --collect-submodules rushbase_worker \
  --collect-submodules rushbase_core \
  --collect-submodules mcp --collect-data mcp \
  --collect-submodules uvicorn \
  --add-data "${ROOT}/worker/models.lock.json${SEP}rushbase_worker/data" \
  --add-data "${ROOT}/worker/binaries.lock.json${SEP}rushbase_worker/data" \
  "${ROOT}/worker/pyinstaller/rushbase_entry.py"

cp "${WORK_DIR}/dist/rushbase${EXT}" "${OUT_DIR}/rushbase-${TRIPLE}${EXT}"
chmod +x "${OUT_DIR}/rushbase-${TRIPLE}${EXT}"
log "wrote ${OUT_DIR}/rushbase-${TRIPLE}${EXT} ($(du -h "${OUT_DIR}/rushbase-${TRIPLE}${EXT}" | cut -f1))"
"${OUT_DIR}/rushbase-${TRIPLE}${EXT}" version --json
