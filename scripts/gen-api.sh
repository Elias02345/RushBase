#!/usr/bin/env bash
# Regenerate packages/api-client from the server's OpenAPI schema.
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=scripts/dev-env.sh
source "${SCRIPT_DIR}/dev-env.sh"

cd "${ROOT}"
uv run python -m rushbase_server.openapi > packages/api-client/openapi.json
pnpm --filter @rushbase/api-client gen
echo "[OK] packages/api-client/src/schema.d.ts regenerated"
