#!/usr/bin/env bash
# End-to-end kill test (milestone M1): SIGKILL a fake worker mid-job and assert that the job is
# re-leased to a second worker. Requires `make dev-up` (PostgreSQL). If no server answers on
# 127.0.0.1:18080 a private one with short timings is started and removed afterwards.
#   scripts/kill-test.sh
# Against an already running dev server set KILL_TEST_USER / KILL_TEST_PASSWORD (an admin).
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${SCRIPT_DIR}/with-dev-env.sh" uv run python "${SCRIPT_DIR}/kill_test.py" "$@"
