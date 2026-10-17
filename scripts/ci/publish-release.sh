#!/usr/bin/env bash
# Finish a draft GitHub release (CI, last step of .github/workflows/desktop.yml):
# checksums, release notes (CHANGELOG section + install table), pre-release flag, publish.
#   scripts/ci/publish-release.sh v0.9.0
# Needs the `gh` CLI with GH_TOKEN (contents: write) and GITHUB_REPOSITORY.
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

log()  { echo "[INFO] $*"; }
ok()   { echo "[OK] $*"; }
fail() { echo "[FATAL] $*" >&2; exit 1; }

TAG="${1:?Usage: publish-release.sh vX.Y.Z[-suffix]}"
VERSION="${TAG#v}"
REPO="${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is not set}"
OWNER_LC="$(tr '[:upper:]' '[:lower:]' <<<"${REPO%%/*}")"
WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT

# --- pre-release / latest ----------------------------------------------------------------------
# Anything below 1.0.0 or with a suffix (-rc.1) is a pre-release and never "latest".
PRERELEASE=false
if [[ "${VERSION}" == *-* || "${VERSION}" == 0.* ]]; then PRERELEASE=true; fi

# --- required assets ---------------------------------------------------------------------------
mkdir -p "${WORK}/assets"
gh release download "${TAG}" --repo "${REPO}" --dir "${WORK}/assets" --clobber
cd "${WORK}/assets"

pick() { # pick <glob>: the single asset matching the glob, empty when missing
  local m=()
  mapfile -t m < <(compgen -G "$1" || true)
  [[ "${#m[@]}" -le 1 ]] || fail "More than one asset matches '$1': ${m[*]}"
  printf '%s' "${m[0]:-}"
}
APPIMAGE="$(pick '*.AppImage')"
DEB="$(pick '*.deb')"
RPM="$(pick '*.rpm')"
DMG="$(pick '*.dmg')"
MSI="$(pick '*.msi')"
NSIS="$(pick '*-setup.exe')"
ARCH_PKG="$(pick 'rushbase-bin-*.pkg.tar.zst')"
CLI_LINUX="$(pick "rushbase-${VERSION}-linux-*")"
CLI_MAC="$(pick "rushbase-${VERSION}-macos-*")"
CLI_WIN="$(pick "rushbase-${VERSION}-windows-*.exe")"
for pair in AppImage:"${APPIMAGE}" deb:"${DEB}" rpm:"${RPM}" dmg:"${DMG}" msi:"${MSI}" \
  nsis:"${NSIS}" arch:"${ARCH_PKG}" cli-linux:"${CLI_LINUX}" cli-macos:"${CLI_MAC}" cli-windows:"${CLI_WIN}"; do
  [[ -n "${pair#*:}" ]] || fail "Release ${TAG} is missing its ${pair%%:*} asset; not publishing."
done

# --- checksums ---------------------------------------------------------------------------------
rm -f SHA256SUMS
sha256sum -- * >../SHA256SUMS
mv ../SHA256SUMS SHA256SUMS
gh release upload "${TAG}" --repo "${REPO}" --clobber SHA256SUMS
ok "Uploaded SHA256SUMS ($(wc -l <SHA256SUMS) files)"

# --- notes ---------------------------------------------------------------------------------------
changelog="$(awk -v h="## [${VERSION}]" '
  index($0, h) == 1 { on = 1; next }
  on && /^## \[/ { exit }
  on { print }
' "${ROOT}/CHANGELOG.md")"
[[ -n "${changelog//[[:space:]]/}" ]] || changelog="See [CHANGELOG.md](https://github.com/${REPO}/blob/${TAG}/CHANGELOG.md)."

IMG_SERVER="ghcr.io/${OWNER_LC}/rushbase-server"
IMG_WORKER="ghcr.io/${OWNER_LC}/rushbase-worker"
{
  echo "${changelog}"
  cat <<EOF

## Install

| Platform | Download |
| --- | --- |
| Windows | [\`${MSI}\`](https://github.com/${REPO}/releases/download/${TAG}/${MSI}) (MSI) or [\`${NSIS}\`](https://github.com/${REPO}/releases/download/${TAG}/${NSIS}) (NSIS, per user) |
| macOS (Apple Silicon) | [\`${DMG}\`](https://github.com/${REPO}/releases/download/${TAG}/${DMG}) |
| Linux | [\`${APPIMAGE}\`](https://github.com/${REPO}/releases/download/${TAG}/${APPIMAGE}), [\`${DEB}\`](https://github.com/${REPO}/releases/download/${TAG}/${DEB}) or [\`${RPM}\`](https://github.com/${REPO}/releases/download/${TAG}/${RPM}) |
| Arch Linux / CachyOS / Manjaro | \`sudo pacman -U https://github.com/${REPO}/releases/download/${TAG}/${ARCH_PKG}\` (adds Rushbase to the KDE/GNOME menu) |
| Headless worker (no GUI) | \`${CLI_LINUX}\`, \`${CLI_MAC}\`, \`${CLI_WIN}\`: run \`rushbase enroll\` and \`rushbase process\` |

Verify downloads with \`SHA256SUMS\` (\`sha256sum -c SHA256SUMS --ignore-missing\`).

**The apps are not code-signed yet.**

- **macOS:** right-click the app, choose *Open*, confirm. Or run \`xattr -dr com.apple.quarantine /Applications/Rushbase.app\`.
- **Windows:** SmartScreen shows "Windows protected your PC": click *More info*, then *Run anyway*.
- **Linux AppImage:** \`chmod +x ${APPIMAGE} && ./${APPIMAGE}\`. Needs \`webkit2gtk-4.1\`.
- **AppImage menu entry (KDE, GNOME):** an AppImage does not add itself to the launcher. Use [AppImageLauncher](https://github.com/TheAssassin/AppImageLauncher) or [Gear Lever](https://github.com/pkgforge-dev/Gear-Lever-AppImage) (Flatpak \`it.mijorus.gearlever\`), or install the \`.deb\`/\`.rpm\`/Arch package instead.
- **Headless binaries (Linux/macOS):** \`chmod +x rushbase-${VERSION}-*\` before the first run.

### Server

\`\`\`sh
git clone --branch ${TAG} https://github.com/${REPO}.git && cd RushBase
sudo bash install.sh
\`\`\`

Docker mode uses the published image \`${IMG_SERVER}:${VERSION}\` when the checkout is on this tag and
falls back to a local build otherwise.

### Docker images

- Server: \`${IMG_SERVER}:${VERSION}\`
- Worker: \`${IMG_WORKER}:${VERSION}-cpu\`, \`-cuda\`, \`-vulkan\`, \`-rocm\`
EOF
  if [[ "${VERSION}" != *-* ]]; then
    echo "- Moving tags: \`${IMG_SERVER}:latest\`, \`${IMG_WORKER}:latest-<variant>\`"
  fi
} >"${WORK}/notes.md"

# --- publish -------------------------------------------------------------------------------------
latest_flag="--latest=false"
if [[ "${PRERELEASE}" == false ]]; then latest_flag="--latest"; fi
gh release edit "${TAG}" --repo "${REPO}" --title "Rushbase ${TAG}" --notes-file "${WORK}/notes.md" \
  --prerelease="${PRERELEASE}" "${latest_flag}" --draft=false
ok "Published ${TAG} (prerelease=${PRERELEASE})"
