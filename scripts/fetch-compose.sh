#!/usr/bin/env bash
# Download a pinned standalone Docker Compose binary into the project toolchain
# when the host has no `docker compose` plugin. Verifies the published checksum.
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/dev-env.sh
source "${SCRIPT_DIR}/dev-env.sh"

COMPOSE_VERSION="v5.6.0"
case "$(uname -s)-$(uname -m)" in
  Linux-x86_64) asset="docker-compose-linux-x86_64" ;;
  Linux-aarch64) asset="docker-compose-linux-aarch64" ;;
  Darwin-arm64) asset="docker-compose-darwin-aarch64" ;;
  Darwin-x86_64) asset="docker-compose-darwin-x86_64" ;;
  *) echo "[FATAL] unsupported platform for fetch-compose.sh" >&2; exit 1 ;;
esac

target="${TOOLCHAIN_DIR}/bin/docker-compose"
if [[ -x "${target}" ]]; then
  echo "[OK] ${target} already present"
  exit 0
fi

base="https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
mkdir -p "${TOOLCHAIN_DIR}/bin"
curl -fsSL "${base}/${asset}" -o "${tmp}/${asset}"
curl -fsSL "${base}/${asset}.sha256" -o "${tmp}/${asset}.sha256"
(cd "${tmp}" && sha256sum -c "${asset}.sha256")
install -m 0755 "${tmp}/${asset}" "${target}"
echo "[OK] installed ${target}"
