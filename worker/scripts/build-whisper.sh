#!/usr/bin/env bash
# Build whisper.cpp from a pinned tag for one backend (cpu|vulkan|cuda|metal) on Linux/macOS.
#   build-whisper.sh <backend> <output-dir> [tag]
# Runs on a CI runner or inside the container started by build-whisper-docker.sh. Output:
#   <output-dir>/whisper-cli, <output-dir>/BUILD_INFO.json (all ggml code linked statically).
set -Eeuo pipefail

BACKEND="${1:?backend: cpu|vulkan|cuda|metal}"
OUT="${2:?output directory}"
TAG="${3:-v1.9.5}"
CUDA_ARCHS="${CUDA_ARCHS:-75;80;86;89}"
JOBS="${JOBS:-$(nproc 2>/dev/null || sysctl -n hw.ncpu)}"

WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT
mkdir -p "${OUT}"

curl -fsSL "https://github.com/ggml-org/whisper.cpp/archive/refs/tags/${TAG}.tar.gz" -o "${WORK}/src.tgz"
mkdir "${WORK}/src"
tar -xzf "${WORK}/src.tgz" -C "${WORK}/src" --strip-components=1

flags=(-DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF -DWHISPER_BUILD_TESTS=OFF
  -DWHISPER_BUILD_SERVER=OFF -DGGML_NATIVE=OFF)
case "${BACKEND}" in
  cpu) ;;
  vulkan) flags+=(-DGGML_VULKAN=ON) ;;
  cuda) flags+=(-DGGML_CUDA=ON "-DCMAKE_CUDA_ARCHITECTURES=${CUDA_ARCHS}") ;;
  metal) flags+=(-DGGML_METAL=ON -DGGML_METAL_EMBED_LIBRARY=ON) ;;
  *) echo "unknown backend ${BACKEND}" >&2; exit 2 ;;
esac
if [[ "${BACKEND}" != "metal" ]]; then
  # Static builds have no runtime CPU dispatch: AVX2 + FMA + F16C is the baseline of every
  # supported desktop CPU.
  flags+=(-DGGML_AVX2=ON -DGGML_FMA=ON -DGGML_F16C=ON)
fi

cmake -S "${WORK}/src" -B "${WORK}/build" "${flags[@]}"
cmake --build "${WORK}/build" --config Release -j "${JOBS}" --target whisper-cli

install -m 0755 "$(find "${WORK}/build" -name whisper-cli -type f | head -n1)" "${OUT}/whisper-cli"
printf '{"project":"whisper.cpp","tag":"%s","backend":"%s","cuda_archs":"%s"}\n' \
  "${TAG}" "${BACKEND}" "${CUDA_ARCHS}" >"${OUT}/BUILD_INFO.json"
(cd "${OUT}" && { sha256sum whisper-cli 2>/dev/null || shasum -a 256 whisper-cli; }) >"${OUT}/whisper-cli.sha256"
echo "built ${OUT}/whisper-cli"
