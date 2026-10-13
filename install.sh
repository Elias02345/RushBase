#!/usr/bin/env bash
# Thin wrapper: the implementation lives in scripts/install.sh.
set -Eeuo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "${SCRIPT_DIR}/scripts/install.sh" "$@"
