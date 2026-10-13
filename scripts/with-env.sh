#!/usr/bin/env bash
# Run a command inside the isolated project toolchain environment:
#   scripts/with-env.sh uv run pytest
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/dev-env.sh
source "${SCRIPT_DIR}/dev-env.sh"
exec "$@"
