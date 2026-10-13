# ADR-0008: Prebuilt inference binaries, pinning and verification

- Status: Accepted (amended by ADR-0011)
- Date: 2026-10-13

## Context
llama.cpp publishes release assets for every needed backend (tag b11580 on 2026-10-13): Ubuntu x64 CPU, Vulkan,
CUDA 12.8/13.4 (+ `cudart-llama-*` runtime bundles), ROCm 10.0, SYCL, OpenVINO; macOS arm64 (Metal); Windows
CPU/Vulkan/CUDA/ROCm/SYCL. Every asset has a `sha256` digest in the GitHub API. whisper.cpp tagged releases
(v1.9.5) have no assets; rolling `bNNNN` prereleases ship only CPU builds for Linux. Both projects are MIT.

## Decision
1. llama.cpp: download upstream assets, never re-host. Selection: NVIDIA → `cuda-12.8` (+ cudart bundle if no
   toolkit), AMD → `rocm-10.0` then `vulkan`, Intel → `vulkan` (SYCL optional), Apple → `macos-arm64`, else CPU.
2. `worker/binaries.lock.json` pins tag, asset names and SHA256. Downloads are verified against the lock file
   (not the live API) and abort on mismatch. A script regenerates the lock from the API; changes are reviewed.
3. whisper.cpp: built from source tag **v1.9.5** in Rushbase CI (Linux CPU/Vulkan/CUDA, macOS Metal, Windows),
   published as Rushbase release assets with SHA256 and pinned the same way.
4. Whisper models: `ggml-large-v3-turbo-q5_0.bin` (GPU), `ggml-small-q8_0.bin` (CPU), pinned by SHA256.
5. Everything lives in a per-user data directory (`~/.local/share/rushbase` or the platform equivalent,
   overridable), never in system paths.

## Consequences
Small installers, no compiler on user machines; a small CI job for whisper.cpp; CUDA bundles are large and
downloads must be resumable.

## Sources
- https://api.github.com/repos/ggml-org/llama.cpp/releases
- https://api.github.com/repos/ggml-org/whisper.cpp/releases
- https://huggingface.co/ggerganov/whisper.cpp
