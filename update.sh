#!/usr/bin/env bash
# Thin wrapper: the implementation lives in scripts/update.sh.
set -Eeuo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "${SCRIPT_DIR}/scripts/update.sh" "$@"
