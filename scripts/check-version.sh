#!/usr/bin/env bash
# Fail when any manifest carries a version different from a release tag (used by CI).
#   scripts/check-version.sh v0.9.0
#   scripts/check-version.sh 0.9.0-rc.1
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=scripts/lib/versions.sh
source "${SCRIPT_DIR}/lib/versions.sh"

if [[ -t 1 ]]; then
  C_GREEN=$'\033[32m'; C_RED=$'\033[31m'; C_RESET=$'\033[0m'
else
  C_GREEN=''; C_RED=''; C_RESET=''
fi
ok()   { echo "${C_GREEN}[OK]${C_RESET} $*"; }
err()  { echo "${C_RED}[ERROR]${C_RESET} $*" >&2; }
fail() { echo "${C_RED}[FATAL]${C_RESET} $*" >&2; exit 1; }

[[ $# -eq 1 ]] || fail "Usage: check-version.sh <tag|version>"
version="${1#v}"
is_semver "${version}" || fail "'$1' is not a version like v1.2.3 or v1.2.3-rc.1."
py_version="$(pep440 "${version}")"

cd "${ROOT}"
bad=0
while read -r file found kind; do
  want="${version}"
  if [[ "${kind}" == "pep440" ]]; then want="${py_version}"; fi
  if [[ "${found}" == "${want}" ]]; then
    ok "${file}: ${found}"
  else
    err "${file}: found '${found}', expected '${want}'"
    bad=$((bad + 1))
  fi
done < <(print_versions)

[[ "${bad}" -eq 0 ]] || fail "${bad} manifest(s) differ from $1. Run scripts/release.sh to bump them."
ok "All manifests match $1."
