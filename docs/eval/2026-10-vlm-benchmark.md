# Benchmark on the reference machine (2026-10-16)

Decides the open question of ADR-0011: the default VLM of the `gpu8` profile.

## Setup

- RTX 3070 8 GB (driver 615.71, CUDA 13.4 capable; the 12.8 llama.cpp build is used), Ryzen 7 5800X
  (16 threads), 32 GB RAM, CachyOS. The desktop uses ~0.9 GB VRAM (baseline below), 7.3 GB free.
- llama.cpp b11681 `ubuntu-cuda-12.8`, flags `--jinja -np 1 -fa on -ngl 99 -c 8192 -ctk q8_0
  -ctv q8_0 --image-min-tokens 128 --image-max-tokens 512`, thinking disabled for Qwen3.5.
- Workload: 24 items (12 video shots with 3 keyframes each, 12 loose frames sent as single
  images) x 2 passes = 48 calls per variant, strict JSON schema with a sample taxonomy (2 fields,
  1 flag), prompt v1. Command:
  `rushbase bench vision --frames <dir> --profiles gpu8-4b,gpu8-9b,baseline-qwen3vl8b`.
- **The test media are synthetic** (colour bars, fractals, tones). Latency, memory and JSON validity
  are meaningful; the quality of descriptions is **not** judged here. A golden-set A/B (shot size,
  angle, people count, OCR, German quality) on real footage is still required before the default
  is declared final.

## Results (vision)

| Variant | Model | Peak VRAM | Headroom (of 8192) | Median / p95 per call | Tokens/s | JSON valid first try | Valid after retry |
| --- | --- | --- | --- | --- | --- | --- | --- |
| gpu8-4b | Qwen3.5-4B Q8_0 + mmproj F16 | 6404 MiB | 1788 MiB | 10.5 s / 12.8 s | 66.6 | 48/48 | 48/48 |
| gpu8-9b | Qwen3.5-9B Q4_K_M + mmproj F16 | 7256 MiB | 936 MiB | 11.2 s / 14.0 s | 60.0 | 47/48 | 48/48 |
| baseline-qwen3vl8b | Qwen3-VL-8B Q4_K_M + mmproj Q8_0 | 7317 MiB | 875 MiB | 9.0 s / 10.4 s | 62.6 | 48/48 | 48/48 |

Peak VRAM is the maximum of `nvidia-smi` samples (every 0.4 s) over load plus all calls, including
the ~917 MiB the desktop already used. Median prompt size was ~1560 tokens (3 images + prompt),
median output 550-680 tokens. Model load took 2-4 s (files in the page cache).

## Recommendation

- **9B Q4_K_M fits stably with three images** on this card (936 MiB headroom, no OOM in 48 calls,
  one invalid answer fixed by the retry) and is only ~7 % slower than 4B Q8_0. `gpu8` therefore
  defaults to `gpu8-9b`; the loader checks free VRAM (`min_free_mb` 7000) and falls back to
  `gpu8-4b` on low memory or OOM, recording the model in every result. If a browser or another
  program takes more than ~0.9 GB, the fallback triggers by design.
- Qwen3-VL-8B stays the regression baseline: fastest here but with the same footprint and older.
- Confidence values: all models answer most `*_confidence` fields with 1.0 despite the prompt, so
  VLM confidences are weak signals. The SigLIP cross-check and review thresholds compensate;
  calibration needs the golden set.

## Results (Whisper, 66 s of speech, public-domain JFK sample tiled 6 times)

| Model | Device | Run time | Realtime factor | Extra VRAM |
| --- | --- | --- | --- | --- |
| large-v3-turbo q5_0 | CUDA (RTX 3070) | 1.8 s | 0.027 | ~1.0 GB |
| large-v3-turbo q5_0 | CPU (8 threads) | 35.4 s | 0.54 | - |
| small q8_0 | CPU (8 threads) | 7.0 s | 0.11 | - |

The GPU run is 20x faster than the CPU run of the same model. The whisper.cpp binary is the v1.9.5
CUDA build made in Docker (see `docs/worker-ai.md`).

## Results (embeddings and events, CPU)

| Model | Latency |
| --- | --- |
| SigLIP 2 vision fp16 | 104 ms / image (cosine 0.9999 to the reference) |
| SigLIP 2 vision int8 | 75 ms / image (cosine 0.95: not used) |
| SigLIP 2 text fp16 | 28 ms / text (the int8 export has cosine 0.88-0.97: not used) |
| multilingual-e5-base (quantized) | 9 ms / text (~40 tokens) |
| CED-small | 0.95 s per 60 s of audio (2 s windows, 0.5 s hop) |

ADR-0007's expectation "cosine >= 0.97 for int8 towers" did not hold; fp16 is used everywhere.
