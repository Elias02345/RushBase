# Worker AI stages (milestone M4, worker side)

Everything runs on the worker machine (ADR-0012): llama.cpp for the vision-language model,
whisper.cpp for speech, ONNX Runtime for embeddings and audio events. No PyTorch at runtime.
Model choices follow ADR-0011 and are data (`worker/src/rushbase_worker/profiles.toml`,
`worker/models.lock.json`).

## Hardware, backend and profile

`rushbase hardware [--json]` probes NVIDIA (NVML via the optional `nvidia-ml-py`, else
`nvidia-smi`), AMD (`rocm-smi`, sysfs), Intel (sysfs), Apple Silicon (`sysctl`), Vulkan devices
(`vulkaninfo --summary`), AVX2/AVX512 and RAM. The **free** VRAM decides the profile:

| Profile | Free memory | VLM (default) | Fallback chain |
| --- | --- | --- | --- |
| `xl` | >= 15 GB | Qwen3.5-9B Q8_0 | gpu8-9b, gpu8-4b, ... |
| `gpu8` | >= 6.9 GB | Qwen3.5-9B Q4_K_M (measured, see benchmark) | gpu8-4b (Qwen3.5-4B Q8_0), gpu4-4b |
| `gpu4` | >= 3.4 GB | Qwen3.5-4B Q4_K_M | Qwen3.5-2B |
| `cpu` | otherwise | Qwen3.5-2B Q4_K_M (`cpu-minicpm` = MiniCPM-V 4.6 is selectable) | - |

Backend: NVIDIA -> CUDA (llama.cpp `cuda-12.8` + cudart bundle), AMD -> ROCm or Vulkan, Intel ->
Vulkan, Apple -> Metal, otherwise CPU. Overrides: `RUSHBASE_PROFILE`, `RUSHBASE_BACKEND`,
`RUSHBASE_VISION_VARIANT`. The worker advertises `backends`, `profile` and `vram_mb` when claiming.

## Binaries and models

Data directory: `RUSHBASE_DATA_DIR` (dev: `.toolchain/worker-data`) with `backends/` and `models/`.
`rushbase models list|pull|verify|remove` (progress as JSON lines with `--json`). Downloads resume
via HTTP Range, are verified against the SHA-256 in the lock files and installed atomically
(`.part` file, rename, marker). `rushbase process` never downloads unless `--auto-download`; without
it a missing asset gives an error naming `rushbase models pull`, and stages whose assets are
missing are simply not advertised.

- `worker/binaries.lock.json`: llama.cpp tag `b11681` with per-platform assets, digests from the
  GitHub Releases API (`worker/scripts/update-lock.py binaries`; review the diff). The newest release
  carrying all wanted assets is chosen; current CUDA naming is `ubuntu-cuda-12.8` / `cuda-13.4`
  (ADR numbers were checked, not trusted).
- `worker/models.lock.json`: Hugging Face repo, **commit sha**, file, size and LFS SHA-256
  (`update-lock.py models`).
- whisper.cpp has no Linux GPU releases: `.github/workflows/whisper-build.yml` (manual or on a
  `whisper-*` tag) builds v1.9.5 for linux cpu/vulkan/cuda, macOS Metal and Windows
  cpu/vulkan/cuda and publishes them as release assets with SHA256SUMS. Locally,
  `worker/scripts/build-whisper-docker.sh cuda <dir>` builds in a throw-away container
  (`rushbase-m4-build`, `nvidia/cuda:12.8.1-devel-ubuntu24.04`, arch sm_86 only for the dev run,
  NCCL off) - nothing is installed on the host.
- **Pinned release builds**: `worker/binaries.lock.json` (`whisper.cpp.platforms`, keys
  `<os>-<arch>-<backend>`, filled by `update-lock.py binaries --whisper-repo ... --whisper-tag
  whisper-v1.9.5`) lists the CI release assets with SHA-256. `rushbase models pull` installs the
  first entry that fits the detected hardware: cuda, then vulkan, then cpu on Linux/Windows,
  metal on macOS arm64 (`backends/whisper.cpp-<tag>-<key>`, verified before unpacking). Only
  when the lock has no entry for the machine does it install the upstream CPU build (`b5454`,
  Linux x64). `find_whisper` prefers release builds over that fallback.
- **CUDA runtime**: the CUDA whisper builds ship no CUDA libraries. The worker puts the installed
  llama.cpp build directory (which holds the cudart bundle) on `LD_LIBRARY_PATH` (`PATH` on
  Windows) for the `whisper-cli` subprocess, so `models pull` installs llama.cpp first.

## GPU scheduling

`ModelLifecycle` grants one GPU-resident resource at a time (`vlm`, `whisper`). Handlers of the same
resource share it; a request for the other one waits for the users to finish and unloads the
resident one (llama-server is stopped). Because the server claims stage-batched, switches are rare;
the runner also unloads when the queue is empty. llama-server: loopback only, port 18090-18099,
logs in `<data_dir>/logs`. At start the loader checks free VRAM against `min_free_mb` of the
variant and falls back to the next smaller variant on OOM; the used model is in `ModelRef`.

## Stages

- **audio**: ffmpeg -> 16 kHz mono; CED (`ced-small`/`ced-mini`) over 2 s windows (0.5 s hop), grouped
  by `audio/tags.toml` (AudioSet display names -> `speech, music, applause, wind, gunshot,
  explosion, silence`), per-tag thresholds (`RUSHBASE_EVENT_THRESHOLDS`), 2 consecutive hits,
  gap merge 1 s, raw top-3 kept, silence also by level. The CED ONNX already outputs sigmoid
  probabilities. Whisper (`-l auto`, JSON with word times, Silero VAD when installed) runs only
  with >= 1 s of speech. EBU R128 via ffmpeg `ebur128`. Pure audio: audio type from the event
  distribution, BPM by onset autocorrelation. **Key detection is not implemented**: chroma key
  estimates are unreliable enough that an empty field is better than a wrong one.
- **embed**: SigLIP 2 vision tower (fp16) on keyframes and photo previews, L2-normalised. Golden
  test against `transformers` (`tests/fixtures/ai/make-siglip-fixture.sh` runs it in a throw-away
  Docker container): preprocessing identical, cosine 0.99999 (fp16). The int8 exports reach only
  0.95 (vision) and 0.88-0.97 (text), so fp16 is used.
- **vision**: per shot up to 3 keyframes plus timestamps in one request; photos single image. Flat
  strict JSON schema (`Estimated` split into value + `_confidence`, taxonomy fields `tax_<key>` and
  flags `flag_<key>` as enums from the snapshot), versioned prompts
  (`vision/prompts/v1_*.md`), pydantic validation, `finish_reason=length` is a failure, one retry at
  temperature 0, summary from shot descriptions in a short text call (copied for single shots).
- **classify** (CPU): e5 `passage:` vectors of summary, shot descriptions (de/en) and ~30 s
  transcript chunks; terms mapped to approved values by exact, synonym, then embedding similarity
  (`RUSHBASE_TAXONOMY_MATCH_THRESHOLD`); unknown terms recurring in >= 2 shots become proposals;
  review reasons for low confidence per field (`RUSHBASE_REVIEW_FIELD_CONFIDENCE`), true check
  flags and VLM-vs-SigLIP zero-shot conflicts (shot size, interior/exterior, using the embed result).

## Taxonomy snapshot

`GET /api/v1/worker/taxonomy` -> `{version, fields[{key,label_de,label_en,kind,values[{value,
synonyms,description}]}], flags[{key,description_de,description_en}]}` (`rushbase_worker.taxonomy`).
The server does not serve it yet: on 404 the client logs once and uses an empty snapshot
(version 0). `rushbase analyze --taxonomy file.json` uses a local file.

## Benchmarks

`rushbase bench vision|whisper|embed`; results and the `gpu8` recommendation:
`docs/eval/2026-10-vlm-benchmark.md`.

## Tests

Unit tests use fakes. Real-model tests are marked `models` (and `gpu`) and skip unless the models
are installed (`RUSHBASE_DATA_DIR=... pytest worker/tests/test_ai_models.py`; the Whisper test also
needs `RUSHBASE_TEST_SPEECH_WAV`).
