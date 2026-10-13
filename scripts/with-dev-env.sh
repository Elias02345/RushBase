#!/usr/bin/env bash
# Run a command with the toolchain environment plus the generated development settings
# (dev/data/dev.env, created by `make dev-up`):
#   scripts/with-dev-env.sh uv run rushbase-server
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/dev-env.sh
source "${SCRIPT_DIR}/dev-env.sh"

env_file="${RUSHBASE_ROOT}/dev/data/dev.env"
if [[ ! -f "${env_file}" ]]; then
  echo "[FATAL] ${env_file} missing; run 'make dev-up' first" >&2
  exit 1
fi
set -a
# shellcheck disable=SC1090
source "${env_file}"
set +a
exec "$@"
