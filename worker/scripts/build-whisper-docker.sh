#!/usr/bin/env bash
# Build whisper.cpp inside a throw-away container (never installs toolkits on the host):
#   build-whisper-docker.sh <cuda|vulkan|cpu> <output-dir> [tag]
set -Eeuo pipefail

BACKEND="${1:?backend: cuda|vulkan|cpu}"
mkdir -p "${2:?output directory}"
OUT="$(cd "$2" && pwd)"
TAG="${3:-v1.9.5}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE="${WHISPER_BUILD_IMAGE:-nvidia/cuda:12.8.1-devel-ubuntu24.04}"
NAME="rushbase-m4-build"

docker rm -f "${NAME}" >/dev/null 2>&1 || true
trap 'docker rm -f "${NAME}" >/dev/null 2>&1 || true' EXIT
docker run --name "${NAME}" -e DEBIAN_FRONTEND=noninteractive \
  -e CUDA_ARCHS="${CUDA_ARCHS:-75;80;86;89}" \
  -v "${SCRIPT_DIR}/build-whisper.sh:/build-whisper.sh:ro" -v "${OUT}:/out" "${IMAGE}" bash -c "
    set -Eeuo pipefail
    apt-get update -qq
    apt-get install -y -qq --no-install-recommends cmake curl ca-certificates build-essential \
      pkg-config libvulkan-dev glslc glslang-tools >/dev/null
    bash /build-whisper.sh '${BACKEND}' /out '${TAG}'
    chown -R $(id -u):$(id -g) /out"
