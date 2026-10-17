#!/usr/bin/env bash
# Build rushbase-bin from a release .deb inside a throwaway Arch Linux container (makepkg refuses
# to run as root, so a build user is created). Writes into <outdir>:
#   rushbase-bin-<ver>-1-x86_64.pkg.tar.zst, PKGBUILD (real sha256), .SRCINFO
# and validates the desktop entry and icons in the package. Nothing is installed on the host.
#   scripts/ci/build-arch-package.sh <path-to-Rushbase_X.Y.Z_amd64.deb> <outdir>
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
log()  { echo "[INFO] $*"; }
fail() { echo "[FATAL] $*" >&2; exit 1; }

DEB="${1:?Usage: build-arch-package.sh <deb> <outdir>}"
OUT="${2:?Usage: build-arch-package.sh <deb> <outdir>}"
[[ -f "${DEB}" ]] || fail "No such file: ${DEB}"
deb_name="$(basename "${DEB}")"
if [[ ! "${deb_name}" =~ ^Rushbase_(.+)_amd64\.deb$ ]]; then fail "Unexpected deb name: ${deb_name}"; fi
pkgver="${BASH_REMATCH[1]//-/_}"
mkdir -p "${OUT}"
OUT="$(cd "${OUT}" && pwd)"
DEB="$(cd "$(dirname "${DEB}")" && pwd)/${deb_name}"

name="rushbase-arch-$$"
cleanup() { docker rm -f "${name}" >/dev/null 2>&1 || true; }
trap cleanup EXIT

log "Building rushbase-bin ${pkgver} in an Arch container (${name})"
docker run --name "${name}" \
  -v "${DEB}:/in/${deb_name}:ro" \
  -v "${ROOT}/deploy/aur/PKGBUILD:/in/PKGBUILD:ro" \
  -v "${OUT}:/out" \
  -e "PKGVER=${pkgver}" -e "DEB_NAME=${deb_name}" -e "HOST_UID=$(id -u)" -e "HOST_GID=$(id -g)" \
  archlinux:latest bash -Eeuo pipefail -c '
    pacman -Syu --noconfirm --needed base-devel git libarchive desktop-file-utils >/dev/null
    useradd -m builder
    install -d -o builder -g builder /home/builder/pkg
    install -m644 -o builder -g builder /in/PKGBUILD /home/builder/pkg/PKGBUILD
    install -m644 -o builder -g builder "/in/${DEB_NAME}" "/home/builder/pkg/${DEB_NAME}"
    cd /home/builder/pkg
    sed -i "s/^pkgver=.*/pkgver=${PKGVER}/" PKGBUILD
    # The deb is already next to the PKGBUILD, so makepkg uses it instead of downloading it.
    su builder -c "updpkgsums"
    su builder -c "makepkg --noconfirm --nodeps"
    su builder -c "makepkg --printsrcinfo > .SRCINFO"
    pkg="$(ls rushbase-bin-*.pkg.tar.zst)"

    echo "[INFO] package contents (desktop entry, icons, binaries)"
    bsdtar -tf "${pkg}" | grep -E "usr/bin/|usr/share/applications/.*\.desktop|usr/share/icons/hicolor/" | tee /tmp/listing
    for need in usr/share/applications/ hicolor/32x32/apps/rushbase.png hicolor/64x64/apps/rushbase.png \
        hicolor/128x128/apps/rushbase.png hicolor/256x256/apps/rushbase.png hicolor/512x512/apps/rushbase.png \
        hicolor/scalable/apps/rushbase.svg usr/bin/rushbase-desktop usr/bin/rushbase; do
      grep -q "${need}" /tmp/listing || { echo "[FATAL] package lacks ${need}" >&2; exit 1; }
    done
    mkdir /tmp/x && bsdtar -xf "${pkg}" -C /tmp/x
    for d in /tmp/x/usr/share/applications/*.desktop; do
      desktop-file-validate "${d}"
      echo "[OK] desktop-file-validate ${d##*/}"
      grep -q "^Icon=rushbase$" "${d}"
    done
    cp "${pkg}" PKGBUILD .SRCINFO /out/
    chown "${HOST_UID}:${HOST_GID}" /out/*
  '
log "Wrote $(ls "${OUT}"/rushbase-bin-*.pkg.tar.zst)"
