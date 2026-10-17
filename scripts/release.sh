#!/usr/bin/env bash
# Cut a release: bump every manifest, roll the changelog, commit and tag. Never pushes.
#   scripts/release.sh 0.9.0                       on main
#   scripts/release.sh 0.9.0-rc.1 --branch dev
#   scripts/release.sh 0.9.0 --dry-run             show the diff and revert everything
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=scripts/lib/versions.sh
source "${SCRIPT_DIR}/lib/versions.sh"

if [[ -t 1 ]]; then
  C_CYAN=$'\033[36m'; C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'
  C_RED=$'\033[31m'; C_BOLD=$'\033[1m'; C_RESET=$'\033[0m'
else
  C_CYAN=''; C_GREEN=''; C_YELLOW=''; C_RED=''; C_BOLD=''; C_RESET=''
fi
log()  { echo "${C_CYAN}[INFO]${C_RESET} $*"; }
ok()   { echo "${C_GREEN}[OK]${C_RESET} $*"; }
warn() { echo "${C_YELLOW}[WARN]${C_RESET} $*" >&2; }
fail() { echo "${C_RED}[FATAL]${C_RESET} $*" >&2; exit 1; }

BRANCH=main
DRY_RUN=0
VERSION=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --branch) BRANCH="${2:?--branch needs a value}"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) sed -n '2,6p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) fail "Unknown option: $1" ;;
    *)
      [[ -z "${VERSION}" ]] || fail "Only one version is accepted."
      VERSION="${1#v}"
      shift
      ;;
  esac
done
[[ -n "${VERSION}" ]] || fail "Usage: release.sh <X.Y.Z[-rc.N]> [--branch <name>] [--dry-run]"
is_semver "${VERSION}" || fail "'${VERSION}' is not a version like 1.2.3 or 1.2.3-rc.1."
PY_VERSION="$(pep440 "${VERSION}")"
TAG="v${VERSION}"

cd "${ROOT}"
[[ -z "$(git status --porcelain)" ]] || fail "The working tree is not clean."
current="$(git rev-parse --abbrev-ref HEAD)"
[[ "${current}" == "${BRANCH}" ]] || fail "On branch '${current}', expected '${BRANCH}' (see --branch)."
if git rev-parse -q --verify "refs/tags/${TAG}" >/dev/null; then fail "Tag ${TAG} already exists."; fi
grep -q '^## \[Unreleased\]' CHANGELOG.md || fail "CHANGELOG.md has no [Unreleased] section."

revert() { git checkout -q -- .; }
if [[ "${DRY_RUN}" -eq 1 ]]; then trap revert EXIT; fi

# --- bump ---------------------------------------------------------------------------------------
log "Bumping manifests to ${VERSION}"
for f in "${VERSION_PYPROJECTS[@]}"; do
  sed -i "0,/^version = \".*\"\$/s//version = \"${PY_VERSION}\"/" "${f}"
done
sed -i "0,/^version = \".*\"\$/s//version = \"${VERSION}\"/" "${VERSION_CARGO_TOML}"
for f in "${VERSION_PACKAGE_JSONS[@]}" "${VERSION_TAURI_CONF}"; do
  sed -i "0,/^  \"version\": \"[^\"]*\"/s//  \"version\": \"${VERSION}\"/" "${f}"
done
# Cargo.lock: only the entry of the desktop crate (the line after its name).
sed -i "/^name = \"rushbase-desktop\"\$/{n;s/^version = \".*\"/version = \"${VERSION}\"/}" "${VERSION_CARGO_LOCK}"

# uv.lock records the workspace members' versions; let uv rewrite it when it is available.
if command -v uv >/dev/null 2>&1; then
  uv lock --offline >/dev/null 2>&1 || uv lock >/dev/null
else
  warn "uv not found: patching uv.lock by hand"
  for p in "${VERSION_UV_PACKAGES[@]}"; do
    sed -i "/^name = \"${p}\"\$/{n;s/^version = \".*\"/version = \"${PY_VERSION}\"/}" "${VERSION_UV_LOCK}"
  done
fi
# pnpm-lock.yaml does not record workspace package versions, so there is nothing to refresh.

# --- changelog ----------------------------------------------------------------------------------
today="$(date -u +%Y-%m-%d)"
awk -v v="${VERSION}" -v d="${today}" '
  !done && /^## \[Unreleased\]/ { print; print ""; print "## [" v "] - " d; done = 1; next }
  { print }
' CHANGELOG.md >CHANGELOG.md.new
mv CHANGELOG.md.new CHANGELOG.md

"${SCRIPT_DIR}/check-version.sh" "${TAG}" >/dev/null || fail "Version check failed after the bump."
ok "All manifests are at ${VERSION}"

if [[ "${DRY_RUN}" -eq 1 ]]; then
  git --no-pager diff --stat
  git --no-pager diff -U0 | head -n 150
  warn "Dry run: reverting all changes."
  exit 0
fi

# --- commit and tag -----------------------------------------------------------------------------
git add -A
git commit -q -m "chore(release): ${TAG}"
if [[ -n "$(git config user.signingkey || true)" ]]; then
  git tag -s "${TAG}" -m "Rushbase ${TAG}"
  ok "Created signed tag ${TAG}"
else
  warn "user.signingkey is not configured: creating an annotated, UNSIGNED tag."
  git tag -a "${TAG}" -m "Rushbase ${TAG}"
fi

echo
echo "${C_BOLD}Release ${TAG} is committed and tagged locally. Publish it with:${C_RESET}"
echo "  git push origin ${BRANCH}"
echo "  git push origin ${TAG}"
